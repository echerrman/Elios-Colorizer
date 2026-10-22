"""Elios Colorizer 3.x application shell."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox,
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSlider,
    QStackedWidget, QTabBar, QVBoxLayout, QWidget,
)

from .. import __version__
from ..ui import MainWindow as ColorizationControllerWindow, _asset_path
from .core import ArtifactRegistry, ColorizedPointCloudArtifact, ToolRegistry
from .design_system import StatusBadge, ToggleSwitch, modular_style
from .tools.colorization import descriptor as colorization_descriptor


def _label(text: str, object_name: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if object_name:
        label.setObjectName(object_name)
    return label


class ApplicationWindow(ColorizationControllerWindow):
    """Production 3.x shell connected to the established workflow controller."""

    def __init__(self, backend=None, restore_settings: bool = True, startup_hidden: bool = False):
        self._restore_v3_settings = restore_settings
        if restore_settings:
            self._migrate_v2_settings_once()
        self.tool_registry = ToolRegistry()
        self.tool_registry.register(colorization_descriptor())
        self.artifacts = ArtifactRegistry()
        self.tool_workspaces: dict[str, QWidget] = {}
        self.colorization_workspace = None
        super().__init__(backend, restore_settings, startup_hidden, settings_application="EliosColorizer3")
        self._install_modular_shell()
        self._recent_runs = self._load_recent_runs() if self._restore_v3_settings else []
        self.colorization_workspace.set_recent_runs(self._recent_runs)
        self.setWindowTitle("Elios Colorizer")
        self.resize(1360, 900)
        self.setMinimumSize(900, 680)
        self._apply_theme()
        self._sync_modular_views()
        QApplication.instance().installEventFilter(self)

    @staticmethod
    def _migrate_v2_settings_once() -> None:
        """Copy compatible 2.x preferences once, then keep side-by-side installs isolated."""
        target = QSettings("EliosColorizer", "EliosColorizer3")
        if target.value("v2_settings_migrated", False, type=bool):
            return
        source = QSettings("EliosColorizer", "EliosColorizer")
        for key in source.allKeys():
            if not target.contains(key):
                target.setValue(key, source.value(key))
        target.setValue("v2_settings_migrated", True)
        target.sync()

    def _install_modular_shell(self) -> None:
        old_shell = self.centralWidget()
        self.workflow_layout.removeWidget(self.flight_card)
        self.workflow_layout.removeWidget(self.check_card)
        self.workflow_layout.removeWidget(self.processing_card)
        self.app_shell_layout.removeWidget(self.sticky_bar)

        shell = QWidget(objectName="modernShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        header = QFrame(objectName="modernHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 16, 10)
        header_layout.setSpacing(9)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(_label("Elios Colorizer", "productTitle"))
        header_layout.addLayout(brand)
        header_layout.addStretch(1)
        old_theme_button = self.theme_button
        self.settings_button.setText("Settings")
        self.settings_button.setIcon(QIcon(str(_asset_path("settings.svg"))))
        for button in (self.calibration_workbench_button, self.settings_button):
            button.setParent(header)
            header_layout.addWidget(button)
        self.theme_sun_icon = QLabel(header, objectName="themeSunIcon")
        self.theme_sun_icon.setPixmap(QIcon(str(_asset_path("theme-sun.svg"))).pixmap(20, 20))
        self.theme_sun_icon.setToolTip("Light mode")
        header_layout.addWidget(self.theme_sun_icon)
        self.theme_button = ToggleSwitch(self._dark_mode, header)
        self.theme_button.setAccessibleName("Use dark theme")
        self.theme_button.setToolTip("Switch between light and dark themes")
        self.theme_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_button)
        self.theme_moon_icon = QLabel(header, objectName="themeMoonIcon")
        self.theme_moon_icon.setPixmap(QIcon(str(_asset_path("theme-moon.svg"))).pixmap(20, 20))
        self.theme_moon_icon.setToolTip("Dark mode")
        header_layout.addWidget(self.theme_moon_icon)
        old_theme_button.deleteLater()
        shell_layout.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        rail = QFrame(objectName="toolRail")
        rail.setFixedWidth(210)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(14, 20, 14, 16)
        rail_layout.setSpacing(8)
        rail_layout.addWidget(_label("TOOLS", "railHeading"))
        self.tool_buttons: dict[str, QPushButton] = {}
        self.tool_button_group = QButtonGroup(self)
        self.tool_button_group.setExclusive(True)
        for tool in self.tool_registry.descriptors():
            button = QPushButton(tool.title, objectName="toolNav")
            button.setCheckable(True)
            button.setChecked(tool.id == "colorization")
            button.setToolTip(tool.description)
            button.clicked.connect(lambda _checked=False, tool_id=tool.id: self._activate_tool(tool_id))
            self.tool_button_group.addButton(button)
            rail_layout.addWidget(button)
            self.tool_buttons[tool.id] = button
        rail_layout.addSpacing(18)
        rail_layout.addWidget(_label("COMING LATER", "railHeading"))
        planned = QPushButton("Color Analysis\nAnalyze colorized clouds", objectName="plannedTool")
        planned.setEnabled(False)
        rail_layout.addWidget(planned)
        rail_layout.addStretch(1)
        self.version_label = _label(f"Version {__version__}", "pageDescription")
        rail_layout.addWidget(self.version_label)
        body.addWidget(rail)

        self.tool_host = QStackedWidget()
        for descriptor in self.tool_registry.descriptors():
            workspace = descriptor.factory(self)
            self.tool_workspaces[descriptor.id] = workspace
            self.tool_host.addWidget(workspace)
        self.colorization_workspace = self.tool_workspaces["colorization"]
        body.addWidget(self.tool_host, 1)

        self.summary_rail = self._build_summary_rail()
        body.addWidget(self.summary_rail)
        shell_layout.addLayout(body, 1)

        for overlay in (self.drop_overlay, self.calibration_loading_overlay):
            overlay.setParent(shell)
            overlay.raise_()

        self.setCentralWidget(shell)
        self.app_shell = shell
        self.app_shell_layout = shell_layout
        old_shell.deleteLater()
        self.colorization_workspace.page_changed.connect(self._on_workflow_page_changed)

    def _activate_tool(self, tool_id: str) -> None:
        workspace = self.tool_workspaces[tool_id]
        self.tool_host.setCurrentWidget(workspace)
        self.tool_buttons[tool_id].setChecked(True)

    def _build_summary_rail(self) -> QFrame:
        rail = QFrame(objectName="summaryRail")
        rail.setFixedWidth(270)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(17, 20, 17, 16)
        layout.setSpacing(10)
        layout.addWidget(_label("RUN SUMMARY", "railHeading"))
        self.summary_flights = self._summary_block(layout, "Flights")
        self.summary_validation = self._summary_block(layout, "Validation", status=True)
        self.summary_mode = self._summary_block(layout, "Result")
        self.summary_output = self._summary_block(layout, "Output")
        layout.addStretch(1)
        return rail

    @staticmethod
    def _summary_block(layout: QVBoxLayout, title: str, status: bool = False) -> QLabel:
        frame = QFrame(objectName="summaryBlock")
        box = QVBoxLayout(frame)
        box.setContentsMargins(11, 9, 11, 10)
        box.setSpacing(3)
        box.addWidget(_label(title.upper(), "railHeading"))
        value = StatusBadge() if status else _label("—", "summaryValue")
        box.addWidget(value)
        layout.addWidget(frame)
        return value

    def _on_workflow_page_changed(self, page_id: str) -> None:
        self._sync_modular_views()

    def _sync_modular_views(self) -> None:
        workspace = self.colorization_workspace
        if workspace is None:
            return
        count = len(self.flight_rows)
        self.summary_flights.setText(f"{count} flight{'s' if count != 1 else ''}")
        if self._job_kind == "inspect" or self._inspection_pending:
            validation = "Checking…"
        elif self._ready:
            validation = "Ready"
        elif self._last_inspection_result:
            validation = "Needs attention"
        else:
            validation = "Not checked"
        tone = "info" if validation == "Checking…" else "success" if validation == "Ready" else "warning" if validation == "Needs attention" else "neutral"
        self.summary_validation.set_status(validation, tone)
        self.summary_mode.setText("Merged cloud" if self.mode_combo.currentData() == "merge" else "Separate outputs")
        output = self.output_edit.text().strip()
        self.summary_output.setText(output or "Not selected")
        self.summary_output.setToolTip(output)
        workspace.refresh()

    def _apply_theme(self):
        super()._apply_theme()
        if hasattr(self, "theme_button"):
            destination = "light" if self._dark_mode else "dark"
            self.theme_button.setAccessibleName(f"Use {destination} theme")
            self.theme_button.setToolTip(f"Switch to {destination} mode")
        self.setStyleSheet(self.styleSheet() + modular_style(self._dark_mode, str(_asset_path("checkmark.svg"))))

    def _apply_responsive_layout(self, available_width=None):
        if self.colorization_workspace is None:
            return super()._apply_responsive_layout(available_width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "summary_rail"):
            self.summary_rail.setVisible(self.width() >= 1160)

    def closeEvent(self, event):
        super().closeEvent(event)
        if event.isAccepted():
            application = QApplication.instance()
            if application is not None:
                application.removeEventFilter(self)

    def eventFilter(self, watched, event):
        """Reserve the wheel for scrolling; settings change only by deliberate input."""
        wheel_controls = (QComboBox, QAbstractSpinBox, QSlider, QCheckBox, QRadioButton, QTabBar)
        if event.type() == QEvent.Type.Wheel and isinstance(watched, wheel_controls):
            parent = watched.parentWidget()
            while parent is not None and not isinstance(parent, QAbstractScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                QApplication.sendEvent(parent.viewport(), event)
            return True
        return super().eventFilter(watched, event)

    def _inputs_changed(self, *_):
        super()._inputs_changed(*_)
        if self.colorization_workspace is not None:
            self.colorization_workspace.reset_results()
        self._sync_modular_views()

    def _mode_changed(self, *_):
        super()._mode_changed(*_)
        self._sync_modular_views()

    def _update_actions(self):
        super()._update_actions()
        self._sync_modular_views()

    def _on_result(self, result):
        was_run = self._job_kind == "run"
        super()._on_result(result)
        if was_run:
            self._record_result_artifact(result)
            self._record_recent_run(result)
            self.colorization_workspace.show_result()
        self._sync_modular_views()

    def _record_result_artifact(self, result) -> None:
        output = result.get("output")
        if not output:
            return
        report = result.get("report")
        artifact = ColorizedPointCloudArtifact(
            path=Path(output),
            report_path=Path(report) if report else None,
            source_flights=tuple(
                row.name_edit.text().strip() or f"Flight {index}"
                for index, row in enumerate(self.flight_rows, 1)
            ),
            colored_points=int(result.get("colored_points") or 0),
            total_points=int(result.get("total_points") or 0),
        )
        self.artifacts.add(artifact)

    def _on_error(self, message, details):
        was_run = self._job_kind == "run"
        cancelled = self._cancellation.is_set()
        super()._on_error(message, details)
        if was_run:
            self.colorization_workspace.show_failure(
                "Processing stopped safely." if cancelled else message,
                cancelled=cancelled,
            )
        self._sync_modular_views()

    def _start_colorization(self):
        super()._start_colorization()
        if self._job_kind == "run":
            self.colorization_workspace.reset_results()
            self.colorization_workspace.set_page("run")

    def _show_advanced_settings(self):
        """The 3.0 shortcut reveals the inline controls instead of a modal dialog."""
        if self.colorization_workspace is None:
            return
        self.colorization_workspace.set_page("processing")
        self.colorization_workspace._scrolls[2].ensureWidgetVisible(
            self.colorization_workspace.advanced_panel, 20, 40
        )

    def _load_recent_runs(self) -> list[dict]:
        raw = self.settings.value("recent_run_summaries_json", "[]")
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError):
            return []
        return [item for item in value if isinstance(item, dict)][:20] if isinstance(value, list) else []

    def _save_recent_runs(self) -> None:
        if not self._restore_v3_settings:
            return
        self.settings.setValue("recent_run_summaries_json", json.dumps(self._recent_runs))
        self.settings.sync()

    def _record_recent_run(self, result) -> None:
        total = int(result.get("total_points") or 0)
        colored = int(result.get("colored_points") or 0)
        coverage = colored / total * 100 if total else 0.0
        elapsed = max(0.0, time.monotonic() - self._run_started_at) if self._run_started_at else 0.0
        self._recent_runs.insert(0, {
            "id": uuid.uuid4().hex,
            "completed_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
            "output": str(result.get("output") or self.output_edit.text().strip()),
            "colored_points": colored,
            "total_points": total,
            "coverage": f"{coverage:.1f}% ({colored:,} / {total:,} points)",
            "elapsed": self._format_duration(elapsed),
            "settings": self.colorization_workspace.advanced_panel.active_summary.text(),
            "mode": "Merged cloud" if self.mode_combo.currentData() == "merge" else "Separate outputs",
        })
        self._recent_runs = self._recent_runs[:20]
        self._save_recent_runs()
        self.colorization_workspace.set_recent_runs(self._recent_runs)

    def _delete_recent_run(self, run_id) -> None:
        self._recent_runs = [entry for entry in self._recent_runs if entry.get("id") != run_id]
        self._save_recent_runs()
        self.colorization_workspace.set_recent_runs(self._recent_runs)

    def _navigate_to_check(self, item):
        if self.colorization_workspace is not None:
            self.colorization_workspace.set_page("inputs")
        super()._navigate_to_check(item)
