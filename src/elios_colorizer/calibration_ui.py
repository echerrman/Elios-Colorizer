"""Desktop workbench for refining RGB-to-LAS calibration."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from PySide6.QtCore import QRegularExpression, QTimer, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QSlider, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from .calibration_workbench import (
    CalibrationAdjustments, CalibrationReferenceRequest, CalibrationSession,
    save_refined_calibration,
)
from .camera import CalibrationError
from .calibration_viewport import CalibrationViewport


def parse_reference_time(text: str) -> float:
    """Parse a user-facing M:SS or MM:SS timestamp."""
    parts = text.strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("Enter each time as minutes:seconds, for example 2:34")
    minutes, seconds = (int(part) for part in parts)
    if seconds >= 60:
        raise ValueError("Seconds must be between 00 and 59")
    return float(minutes * 60 + seconds)


class CalibrationReferenceDialog(QDialog):
    """Choose automatic references or explicit flight/timestamp pairs."""

    def __init__(self, flights, parent=None, *, geometry_path: Path | None = None,
                 source_to_geometry=None):
        super().__init__(parent)
        self.flights = [flight for flight in flights if flight.folder]
        self.geometry_path = geometry_path
        self.source_to_geometry = list(source_to_geometry or [None] * len(self.flights))
        self._manual_rows: list[tuple[QComboBox, QLineEdit]] = []
        self.setWindowTitle("Choose calibration reference views")
        self.setMinimumWidth(650)
        root = QVBoxLayout(self)
        title = QLabel("Choose reference views", objectName="dialogTitle")
        root.addWidget(title)
        explanation = QLabel(
            "Reference views only control what you inspect in the workbench. They do not alter flight data. "
            "Use manual times when you know a frame contains clear railings, wall edges, pipes, or door frames.")
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        self.automatic = QRadioButton("Use automatic reference views")
        self.automatic.setChecked(True)
        root.addWidget(self.automatic)
        automatic_row = QHBoxLayout()
        automatic_row.addSpacing(24)
        automatic_row.addWidget(QLabel("Flight"))
        self.automatic_flight = self._flight_combo()
        automatic_row.addWidget(self.automatic_flight, 1)
        automatic_row.addWidget(QLabel("3 views at 15%, 50%, and 85%"))
        root.addLayout(automatic_row)

        self.manual = QRadioButton("Choose flight timestamps manually")
        root.addWidget(self.manual)
        manual_group = QGroupBox("Manual reference views")
        manual_box = QVBoxLayout(manual_group)
        hint = QLabel("Enter one to three timestamps as M:SS. Blank rows are ignored.")
        hint.setWordWrap(True)
        manual_box.addWidget(hint)
        validator = QRegularExpressionValidator(QRegularExpression(r"\d{0,4}:?\d{0,2}"), self)
        for index in range(3):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"View {index + 1}"))
            flight_combo = self._flight_combo()
            row.addWidget(flight_combo, 1)
            timestamp = QLineEdit()
            timestamp.setPlaceholderText("M:SS")
            timestamp.setMaximumWidth(100)
            timestamp.setValidator(validator)
            row.addWidget(QLabel("Time"))
            row.addWidget(timestamp)
            manual_box.addLayout(row)
            self._manual_rows.append((flight_combo, timestamp))
        root.addWidget(manual_group)
        self.automatic.toggled.connect(manual_group.setDisabled)
        manual_group.setDisabled(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Open workbench")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._validate_and_accept)
        root.addWidget(buttons)

    def _flight_combo(self) -> QComboBox:
        combo = QComboBox()
        for index, flight in enumerate(self.flights, 1):
            name = flight.name.strip() if flight.name else ""
            combo.addItem(name or Path(flight.folder).name or f"Flight {index}", index - 1)
        return combo

    def _validate_and_accept(self) -> None:
        if self.automatic.isChecked():
            self.accept()
            return
        try:
            requests = self.manual_requests()
        except ValueError as exc:
            QMessageBox.warning(self, "Check reference times", str(exc))
            return
        if not requests:
            QMessageBox.warning(self, "Choose a reference", "Enter at least one manual timestamp.")
            return
        self.accept()

    def automatic_flight_selection(self):
        return self.flights[self.automatic_flight.currentData()]

    def automatic_flight_index(self) -> int:
        return int(self.automatic_flight.currentData())

    def manual_requests(self) -> list[CalibrationReferenceRequest]:
        requests = []
        for combo, timestamp in self._manual_rows:
            text = timestamp.text().strip()
            if not text:
                continue
            flight = self.flights[combo.currentData()]
            flight_index = combo.currentData()
            requests.append(CalibrationReferenceRequest(
                flight_name=combo.currentText(), folder=Path(flight.folder),
                las_override=Path(flight.las_override) if flight.las_override else None,
                elapsed_s=parse_reference_time(text), geometry_path=self.geometry_path,
                source_to_geometry=self.source_to_geometry[flight_index]))
        return requests


class CalibrationWorkbenchDialog(QDialog):
    """Review synchronized video/LAS alignment and save a derived profile."""

    def __init__(self, session: CalibrationSession, parent=None):
        super().__init__(parent)
        self.session = session
        self.working_base = session.base_calibration
        self.saved_path: Path | None = None
        self.reviewed: set[int] = set()
        self.last_score = 0.0
        self._controls: dict[str, QDoubleSpinBox] = {}
        self._reset_buttons: dict[str, QToolButton] = {}
        self.setObjectName("calibrationWorkbench")
        self.setWindowTitle("Calibration Workbench")
        self.resize(1420, 860)
        self.setMinimumSize(1040, 680)
        self._build()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(90)
        self._refresh_timer.timeout.connect(self._render)
        self._render()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        hero = QFrame(objectName="dialogHero")
        hero_box = QVBoxLayout(hero)
        title = QLabel("Calibration Workbench", objectName="dialogTitle")
        subtitle = QLabel(
            "Align projected LAS structure with synchronized video. The selected base profile remains unchanged; "
            "Save creates a separate profile with an embedded fallback.", objectName="dialogSubtitle")
        subtitle.setWordWrap(True)
        hero_box.addWidget(title)
        hero_box.addWidget(subtitle)
        root.addWidget(hero)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        preview_panel = QFrame(objectName="dialogSection")
        preview_box = QVBoxLayout(preview_panel)
        reference_row = QHBoxLayout()
        reference_row.addWidget(QLabel("Reference view"))
        self.reference_combo = QComboBox()
        for index in range(len(self.session.reference_elapsed_s)):
            self.reference_combo.addItem(self.session.label_for(index), index)
        self.reference_combo.currentIndexChanged.connect(self._reference_changed)
        reference_row.addWidget(self.reference_combo, 1)
        previous = QPushButton("← Previous")
        next_button = QPushButton("Next →")
        previous.clicked.connect(lambda: self.reference_combo.setCurrentIndex(
            max(0, self.reference_combo.currentIndex() - 1)))
        next_button.clicked.connect(lambda: self.reference_combo.setCurrentIndex(
            min(self.reference_combo.count() - 1, self.reference_combo.currentIndex() + 1)))
        reference_row.addWidget(previous)
        reference_row.addWidget(next_button)
        preview_box.addLayout(reference_row)
        guide = QLabel(
            "Line up the same physical geometry in both layers. For example, make a shaded point-cloud railing sit "
            "directly on the video railing. Prefer rigid edges and depth changes; ignore glare, paint, shadows, and "
            "wall texture. Zoom and pan affect both layers together and do not change the saved calibration.")
        guide.setWordWrap(True)
        guide.setObjectName("dialogSectionNote")
        preview_box.addWidget(guide)
        self.cloud_source_label = QLabel()
        self.cloud_source_label.setObjectName("dialogSectionNote")
        preview_box.addWidget(self.cloud_source_label)
        self.preview = CalibrationViewport()
        self.preview.rendered.connect(self._viewport_rendered)
        preview_box.addWidget(self.preview, 1)
        self.overlay_status = QLabel()
        self.overlay_status.setWordWrap(True)
        preview_box.addWidget(self.overlay_status)
        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("View"))
        self.display_mode = QComboBox()
        for label, value in (("Composite", "composite"),
                             ("3D Inspector", "context"),
                             ("Edge Diagnostic", "diagnostic")):
            self.display_mode.addItem(label, value)
        self.display_mode.currentIndexChanged.connect(self._display_mode_changed)
        display_row.addWidget(self.display_mode)
        display_row.addWidget(QLabel("Cloud color"))
        self.cloud_color = QComboBox()
        self.cloud_color.addItem("Elevation", "elevation")
        self.cloud_color.addItem("Neutral", "neutral")
        self.cloud_color.currentIndexChanged.connect(
            lambda: self.preview.set_color_mode(self.cloud_color.currentData()))
        display_row.addWidget(self.cloud_color)
        reset_view = QPushButton("Reset view")
        reset_view.clicked.connect(self.preview.reset_view)
        display_row.addWidget(reset_view)
        preview_box.addLayout(display_row)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Video opacity"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(55)
        self.opacity.valueChanged.connect(self.preview.set_video_opacity)
        opacity_row.addWidget(self.opacity, 1)
        self.opacity_value = QLabel("55%")
        self.opacity.valueChanged.connect(lambda value: self.opacity_value.setText(f"{value}%"))
        opacity_row.addWidget(self.opacity_value)
        preview_box.addLayout(opacity_row)

        detail_row = QHBoxLayout()
        detail_row.addWidget(QLabel("Point size"))
        self.point_size = QSlider(Qt.Orientation.Horizontal)
        self.point_size.setRange(1, 10)
        self.point_size.setValue(4)
        self.point_size.valueChanged.connect(self.preview.set_point_size)
        detail_row.addWidget(self.point_size, 1)
        self.point_size_value = QLabel("4 px")
        self.point_size.valueChanged.connect(
            lambda value: self.point_size_value.setText(f"{value} px"))
        detail_row.addWidget(self.point_size_value)
        detail_row.addWidget(QLabel("Density"))
        self.density = QComboBox()
        for label, value in (("Low", 75_000), ("Medium", 200_000),
                             ("High", 500_000), ("Maximum", 1_000_000)):
            self.density.addItem(f"{label} ({value // 1000}k)", value)
        self.density.setCurrentIndex(2)
        self.density.currentIndexChanged.connect(
            lambda: self.preview.set_density(self.density.currentData()))
        detail_row.addWidget(self.density)
        detail_row.addWidget(QLabel("Depth shading"))
        self.edl = QSlider(Qt.Orientation.Horizontal)
        self.edl.setRange(0, 250)
        self.edl.setValue(120)
        self.edl.setMaximumWidth(150)
        self.edl.valueChanged.connect(self.preview.set_edl_strength)
        detail_row.addWidget(self.edl)
        preview_box.addLayout(detail_row)
        splitter.addWidget(preview_panel)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setMinimumWidth(390)
        controls_scroll.setMaximumWidth(470)
        controls_page = QWidget()
        controls_box = QVBoxLayout(controls_page)
        controls_box.setContentsMargins(8, 4, 8, 4)
        controls_box.setSpacing(10)
        self.profile_name = QLineEdit(f"{self.working_base.profile_name} - Workbench refined")
        profile_group = QGroupBox("Derived profile")
        profile_form = QFormLayout(profile_group)
        profile_form.addRow("Profile name", self.profile_name)
        self.base_label = QLabel(self.working_base.profile_name)
        self.base_label.setWordWrap(True)
        profile_form.addRow("Fallback base", self.base_label)
        controls_box.addWidget(profile_group)

        timing = QGroupBox("Timing")
        timing_form = QFormLayout(timing)
        timing_form.addRow("Video → telemetry", self._spin(
            "timing_offset_s", -1.0, 1.0, .005, 3, " s"))
        controls_box.addWidget(timing)

        image = QGroupBox("Image alignment")
        image_form = QFormLayout(image)
        image_form.addRow("Focal scale", self._spin("focal_scale", .90, 1.10, .001, 4, "×", 1.0))
        image_form.addRow("Principal X", self._spin("principal_x_px", -250, 250, .5, 1, " px"))
        image_form.addRow("Principal Y", self._spin("principal_y_px", -250, 250, .5, 1, " px"))
        controls_box.addWidget(image)

        mount = QGroupBox("Camera mount")
        mount_form = QFormLayout(mount)
        mount_form.addRow("Roll", self._spin("mount_roll_degrees", -5, 5, .01, 2, "°"))
        mount_form.addRow("Pitch", self._spin("mount_pitch_degrees", -5, 5, .01, 2, "°"))
        mount_form.addRow("Yaw", self._spin("mount_yaw_degrees", -5, 5, .01, 2, "°"))
        mount_form.addRow("Translate X", self._spin("translation_x_m", -.15, .15, .001, 3, " m"))
        mount_form.addRow("Translate Y", self._spin("translation_y_m", -.15, .15, .001, 3, " m"))
        mount_form.addRow("Translate Z", self._spin("translation_z_m", -.15, .15, .001, 3, " m"))
        mount_form.addRow("Tilt zero", self._spin("tilt_zero_degrees", -5, 5, .01, 2, "°"))
        controls_box.addWidget(mount)

        note = QLabel(
            "Review several views before saving. Use timing for motion-dependent displacement, principal point for a "
            "nearly constant image shift, focal scale for center-to-edge growth, and mount angles for directional error.")
        note.setWordWrap(True)
        note.setObjectName("dialogSectionNote")
        controls_box.addWidget(note)
        action_row = QHBoxLayout()
        reset = QPushButton("Reset adjustments")
        reset.clicked.connect(self._reset_adjustments)
        action_row.addWidget(reset)
        self.fallback_button = QPushButton("Use original fallback")
        self.fallback_button.setVisible(self.working_base.schema_version == 2)
        self.fallback_button.clicked.connect(self._use_fallback)
        action_row.addWidget(self.fallback_button)
        controls_box.addLayout(action_row)
        controls_box.addStretch(1)
        controls_scroll.setWidget(controls_page)
        splitter.addWidget(controls_scroll)
        splitter.setStretchFactor(0, 1)
        root.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.review_status = QLabel()
        footer.addWidget(self.review_status, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = QPushButton("Save profile and use", objectName="primaryDialogButton")
        save.clicked.connect(self._save)
        footer.addWidget(save)
        root.addLayout(footer)
        self._update_review_status()

    def _spin(self, name: str, minimum: float, maximum: float, step: float,
              decimals: int, suffix: str, value: float = 0.0) -> QWidget:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        spin.setAccelerated(True)
        spin.valueChanged.connect(self._schedule_render)
        self._controls[name] = spin
        reset = QToolButton()
        reset.setText("↺")
        reset.setToolTip(f"Reset {name.replace('_', ' ')}")
        reset.setAccessibleName(f"Reset {name.replace('_', ' ')}")
        reset.setAutoRaise(True)
        reset.setFixedSize(30, 30)
        reset.clicked.connect(lambda _checked=False, target=spin, default=value:
                              target.setValue(default))
        self._reset_buttons[name] = reset
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        row.addWidget(spin, 1)
        row.addWidget(reset)
        return holder

    def adjustments(self) -> CalibrationAdjustments:
        return CalibrationAdjustments(**{item.name: self._controls[item.name].value()
                                         for item in fields(CalibrationAdjustments)})

    def _schedule_render(self, *_):
        self._refresh_timer.start()

    def _reference_changed(self, *_):
        self.preview.reset_view()
        self._render()

    def _display_mode_changed(self, *_):
        mode = self.display_mode.currentData()
        if mode == "context":
            self.preview.reset_view()
        self.preview.set_display_mode(mode)

    def _render(self) -> None:
        try:
            calibration = self.adjustments().apply(self.working_base)
            index = self.reference_combo.currentIndex()
            frame = self.session.frame_for(index, calibration)
            self.cloud_source_label.setText(
                f"Geometry: {self.session.cloud_label_for(index)} · temporary viewing sample: "
                f"up to {len(self.session.xyz_for(index)):,} points")
            self.preview.set_scene(frame, self.session.xyz_for(index), calibration)
            self.reviewed.add(index)
            self._update_review_status()
        except Exception as exc:
            self.overlay_status.setText(str(exc))

    def _viewport_rendered(self, result) -> None:
        self.last_score = float(result.alignment_score)
        if hasattr(result, "sampled_points"):
            self.overlay_status.setText(
                f"{result.sampled_points:,} sampled points · {result.visible_points:,} visible splats · "
                f"edge agreement {result.alignment_score:.1%}. Match physical shapes visually; the score is only a guide.")
        else:
            self.overlay_status.setText(
                f"Diagnostic markers: {result.guide_points:,} · {result.matched_guide_points:,} near an image edge · "
                f"edge agreement {result.alignment_score:.1%}.")

    def _update_review_status(self) -> None:
        count = len(self.reviewed)
        total = self.reference_combo.count()
        self.review_status.setText(
            f"Reviewed {count} of {total} reference views"
            + (" · Review every view before relying on this profile." if count < total else " · Multi-view review complete."))

    def _reset_adjustments(self) -> None:
        for item in fields(CalibrationAdjustments):
            self._controls[item.name].setValue(1.0 if item.name == "focal_scale" else 0.0)
        self._render()

    def _use_fallback(self) -> None:
        self.working_base = self.session.base_calibration.fallback()
        self.base_label.setText(self.working_base.profile_name)
        self.profile_name.setText(f"{self.working_base.profile_name} - Workbench refined")
        self.fallback_button.hide()
        self._reset_adjustments()

    def _save(self) -> None:
        if len(self.reviewed) < self.reference_combo.count():
            response = QMessageBox.question(
                self, "Save before reviewing every view?",
                "A correction that matches one view can be wrong elsewhere. Save this profile anyway?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if response != QMessageBox.StandardButton.Save:
                return
        stem = self.session.base_path.stem
        default = self.session.base_path.with_name(f"{stem}-refined.json")
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save refined calibration profile", str(default), "Camera profile (*.json)")
        if not destination:
            return
        target = Path(destination)
        if target.exists():
            response = QMessageBox.question(
                self, "Replace refined profile?", f"Replace {target.name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if response != QMessageBox.StandardButton.Yes:
                return
        try:
            refined = self.adjustments().apply(
                self.working_base, profile_name=self.profile_name.text().strip() or None,
                reviewed_frames=len(self.reviewed), alignment_score=self.last_score)
            self.saved_path = save_refined_calibration(
                refined, target, base_path=self.session.base_path)
        except (OSError, ValueError, CalibrationError) as exc:
            QMessageBox.warning(self, "Could not save calibration", str(exc))
            return
        self.accept()
