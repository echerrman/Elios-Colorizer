"""Desktop workbench for refining RGB-to-LAS calibration."""
from __future__ import annotations

from dataclasses import fields, replace
import json
from pathlib import Path
import queue
import threading

import numpy as np
from scipy.spatial.transform import Rotation

from PySide6.QtCore import QRegularExpression, QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QHeaderView, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QRadioButton, QScrollArea, QSlider, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from .calibration_workbench import (
    CalibrationAdjustments, CalibrationCorrespondence, CalibrationFitResult,
    CalibrationReferenceRequest, CalibrationSession, correspondence_errors,
    fit_calibration_correspondences, pick_cloud_point, save_refined_calibration,
)
from .camera import Calibration, CalibrationError
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
        self.matches: list[CalibrationCorrespondence] = []
        self._pending_video_xy: tuple[float, float] | None = None
        self._proposal: CalibrationFitResult | None = None
        self._proposal_previous: Calibration | None = None
        self._preview_previous = False
        self._fit_queue: queue.Queue = queue.Queue()
        self._updating_match_table = False
        self.setObjectName("calibrationWorkbench")
        self.setWindowTitle("Calibration Workbench")
        self.setWindowIcon(QApplication.windowIcon())
        self.resize(1420, 860)
        self.setMinimumSize(1040, 680)
        self._build()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(90)
        self._refresh_timer.timeout.connect(self._render)
        self._fit_timer = QTimer(self)
        self._fit_timer.setInterval(100)
        self._fit_timer.timeout.connect(self._poll_fit)
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
        self.preview.image_picked.connect(self._viewport_picked)
        preview_box.addWidget(self.preview, 1)
        self.overlay_status = QLabel()
        self.overlay_status.setWordWrap(True)
        preview_box.addWidget(self.overlay_status)
        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("View"))
        self.display_mode = QComboBox()
        for label, value in (("Composite", "composite"),
                             ("Edge Diagnostic", "diagnostic")):
            self.display_mode.addItem(label, value)
        self.display_mode.currentIndexChanged.connect(self._display_mode_changed)
        display_row.addWidget(self.display_mode)
        display_row.addWidget(QLabel("Cloud color"))
        self.cloud_color = QComboBox()
        self.cloud_color.addItem("Amber", "amber")
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
        self.video_only_button = QPushButton("Hold: video only")
        self.video_only_button.setToolTip(
            "Press and hold to temporarily show the unobstructed video. "
            "Release to restore the opacity slider.")
        self.video_only_button.setAccessibleName(
            "Press and hold for video only; release to restore the cloud overlay")
        self.video_only_button.pressed.connect(
            lambda: self.preview.set_video_opacity(100))
        self.video_only_button.released.connect(
            lambda: self.preview.set_video_opacity(self.opacity.value()))
        opacity_row.addWidget(self.video_only_button)
        preview_box.addLayout(opacity_row)

        detail_row = QHBoxLayout()
        detail_row.addWidget(QLabel("Point size"))
        self.point_size = QSlider(Qt.Orientation.Horizontal)
        self.point_size.setRange(1, 8)
        self.point_size.setValue(2)
        self.point_size.valueChanged.connect(self.preview.set_point_size)
        detail_row.addWidget(self.point_size, 1)
        self.point_size_value = QLabel("2 px")
        self.point_size.valueChanged.connect(
            lambda value: self.point_size_value.setText(f"{value} px"))
        detail_row.addWidget(self.point_size_value)
        detail_row.addWidget(QLabel("Depth shading"))
        self.edl = QSlider(Qt.Orientation.Horizontal)
        self.edl.setRange(0, 250)
        self.edl.setValue(120)
        self.edl.setMaximumWidth(150)
        self.edl.valueChanged.connect(self.preview.set_edl_strength)
        detail_row.addWidget(self.edl)
        detail_row.addWidget(QLabel("Radius"))
        self.edl_radius = QSlider(Qt.Orientation.Horizontal)
        self.edl_radius.setRange(1, 4)
        self.edl_radius.setValue(1)
        self.edl_radius.setMaximumWidth(90)
        self.edl_radius.valueChanged.connect(self.preview.set_edl_radius)
        detail_row.addWidget(self.edl_radius)
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
        self.projection_model = QComboBox()
        self.projection_model.addItem("Automatic / current", "automatic")
        self.projection_model.addItem("Pinhole", "pinhole")
        self.projection_model.addItem("Radial / tangential", "opencv")
        self.projection_model.addItem("Fisheye", "fisheye")
        self.projection_model.currentIndexChanged.connect(self._projection_model_changed)
        profile_form.addRow("Projection", self.projection_model)
        controls_box.addWidget(profile_group)

        self.method_tabs = QTabWidget()
        matching_page = QWidget()
        matching_box = QVBoxLayout(matching_page)
        matching_box.setContentsMargins(6, 8, 6, 6)
        matching_help = QLabel(
            "Match the same rigid feature twice: first in the video, then in the shaded cloud. "
            "Use corners, rail ends, pipe junctions, and depth edges across multiple views.")
        matching_help.setWordWrap(True)
        matching_help.setObjectName("dialogSectionNote")
        matching_box.addWidget(matching_help)
        self.pick_status = QLabel("Add matches near the center and edges of at least two views.")
        self.pick_status.setWordWrap(True)
        matching_box.addWidget(self.pick_status)
        self.add_match_button = QPushButton("＋ Add point match")
        self.add_match_button.clicked.connect(self._begin_match)
        matching_box.addWidget(self.add_match_button)
        self.match_table = QTableWidget(0, 4)
        self.match_table.setHorizontalHeaderLabels(("Use", "View", "Video pixel", "Error"))
        self.match_table.verticalHeader().setVisible(False)
        self.match_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.match_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.match_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.match_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.match_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.match_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.match_table.itemChanged.connect(self._match_item_changed)
        self.match_table.itemSelectionChanged.connect(self._match_selection_changed)
        matching_box.addWidget(self.match_table, 1)
        match_actions = QHBoxLayout()
        self.delete_match_button = QPushButton("Delete selected")
        self.delete_match_button.clicked.connect(self._delete_match)
        match_actions.addWidget(self.delete_match_button)
        repick_match = QPushButton("Repick selected")
        repick_match.clicked.connect(self._repick_match)
        match_actions.addWidget(repick_match)
        clear_matches = QPushButton("Clear all")
        clear_matches.clicked.connect(self._clear_matches)
        match_actions.addWidget(clear_matches)
        matching_box.addLayout(match_actions)
        self.fit_button = QPushButton("Calculate best fit", objectName="primaryDialogButton")
        self.fit_button.clicked.connect(self._start_fit)
        matching_box.addWidget(self.fit_button)
        self.fit_progress = QProgressBar()
        self.fit_progress.setRange(0, 0)
        self.fit_progress.setTextVisible(False)
        self.fit_progress.hide()
        matching_box.addWidget(self.fit_progress)
        self.proposal_group = QGroupBox("Calculated proposal")
        proposal_box = QVBoxLayout(self.proposal_group)
        self.proposal_summary = QLabel()
        self.proposal_summary.setWordWrap(True)
        proposal_box.addWidget(self.proposal_summary)
        proposal_actions = QHBoxLayout()
        accept_proposal = QPushButton("Accept proposal", objectName="primaryDialogButton")
        accept_proposal.clicked.connect(self._accept_proposal)
        proposal_actions.addWidget(accept_proposal)
        discard_proposal = QPushButton("Discard")
        discard_proposal.clicked.connect(self._discard_proposal)
        proposal_actions.addWidget(discard_proposal)
        compare_proposal = QPushButton("Hold: previous")
        compare_proposal.setToolTip("Hold to compare the calibration used before fitting")
        compare_proposal.pressed.connect(self._show_previous_proposal)
        compare_proposal.released.connect(self._show_proposed_calibration)
        proposal_actions.addWidget(compare_proposal)
        proposal_box.addLayout(proposal_actions)
        self.proposal_group.hide()
        matching_box.addWidget(self.proposal_group)
        self.method_tabs.addTab(matching_page, "Point Matching")

        manual_page = QWidget()
        manual_box = QVBoxLayout(manual_page)
        manual_box.setContentsMargins(6, 8, 6, 6)

        timing = QGroupBox("Timing")
        timing_form = QFormLayout(timing)
        timing_form.addRow("Video → telemetry", self._spin(
            "timing_offset_s", -2.0, 2.0, .005, 3, " s"))
        manual_box.addWidget(timing)

        image = QGroupBox("Image alignment")
        image_form = QFormLayout(image)
        image_form.addRow("Focal X", self._spin(
            "focal_scale_x", .80, 1.20, .001, 4, "×", 1.0))
        image_form.addRow("Focal Y", self._spin(
            "focal_scale_y", .80, 1.20, .001, 4, "×", 1.0))
        image_form.addRow("Principal X", self._spin(
            "principal_x_px", -500, 500, .5, 1, " px"))
        image_form.addRow("Principal Y", self._spin(
            "principal_y_px", -500, 500, .5, 1, " px"))
        manual_box.addWidget(image)

        distortion = QGroupBox("Lens distortion")
        distortion_box = QVBoxLayout(distortion)
        distortion_note = QLabel(
            "K1 is the primary center-to-edge curvature control. K2 changes curvature mostly near the outer "
            "edges. P1/P2 correct asymmetric or decentered lens distortion. Start with K1 and use small changes.")
        distortion_note.setWordWrap(True)
        distortion_note.setObjectName("dialogSectionNote")
        self.distortion_note = distortion_note
        distortion_box.addWidget(distortion_note)
        distortion_form = QFormLayout()
        self._distortion_labels = []
        for label, name, step in (
                ("K1 · radial", "distortion_k1", .001),
                ("K2 · outer radial", "distortion_k2", .001),
                ("P1 · tangential", "distortion_p1", .0005),
                ("P2 · tangential", "distortion_p2", .0005)):
            label_widget = QLabel(label)
            self._distortion_labels.append(label_widget)
            distortion_form.addRow(label_widget, self._spin(name, -2.0, 2.0, step, 4, ""))
        distortion_box.addLayout(distortion_form)
        self.distortion_group = distortion
        self._sync_distortion_controls()
        manual_box.addWidget(distortion)

        mount = QGroupBox("Camera mount")
        mount_form = QFormLayout(mount)
        mount_form.addRow("Roll", self._spin(
            "mount_roll_degrees", -15, 15, .01, 2, "°"))
        mount_form.addRow("Pitch", self._spin(
            "mount_pitch_degrees", -15, 15, .01, 2, "°"))
        mount_form.addRow("Yaw", self._spin(
            "mount_yaw_degrees", -15, 15, .01, 2, "°"))
        mount_form.addRow("Translate X", self._spin(
            "translation_x_m", -.30, .30, .001, 3, " m"))
        mount_form.addRow("Translate Y", self._spin(
            "translation_y_m", -.30, .30, .001, 3, " m"))
        mount_form.addRow("Translate Z", self._spin(
            "translation_z_m", -.30, .30, .001, 3, " m"))
        mount_form.addRow("Tilt zero", self._spin(
            "tilt_zero_degrees", -15, 15, .01, 2, "°"))
        manual_box.addWidget(mount)

        note = QLabel(
            "Review several views before saving. Use timing for motion-dependent displacement, principal point for a "
            "nearly constant image shift, Focal X/Y for horizontal/vertical center-to-edge growth, K1/K2 for "
            "curvature, and mount angles for directional error. Wide ranges are provided for diagnosis; use the "
            "smallest correction that holds across every reference view.")
        note.setWordWrap(True)
        note.setObjectName("dialogSectionNote")
        manual_box.addWidget(note)
        action_row = QHBoxLayout()
        reset = QPushButton("Reset adjustments")
        reset.clicked.connect(self._reset_adjustments)
        action_row.addWidget(reset)
        self.fallback_button = QPushButton("Use original fallback")
        self.fallback_button.setVisible(self.working_base.schema_version == 2)
        self.fallback_button.clicked.connect(self._use_fallback)
        action_row.addWidget(self.fallback_button)
        manual_box.addLayout(action_row)
        manual_box.addStretch(1)
        self.method_tabs.addTab(manual_page, "Manual Adjustments")
        controls_box.addWidget(self.method_tabs, 1)
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
        self.save_button = QPushButton("Save profile and use", objectName="primaryDialogButton")
        self.save_button.clicked.connect(self._save)
        footer.addWidget(self.save_button)
        root.addLayout(footer)
        # Enter in a spin box must only commit that edit. QDialog otherwise
        # assigns an auto-default push button (often Previous), causing an
        # unexpected reference-view change after typed calibration values.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self._update_review_status()
        self._load_saved_matches()

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

    def _current_calibration(self, *, profile_name: str | None = None,
                             ignore_proposal: bool = False) -> Calibration:
        if (self._preview_previous and self._proposal_previous is not None
                and not ignore_proposal):
            calibration = self._proposal_previous
            return replace(calibration, profile_name=profile_name or calibration.profile_name)
        if (self._proposal is not None and not ignore_proposal
                and not self._preview_previous):
            calibration = self._proposal.calibration
            return replace(calibration, profile_name=profile_name or calibration.profile_name)
        values = {item.name: self._controls[item.name].value()
                  for item in fields(CalibrationAdjustments)}
        selected = self.projection_model.currentData()
        model = self.working_base.distortion_model if selected == "automatic" else selected
        base = self.working_base
        if model != base.distortion_model:
            base = replace(base, distortion_model=model,
                           distortion_coefficients=(() if model == "pinhole" else (0., 0., 0., 0.)))
        if model in {"pinhole", "fisheye"}:
            model_values = [values[name] for name in (
                "distortion_k1", "distortion_k2", "distortion_p1", "distortion_p2")]
            for name in ("distortion_k1", "distortion_k2", "distortion_p1", "distortion_p2"):
                values[name] = 0.0
            calibration = CalibrationAdjustments(**values).apply(base, profile_name=profile_name)
            if model == "pinhole":
                return replace(calibration, distortion_model="pinhole", distortion_coefficients=())
            coefficients = np.asarray(base.distortion_coefficients or (0., 0., 0., 0.), dtype=float)
            coefficients = np.pad(coefficients[:4], (0, max(0, 4-len(coefficients))))
            return replace(calibration, distortion_model="fisheye",
                           distortion_coefficients=tuple(coefficients+model_values))
        return CalibrationAdjustments(**values).apply(base, profile_name=profile_name)

    def _schedule_render(self, *_):
        self._refresh_timer.start()

    def _reference_changed(self, *_):
        if self._pending_video_xy is not None:
            self._cancel_pending_match("Point match cancelled because the reference view changed.")
        self.preview.reset_view()
        self._render()

    def _display_mode_changed(self, *_):
        mode = self.display_mode.currentData()
        self.preview.set_display_mode(mode)

    def _render(self) -> None:
        try:
            calibration = self._current_calibration()
            index = self.reference_combo.currentIndex()
            frame = self.session.frame_for(index, calibration)
            self.cloud_source_label.setText(
                f"Geometry: {self.session.cloud_label_for(index)} · "
                f"{len(self.session.xyz_for(index)):,} points retained in this buffered reference view")
            self.preview.set_scene(frame, self.session.xyz_for(index), calibration)
            self._update_match_markers(calibration)
            self._refresh_match_table(calibration)
            self.reviewed.add(index)
            self._update_review_status()
        except Exception as exc:
            self.overlay_status.setText(str(exc))

    def _viewport_rendered(self, result) -> None:
        self.last_score = float(result.alignment_score)
        if hasattr(result, "sampled_points"):
            self.overlay_status.setText(
                f"{result.sampled_points:,} retained points · approximately {result.visible_points:,} visible · "
                f"edge agreement {result.alignment_score:.1%}. Full detail is drawn after interaction stops; "
                "match physical shapes visually and treat the score only as a guide.")
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
            self._controls[item.name].setValue(
                1.0 if item.name in ("focal_scale_x", "focal_scale_y") else 0.0)
        self._render()

    def _use_fallback(self) -> None:
        self.working_base = self.session.base_calibration.fallback()
        self.base_label.setText(self.working_base.profile_name)
        self.profile_name.setText(f"{self.working_base.profile_name} - Workbench refined")
        self.fallback_button.hide()
        self.projection_model.blockSignals(True)
        self.projection_model.setCurrentIndex(0)
        self.projection_model.blockSignals(False)
        self._sync_distortion_controls()
        self._reset_adjustments()

    def _sync_distortion_controls(self) -> None:
        self._projection_model_changed(reset_values=False)

    def _projection_model_changed(self, *_args, reset_values: bool = True) -> None:
        if not hasattr(self, "distortion_group"):
            return
        selected = self.projection_model.currentData()
        model = self.working_base.distortion_model if selected == "automatic" else selected
        controls = [self._controls[name] for name in (
            "distortion_k1", "distortion_k2", "distortion_p1", "distortion_p2")]
        if reset_values:
            for control in controls:
                control.blockSignals(True); control.setValue(0.0); control.blockSignals(False)
        self.distortion_group.setEnabled(model != "pinhole")
        if model == "fisheye":
            labels = ("K1 · fisheye", "K2 · fisheye", "K3 · fisheye", "K4 · fisheye")
            for control in controls:
                control.setRange(-1.5, 1.5); control.setSingleStep(.001)
            self.distortion_note.setText(
                "Fisheye K1–K4 control the angular wide-angle projection. These coefficients are not "
                "interchangeable with radial/tangential values.")
        elif model == "opencv":
            labels = ("K1 · radial", "K2 · outer radial", "P1 · tangential", "P2 · tangential")
            controls[0].setRange(-2., 2.); controls[1].setRange(-2., 2.)
            controls[2].setRange(-.25, .25); controls[3].setRange(-.25, .25)
            self.distortion_note.setText(
                "K1 is the primary center-to-edge curvature control. K2 changes the outer edges; "
                "P1/P2 correct asymmetric or decentered distortion.")
        else:
            labels = ("K1", "K2", "P1", "P2")
            self.distortion_note.setText(
                "Pinhole has no lens-distortion coefficients. Choose radial/tangential or fisheye to tune curvature.")
        for widget, label in zip(self._distortion_labels, labels):
            widget.setText(label)
        if hasattr(self, "_refresh_timer"):
            self._schedule_render()

    def _begin_match(self) -> None:
        self._proposal = None
        self._proposal_previous = None
        self._preview_previous = False
        self.proposal_group.hide()
        self.save_button.setEnabled(True)
        self._pending_video_xy = None
        self.display_mode.setCurrentIndex(self.display_mode.findData("composite"))
        self.preview.set_video_opacity(100)
        self.preview.set_pick_mode("video")
        self.pick_status.setText(
            "Step 1 of 2 — click a precise rigid feature in the unobstructed video frame. "
            "Use the mouse wheel to zoom before clicking.")
        self.add_match_button.setText("Cancel point match")
        try:
            self.add_match_button.clicked.disconnect(self._begin_match)
        except RuntimeError:
            pass
        self.add_match_button.clicked.connect(self._cancel_pending_match)

    def _cancel_pending_match(self, message: str | bool = "Point matching cancelled.") -> None:
        if isinstance(message, bool):
            message = "Point matching cancelled."
        self._pending_video_xy = None
        self.preview.set_pick_mode(None)
        self.preview.set_video_opacity(self.opacity.value())
        self.pick_status.setText(message)
        self.add_match_button.setText("＋ Add point match")
        try:
            self.add_match_button.clicked.disconnect(self._cancel_pending_match)
        except RuntimeError:
            pass
        self.add_match_button.clicked.connect(self._begin_match)
        if hasattr(self, "preview"):
            self._update_match_markers(self._current_calibration())

    def _viewport_picked(self, x: float, y: float) -> None:
        if self.preview.pick_mode == "video":
            self._pending_video_xy = (float(x), float(y))
            self.preview.set_pick_mode("cloud")
            self.preview.set_video_opacity(0)
            self._update_match_markers(self._current_calibration())
            self.pick_status.setText(
                "Step 2 of 2 — click the same physical feature in the shaded point cloud. "
                "If it currently projects outside the dashed video frame, wheel down to reveal the buffered "
                "cloud overscan, then click it. Zoom in when features overlap.")
            return
        if self.preview.pick_mode != "cloud" or self._pending_video_xy is None:
            return
        try:
            calibration = self._current_calibration()
            reference = self.reference_combo.currentIndex()
            frame = self.session.frame_for(reference, calibration)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            point = pick_cloud_point(
                self.session.xyz_for(reference), frame, calibration, (x, y),
                radius_px=self._pick_radius_image_px(calibration))
        except Exception as exc:
            self.pick_status.setText(f"Cloud point could not be selected: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        if point is None:
            self.pick_status.setText(
                "No cloud point was found near that click. Zoom in, increase point size, and click visible geometry.")
            return
        self.matches.append(CalibrationCorrespondence(
            reference, self._pending_video_xy, tuple(float(value) for value in point)))
        self._cancel_pending_match(
            f"Match {len(self.matches)} added. Add more across the image and in another reference view.")
        self._refresh_match_table(calibration)
        self._update_match_markers(calibration)

    def _pick_radius_image_px(self, calibration: Calibration) -> float:
        _left, _top, width, _height = self.preview._viewport_rect()
        logical_width = width/max(self.preview.devicePixelRatioF(), 1e-6)
        return max(3., 14.*calibration.image_width
                   / max(logical_width*self.preview.zoom, 1.))

    def _project_match(self, match: CalibrationCorrespondence,
                       calibration: Calibration) -> tuple[float, float] | None:
        frame = self.session.frame_for(match.reference_index, calibration)
        center, camera_to_world = calibration.camera_pose(
            frame.position_world_m, frame.orientation_xyzw, frame.camera_pitch_degrees)
        camera = (np.asarray(match.world_xyz)-center) @ camera_to_world
        if camera[2] <= .01:
            return None
        projected = calibration.project_camera_points(camera.reshape(1, 3))[0]
        if not np.isfinite(projected).all():
            return None
        return float(projected[0]), float(projected[1])

    def _update_match_markers(self, calibration: Calibration) -> None:
        current = self.reference_combo.currentIndex()
        markers = []
        for index, match in enumerate(self.matches):
            if match.reference_index == current:
                markers.append((match.image_xy, self._project_match(match, calibration),
                                str(index+1), match.enabled))
        if self._pending_video_xy is not None:
            markers.append((self._pending_video_xy, None, "new", True))
        self.preview.set_match_markers(markers)

    def _refresh_match_table(self, calibration: Calibration | None = None) -> None:
        if not hasattr(self, "match_table"):
            return
        if calibration is None:
            calibration = self._current_calibration()
        errors = correspondence_errors(self.session, self.matches, calibration) if self.matches else np.empty(0)
        finite_errors = errors[np.isfinite(errors)]
        outlier_limit = (max(8., float(np.median(finite_errors))*3.)
                         if len(finite_errors) else float("inf"))
        enabled_error_index = 0
        selected = self.match_table.currentRow()
        self._updating_match_table = True
        self.match_table.setRowCount(len(self.matches))
        for row, match in enumerate(self.matches):
            use = QTableWidgetItem()
            use.setFlags(use.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Checked if match.enabled else Qt.CheckState.Unchecked)
            use.setData(Qt.ItemDataRole.UserRole, row)
            self.match_table.setItem(row, 0, use)
            self.match_table.setItem(row, 1, QTableWidgetItem(self.session.label_for(match.reference_index)))
            self.match_table.setItem(row, 2, QTableWidgetItem(
                f"{match.image_xy[0]:.0f}, {match.image_xy[1]:.0f}"))
            if match.enabled:
                error = errors[enabled_error_index]; enabled_error_index += 1
                error_text = f"{error:.1f} px"
            else:
                error_text = "Excluded"
            error_item = QTableWidgetItem(error_text)
            if match.enabled and error > outlier_limit:
                error_item.setForeground(QBrush(QColor("#ef7d72")))
                error_item.setToolTip(
                    "This match has unusually high error. Verify both selected points or exclude it.")
            self.match_table.setItem(row, 3, error_item)
        if 0 <= selected < len(self.matches):
            self.match_table.selectRow(selected)
        self._updating_match_table = False

    def _match_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_match_table or item.column() != 0:
            return
        row = item.row()
        if 0 <= row < len(self.matches):
            self.matches[row] = replace(
                self.matches[row], enabled=item.checkState() == Qt.CheckState.Checked)
            self._render()

    def _match_selection_changed(self) -> None:
        if self._updating_match_table:
            return
        row = self.match_table.currentRow()
        if 0 <= row < len(self.matches):
            self.reference_combo.setCurrentIndex(self.matches[row].reference_index)

    def _delete_match(self) -> None:
        row = self.match_table.currentRow()
        if 0 <= row < len(self.matches):
            del self.matches[row]
            self._render()

    def _repick_match(self) -> None:
        row = self.match_table.currentRow()
        if not 0 <= row < len(self.matches):
            return
        reference = self.matches[row].reference_index
        del self.matches[row]
        self.reference_combo.setCurrentIndex(reference)
        self._render()
        self._begin_match()
        self.pick_status.setText(
            "Repicking the selected match — first click its precise location in the video.")

    def _clear_matches(self) -> None:
        if not self.matches:
            return
        response = QMessageBox.question(
            self, "Clear point matches?", "Remove every point match from this calibration session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if response == QMessageBox.StandardButton.Yes:
            self.matches.clear(); self._render()

    def _start_fit(self) -> None:
        enabled = [item for item in self.matches if item.enabled]
        if len(enabled) < 8:
            QMessageBox.information(
                self, "Add more point matches",
                "Add at least 8 enabled matches. For distortion fitting, 20 or more matches spread across "
                "the center, edges, and multiple depths are strongly recommended.")
            return
        if len({item.reference_index for item in enabled}) < 2 and self.reference_combo.count() > 1:
            QMessageBox.information(
                self, "Use another reference view",
                "Add matches in at least two reference views so camera shape is not confused with one-frame pose error.")
            return
        pixels = np.asarray([item.image_xy for item in enabled], dtype=float)
        initial = self._current_calibration(ignore_proposal=True)
        x_span = float(np.ptp(pixels[:, 0])/initial.image_width)
        y_span = float(np.ptp(pixels[:, 1])/initial.image_height)
        requested = self.projection_model.currentData()
        required = 24 if requested == "fisheye" else 18 if requested == "opencv" else 8
        if len(enabled) < required:
            QMessageBox.information(
                self, "Add more point matches",
                f"{self.projection_model.currentText()} fitting requires at least {required} matches. "
                "Add rigid features across multiple views and image regions, or choose Pinhole.")
            return
        needs_distortion_coverage = requested in {"opencv", "fisheye"} or (
            requested == "automatic" and len(enabled) >= 18)
        if needs_distortion_coverage and (x_span < .4 or y_span < .35):
            response = QMessageBox.question(
                self, "Limited image coverage",
                "These matches do not yet cover much of the image. Distortion can be estimated much more "
                "reliably with features near several edges and corners. Calculate anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if response != QMessageBox.StandardButton.Yes:
                return
        self._proposal_previous = initial
        self.fit_button.setEnabled(False)
        self.fit_button.setText("Calculating…")
        self.fit_progress.show()
        self.method_tabs.setTabEnabled(1, False)
        self.add_match_button.setEnabled(False)
        self.projection_model.setEnabled(False)
        self.pick_status.setText(
            "Calculating a robust fit. Automatic mode compares pinhole, radial/tangential, and fisheye models…")

        def work():
            try:
                result = fit_calibration_correspondences(
                    self.session, list(self.matches), initial, model=requested)
                self._fit_queue.put((result, None))
            except Exception as exc:
                self._fit_queue.put((None, exc))

        threading.Thread(target=work, daemon=True, name="calibration-point-fit").start()
        self._fit_timer.start()

    def _poll_fit(self) -> None:
        try:
            result, error = self._fit_queue.get_nowait()
        except queue.Empty:
            return
        self._fit_timer.stop()
        self.fit_button.setEnabled(True)
        self.fit_button.setText("Calculate best fit")
        self.fit_progress.hide()
        if error is not None:
            self.method_tabs.setTabEnabled(1, True)
            self.add_match_button.setEnabled(True)
            self.projection_model.setEnabled(True)
            self.pick_status.setText("The fit could not be calculated.")
            QMessageBox.warning(self, "Could not calculate calibration", str(error))
            return
        self._proposal = result
        enabled = [item for item in self.matches if item.enabled]
        grouped: dict[int, list[float]] = {}
        for match, error_value in zip(enabled, result.per_match_error_px):
            grouped.setdefault(match.reference_index, []).append(error_value)
        view_summary = " · ".join(
            f"{self.session.label_for(index)} {np.median(values):.1f}px"
            for index, values in sorted(grouped.items()))
        previous = self._proposal_previous
        fitted = result.calibration
        local_angles = (Rotation.from_quat(previous.rotation_xyzw).inv()
                        * Rotation.from_quat(fitted.rotation_xyzw)).as_euler(
                            "zxy", degrees=True)
        translation = (np.asarray(fitted.translation_m)
                       - np.asarray(previous.translation_m))*100.
        focal_change = ((fitted.fx/previous.fx-1.)*100.,
                        (fitted.fy/previous.fy-1.)*100.)
        principal_change = (fitted.cx-previous.cx, fitted.cy-previous.cy)
        tilt_change = fitted.tilt_zero_degrees-previous.tilt_zero_degrees
        self.proposal_summary.setText(
            f"{result.model.replace('opencv', 'Radial / tangential').title()} · "
            f"RMS {result.initial_rms_error_px:.1f} → {result.rms_error_px:.1f} px · "
            f"median {result.median_error_px:.1f} px. Per view: {view_summary}. "
            f"Changes: focal {focal_change[0]:+.1f}% / {focal_change[1]:+.1f}%; "
            f"principal {principal_change[0]:+.0f} / {principal_change[1]:+.0f}px; "
            f"mount R/P/Y {local_angles[0]:+.2f}° / {local_angles[1]:+.2f}° / "
            f"{local_angles[2]:+.2f}°; camera XYZ {translation[0]:+.1f} / "
            f"{translation[1]:+.1f} / {translation[2]:+.1f}cm; tilt zero {tilt_change:+.2f}°. "
            "The viewport is previewing this proposal.")
        self.proposal_group.show()
        self.save_button.setEnabled(False)
        self.pick_status.setText(
            "Review the proposed alignment in every reference view, then accept it or discard it.")
        self._render()

    def _accept_proposal(self) -> None:
        if self._proposal is None:
            return
        result = self._proposal
        self.working_base = result.calibration
        self._proposal = None
        self._proposal_previous = None
        self._preview_previous = False
        self.proposal_group.hide()
        self.save_button.setEnabled(True)
        self.method_tabs.setTabEnabled(1, True)
        self.add_match_button.setEnabled(True)
        self.projection_model.setEnabled(True)
        self.base_label.setText(
            f"Point-fit {result.model} · original fallback retained")
        index = self.projection_model.findData(result.model)
        self.projection_model.blockSignals(True)
        self.projection_model.setCurrentIndex(index)
        self.projection_model.blockSignals(False)
        self._reset_adjustments()
        self._sync_distortion_controls()
        self.pick_status.setText(
            "Calculated profile accepted. You can now refine it in Manual Adjustments or save it.")

    def _discard_proposal(self) -> None:
        self._proposal = None
        self._proposal_previous = None
        self._preview_previous = False
        self.proposal_group.hide()
        self.save_button.setEnabled(True)
        self.method_tabs.setTabEnabled(1, True)
        self.add_match_button.setEnabled(True)
        self.projection_model.setEnabled(True)
        self.pick_status.setText("Calculated proposal discarded; existing calibration restored.")
        self._render()

    def _show_previous_proposal(self) -> None:
        if self._proposal is not None and self._proposal_previous is not None:
            self._preview_previous = True
            self._render()

    def _show_proposed_calibration(self) -> None:
        if self._proposal is not None:
            self._preview_previous = False
            self._render()

    def _match_session_path(self, profile_path: Path) -> Path:
        return profile_path.with_suffix(".matches.json")

    def _load_saved_matches(self) -> None:
        source = self._match_session_path(self.session.base_path)
        if not source.is_file():
            return
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            labels = {self.session.label_for(index): index
                      for index in range(len(self.session.references))}
            loaded = []
            for item in data.get("matches", []):
                reference = labels.get(item.get("reference"))
                if reference is None:
                    continue
                loaded.append(CalibrationCorrespondence(
                    reference_index=reference,
                    image_xy=tuple(item["image_xy"]),
                    world_xyz=tuple(item["world_xyz"]),
                    enabled=bool(item.get("enabled", True))))
            self.matches = loaded
            if loaded:
                self.pick_status.setText(
                    f"Restored {len(loaded)} saved point matches for these reference views.")
                self._refresh_match_table(self.working_base)
        except (OSError, ValueError, TypeError, KeyError, CalibrationError):
            # A stale optional session must never prevent the workbench opening.
            self.matches = []

    def _save_match_session(self, profile_path: Path) -> None:
        if not self.matches:
            return
        data = {
            "schema_version": 1,
            "camera_profile": profile_path.name,
            "matches": [{
                "reference": self.session.label_for(item.reference_index),
                "image_xy": list(item.image_xy),
                "world_xyz": list(item.world_xyz),
                "enabled": item.enabled,
            } for item in self.matches],
        }
        self._match_session_path(profile_path).write_text(
            json.dumps(data, indent=2)+"\n", encoding="utf-8")

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
            refined = self._current_calibration(
                profile_name=self.profile_name.text().strip() or None)
            refinement = dict(refined.refinement or {})
            refinement.update({
                "reviewed_reference_frames": len(self.reviewed),
                "alignment_score": self.last_score,
                "point_match_count": len([item for item in self.matches if item.enabled]),
            })
            refined = replace(refined, schema_version=2,
                              fallback_calibration=self.session.base_calibration.fallback().to_dict(),
                              refinement=refinement)
            self.saved_path = save_refined_calibration(
                refined, target, base_path=self.session.base_path)
            self._save_match_session(self.saved_path)
        except (OSError, ValueError, CalibrationError) as exc:
            QMessageBox.warning(self, "Could not save calibration", str(exc))
            return
        self.accept()
