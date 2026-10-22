"""Step-oriented colorization workspace for the 3.0 desktop shell."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ....ui import AdvancedProcessingDialog, AdvancedProcessingSettings, LoadingSpinner


def _plain_label(text: str, object_name: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if object_name:
        label.setObjectName(object_name)
    return label


class ValidationScrollArea(QScrollArea):
    """A scroll page whose busy overlay covers the visible validation tab."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.overlay = QFrame(self.viewport(), objectName="validationLoadingOverlay")
        box = QVBoxLayout(self.overlay)
        box.addStretch(1)
        self.spinner = LoadingSpinner(self.overlay)
        box.addWidget(self.spinner, alignment=Qt.AlignmentFlag.AlignCenter)
        message = _plain_label("Refreshing and validating all selected inputs…", "validationLoadingText")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(message)
        box.addStretch(1)
        self.overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.overlay.setGeometry(self.viewport().rect())

    def set_loading(self, loading: bool) -> None:
        self.overlay.setGeometry(self.viewport().rect())
        self.overlay.setVisible(loading)
        if loading:
            self.spinner.start()
            self.overlay.raise_()
        else:
            self.spinner.stop()


class ColorizationWorkspace(QWidget):
    """Owns the visible workflow while the established controller owns behavior."""

    page_changed = Signal(str)
    PAGE_IDS = ("inputs", "validation", "processing", "run", "results")

    def __init__(self, owner: QWidget) -> None:
        super().__init__()
        self.owner = owner
        self._scrolls: list[QScrollArea] = []
        self._next_buttons: dict[str, QPushButton] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        workflow_header = QFrame(objectName="workflowHeader")
        workflow_layout = QVBoxLayout(workflow_header)
        workflow_layout.setContentsMargins(24, 16, 24, 12)
        workflow_layout.setSpacing(10)
        workflow_layout.addWidget(_plain_label("Colorize point clouds", "pageHeading"))
        workflow_layout.addWidget(_plain_label(
            "Prepare inputs, pass validation, configure processing, then run and monitor colorization.",
            "pageDescription",
        ))
        nav = QHBoxLayout()
        nav.setSpacing(7)
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        self.navigation_buttons: dict[str, QPushButton] = {}
        labels = ("1  Inputs", "2  Validation", "3  Processing", "4  Run", "5  Results")
        for index, (page_id, text) in enumerate(zip(self.PAGE_IDS, labels)):
            button = QPushButton(text, objectName="workflowNav")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self.set_page(i))
            self.navigation_group.addButton(button)
            self.navigation_buttons[page_id] = button
            nav.addWidget(button)
        nav.addStretch(1)
        workflow_layout.addLayout(nav)
        root.addWidget(workflow_header)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._inputs_page())
        self.pages.addWidget(self._validation_page())
        self.pages.addWidget(self._processing_page())
        self.pages.addWidget(self._run_page())
        self.pages.addWidget(self._results_page())
        root.addWidget(self.pages, 1)
        self.set_page(0)

    def _new_page(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        container = QWidget(objectName="workflowPage")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 22, 24, 28)
        layout.setSpacing(12)
        layout.addWidget(_plain_label(title.upper(), "eyebrow"))
        layout.addWidget(_plain_label(description, "pageDescription"))
        return container, layout

    def _scroll_page(self, container: QWidget, validation: bool = False) -> QScrollArea:
        scroll = ValidationScrollArea() if validation else QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(container)
        self._scrolls.append(scroll)
        return scroll

    def _next_action(self, layout: QVBoxLayout, source: str, text: str, destination: str) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        button = QPushButton(text, objectName="primary")
        button.clicked.connect(lambda _checked=False, page=destination: self.set_page(page))
        row.addWidget(button)
        layout.addLayout(row)
        self._next_buttons[source] = button

    def _inputs_page(self) -> QScrollArea:
        container, layout = self._new_page("Inputs", "Select every flight input and all shared data required for this run.")
        self.owner.flight_card.setParent(container)
        layout.addWidget(self.owner.flight_card)
        self.owner.camera_card.setParent(container)
        layout.addWidget(self.owner.camera_card)
        self.owner.alignment_panel.setParent(container)
        layout.addWidget(self.owner.alignment_panel)
        self._next_action(layout, "inputs", "Review validation", "validation")
        layout.addStretch(1)
        return self._scroll_page(container)

    def _validation_page(self) -> QScrollArea:
        container, layout = self._new_page(
            "Validation", "Errors must be resolved before processing. Warnings are informational and do not block the run."
        )
        self.validation_banner = QFrame(objectName="validationBanner")
        banner = QHBoxLayout(self.validation_banner)
        banner.setContentsMargins(15, 12, 15, 12)
        self.validation_icon = _plain_label("○", "validationStateIcon")
        self.validation_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner.addWidget(self.validation_icon)
        text = QVBoxLayout()
        self.validation_title = _plain_label("Waiting for inputs", "emptyTitle")
        self.validation_detail = _plain_label("Select at least one complete flight, then refresh the checks.", "pageDescription")
        text.addWidget(self.validation_title)
        text.addWidget(self.validation_detail)
        banner.addLayout(text, 1)
        layout.addWidget(self.validation_banner)
        self.owner.check_card.setParent(container)
        layout.addWidget(self.owner.check_card)
        self._next_action(layout, "validation", "Configure processing", "processing")
        layout.addStretch(1)
        scroll = self._scroll_page(container, validation=True)
        self.validation_scroll = scroll
        return scroll

    def _processing_page(self) -> QScrollArea:
        container, layout = self._new_page(
            "Processing", "Configure the complete processing policy for this run."
        )
        # Keep the legacy controller card alive for its hidden state widgets, but
        # present its useful controls in the unified settings surface below.
        self.owner.processing_card.setParent(container)
        self.owner.processing_card.hide()
        self.owner.processing_plan_label.setParent(container)
        layout.addWidget(self.owner.processing_plan_label)
        self.owner.preflight_panel.setParent(container)
        layout.addWidget(self.owner.preflight_panel)
        self.advanced_panel = AdvancedProcessingDialog(
            AdvancedProcessingSettings(), container,
            presets=self.owner.advanced_presets,
            available_duration_s=self._available_duration(),
            embedded=True,
        )
        self.advanced_panel.preset_combo.setCurrentIndex(self.advanced_panel.preset_combo.findData("__default__"))
        self.advanced_panel.settings_changed.connect(self._advanced_changed)
        self.advanced_panel.save_preset_button.clicked.connect(self._advanced_changed)
        self.advanced_panel.delete_preset_button.clicked.connect(self._advanced_changed)
        self.advanced_panel.attach_workflow_controls(
            self.owner.distance_check, self.owner.distance_spin, self.owner.color_balance_check
        )
        layout.addWidget(self.advanced_panel)
        self._advanced_changed()
        output_panel = QFrame(objectName="dialogSection")
        output_box = QVBoxLayout(output_panel)
        output_box.setContentsMargins(12, 10, 12, 12)
        output_box.setSpacing(7)
        output_box.addWidget(_plain_label("Output destination", "emptyTitle"))
        output_box.addWidget(_plain_label(
            "Choose the output LAS for one or merged flights, or an output folder when multiple flights will be processed separately.",
            "pageDescription",
        ))
        output_row = QHBoxLayout()
        self.owner.output_edit.setParent(output_panel)
        self.owner.output_button.setParent(output_panel)
        output_row.addWidget(self.owner.output_edit, 1)
        output_row.addWidget(self.owner.output_button)
        output_box.addLayout(output_row)
        self.owner.output_preview.setParent(output_panel)
        output_box.addWidget(self.owner.output_preview)
        layout.addWidget(output_panel)
        self._next_action(layout, "processing", "Review and run", "run")
        layout.addStretch(1)
        return self._scroll_page(container)

    def _available_duration(self) -> float | None:
        report = self.owner._last_inspection_result or {}
        reports = report.get("flights") or ([report] if report else [])
        durations = [float(item.get("video_duration_s") or 0) for item in reports]
        return min((value for value in durations if value > 0), default=None)

    def _advanced_changed(self) -> None:
        if not hasattr(self, "advanced_panel"):
            return
        self.owner.advanced_settings = self.advanced_panel.values()
        self.owner.advanced_presets = self.advanced_panel.presets()
        if getattr(self.owner, "_restore_v3_settings", True):
            self.owner._save_advanced_presets()
        self.owner._update_advanced_summary()
        self.owner._update_processing_summaries()

    def _run_page(self) -> QScrollArea:
        container, layout = self._new_page(
            "Run & monitor", "Confirm the plan, start colorization, and monitor progress in one place."
        )
        card = QFrame(objectName="reviewCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 20)
        box.setSpacing(12)
        self.review_status = _plain_label("Complete the earlier steps to prepare this run.", "emptyTitle")
        box.addWidget(self.review_status)
        self.review_inputs = _plain_label("")
        self.review_processing = _plain_label("")
        self.review_outputs = _plain_label("")
        for label in (self.review_inputs, self.review_processing, self.review_outputs):
            label.setObjectName("reviewBlock")
            box.addWidget(label)
        layout.addWidget(card)
        self.owner.sticky_bar.setParent(container)
        self.owner.sticky_bar.setObjectName("runPanel")
        layout.addWidget(self.owner.sticky_bar)
        self.owner.progress_detail.setParent(container)
        layout.addWidget(self.owner.progress_detail)
        self.owner.flight_progress.setParent(container)
        layout.addWidget(self.owner.flight_progress)
        details = QHBoxLayout()
        details.addStretch(1)
        self.owner.details_button.setParent(container)
        details.addWidget(self.owner.details_button)
        layout.addLayout(details)
        self.owner.log.setParent(container)
        layout.addWidget(self.owner.log)
        layout.addStretch(1)
        return self._scroll_page(container)

    def _results_page(self) -> QScrollArea:
        container, layout = self._new_page("Results", "See the latest outcome and a lightweight history of recent runs.")
        self.results_empty = QFrame(objectName="emptyState")
        empty_box = QVBoxLayout(self.results_empty)
        empty_box.setContentsMargins(28, 34, 28, 34)
        empty_box.setSpacing(7)
        self.results_icon = _plain_label("✓", "emptyIcon")
        self.results_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_box.addWidget(self.results_icon)
        self.results_title = _plain_label("No completed run yet", "emptyTitle")
        self.results_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_box.addWidget(self.results_title)
        self.results_note = _plain_label(
            "Finish a colorization to see coverage, output locations, reports, and next actions.", "pageDescription"
        )
        self.results_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_box.addWidget(self.results_note)
        layout.addWidget(self.results_empty)
        self.owner.result_panel.setParent(container)
        layout.addWidget(self.owner.result_panel)
        layout.addWidget(_plain_label("RECENT RUNS", "eyebrow"))
        self.recent_runs = QWidget()
        self.recent_runs_layout = QVBoxLayout(self.recent_runs)
        self.recent_runs_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_runs_layout.setSpacing(8)
        layout.addWidget(self.recent_runs)
        layout.addStretch(1)
        return self._scroll_page(container)

    def set_recent_runs(self, runs: list[dict]) -> None:
        while self.recent_runs_layout.count():
            item = self.recent_runs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not runs:
            self.recent_runs_layout.addWidget(_plain_label("No retained run summaries.", "pageDescription"))
            return
        for entry in runs:
            card = QFrame(objectName="recentRunCard")
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 11, 14, 11)
            body = QVBoxLayout()
            output = str(entry.get("output") or "Unknown output")
            title = _plain_label(Path(output).name or output, "summaryValue")
            title.setToolTip(output)
            body.addWidget(title)
            body.addWidget(_plain_label(
                f"{entry.get('completed_at', '')}  ·  {entry.get('coverage', '—')} colored  ·  "
                f"{entry.get('elapsed', '—')}  ·  {entry.get('settings', 'Default settings')}", "pageDescription"
            ))
            body.addWidget(_plain_label(output, "pageDescription"))
            row.addLayout(body, 1)
            delete = QPushButton("Delete", objectName="removeFlightButton")
            delete.setToolTip("Delete this summary only; output data is not removed.")
            delete.clicked.connect(lambda _checked=False, run_id=entry.get("id"): self.owner._delete_recent_run(run_id))
            row.addWidget(delete)
            self.recent_runs_layout.addWidget(card)

    def set_validation_loading(self, loading: bool) -> None:
        self.validation_scroll.set_loading(loading)

    def validation_passed(self) -> bool:
        owner = self.owner
        has_flight = any(row.folder_edit.text().strip() for row in owner.flight_rows)
        return bool(
            has_flight and owner._ready and owner._inspected_selection == owner._inspection_selection()
            and not owner._inspection_pending and owner._job_kind != "inspect"
        )

    def refresh_access(self) -> None:
        passed = self.validation_passed()
        for page_id in ("processing", "run"):
            self.navigation_buttons[page_id].setEnabled(passed)
        self._next_buttons["validation"].setEnabled(passed)
        checklist = (self.owner._last_inspection_result or {}).get("checklist", [])
        errors = [row for row in checklist if row.get("status") == "missing"]
        warnings = [row for row in checklist if row.get("status") == "warning"]
        if self.owner._job_kind == "inspect" or self.owner._inspection_pending:
            title, detail, icon = "Checking selected data", "Running all validation checks…", "◌"
        elif passed:
            title = "Validation passed"
            detail = f"No blocking errors. {len(warnings)} warning{'s' if len(warnings) != 1 else ''} may be reviewed below."
            icon = "✓"
        elif errors:
            title = f"{len(errors)} blocking error{'s' if len(errors) != 1 else ''}"
            detail, icon = "Resolve every error before continuing. Warnings do not block processing.", "!"
        else:
            title, detail, icon = "Waiting for valid inputs", "Select at least one complete flight and refresh the checks.", "○"
        self.validation_title.setText(title)
        self.validation_detail.setText(detail)
        self.validation_icon.setText(icon)
        self.validation_banner.setProperty("state", "ready" if passed else "error" if errors else "idle")
        self.validation_banner.style().unpolish(self.validation_banner)
        self.validation_banner.style().polish(self.validation_banner)
        if self.current_page_id in ("processing", "run") and not passed:
            self.set_page("validation")

    def show_result(self) -> None:
        self.results_empty.hide()
        self.owner.result_panel.show()
        self.set_page("results")

    def show_failure(self, message: str, cancelled: bool = False) -> None:
        self.results_icon.setText("■" if cancelled else "×")
        self.results_title.setText("Colorization cancelled" if cancelled else "Colorization could not finish")
        self.results_note.setText(message or ("Processing stopped safely." if cancelled else "Review processing details and try again."))
        self.results_empty.show()
        self.owner.result_panel.hide()
        self.set_page("results")

    def reset_results(self) -> None:
        self.results_icon.setText("✓")
        self.results_title.setText("No completed run yet")
        self.results_note.setText("Finish a colorization to see coverage, output locations, reports, and next actions.")
        self.results_empty.show()
        self.owner.result_panel.hide()

    def set_page(self, page: int | str) -> None:
        index = self.PAGE_IDS.index(page) if isinstance(page, str) else page
        if not 0 <= index < self.pages.count():
            raise ValueError(f"Unknown colorization page: {page!r}")
        requested = self.PAGE_IDS[index]
        if requested in ("processing", "run") and not self.validation_passed():
            index = self.PAGE_IDS.index("validation")
            requested = "validation"
        self.pages.setCurrentIndex(index)
        self.navigation_buttons[requested].setChecked(True)
        self.owner.scroll_area = self._scrolls[index]
        if requested == "run":
            self.refresh()
        self.page_changed.emit(requested)

    def refresh(self) -> None:
        owner = self.owner
        self.set_validation_loading(owner._job_kind == "inspect" or owner._inspection_pending)
        self.refresh_access()
        flight_count = len(owner.flight_rows)
        names = [row.name_edit.text().strip() or f"Flight {i}" for i, row in enumerate(owner.flight_rows, 1)]
        profile = Path(owner.calibration_edit.text()).name if owner.calibration_edit.text().strip() else "Automatic/default profile"
        self.review_inputs.setText(
            f"INPUTS\n{flight_count} flight{'s' if flight_count != 1 else ''}: {', '.join(names)}\nCamera profile: {profile}"
        )
        mode = "One merged cloud" if owner.mode_combo.currentData() == "merge" else "Separate output clouds"
        distance = f" · {owner.distance_spin.value():g} m distance limit" if owner.distance_check.isChecked() else " · no distance limit"
        balancing = " · illumination balancing on" if owner.color_balance_check.isChecked() else " · illumination balancing off"
        self.review_processing.setText(
            f"PROCESSING\n{mode}{distance}{balancing}\n{self.advanced_panel.active_summary.text()}"
        )
        outputs = owner._planned_outputs()
        output_text = "\n".join(f"{name}: {path}" for name, path in outputs.items()) or "No output destination selected"
        self.review_outputs.setText(f"OUTPUTS\n{output_text}")
        self.review_outputs.setProperty("missingOutput", not bool(owner.output_edit.text().strip()))
        self.review_outputs.style().unpolish(self.review_outputs)
        self.review_outputs.style().polish(self.review_outputs)
        if owner._job_kind == "run":
            status = "Colorization in progress"
        elif owner._job_kind == "inspect" or owner._inspection_pending:
            status = "Checking inputs"
        else:
            status = "Ready to colorize" if owner.run_button.isEnabled() else "This run still needs attention"
        self.review_status.setText(status)

    @property
    def current_page_id(self) -> str:
        return self.PAGE_IDS[self.pages.currentIndex()]
