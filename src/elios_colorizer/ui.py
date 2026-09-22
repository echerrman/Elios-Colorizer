"""Responsive, deliberately small desktop front end for Elios Colorizer.

All flight inspection and processing happens outside the GUI thread.  The
service module owns data validation, dependency checks and colorization; the UI
only presents its results and manages one cooperative worker at a time.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


_LIGHT_STYLE = """
QMainWindow, QWidget#page { background: #f3f5f7; color: #1e293b; }
QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
QFrame#card { background: #ffffff; border: 1px solid #dce3eb; border-radius: 10px; }
QLabel { color: #26354a; background: transparent; }
QLabel#title { font-size: 23pt; font-weight: 700; color: #142438; }
QLabel#sectionTitle { font-size: 12pt; font-weight: 650; color: #152d45; }
QLabel#muted { color: #617184; }
QLineEdit { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 5px;
            padding: 8px; color: #192c42; selection-background-color: #c0dcf5; }
QLineEdit:focus { border: 1px solid #317da6; }
QLineEdit:disabled { background: #f1f3f5; color: #748095; }
QPushButton { background: #ffffff; color: #25465f; border: 1px solid #cbd5e1;
              border-radius: 5px; padding: 8px 13px; font-weight: 600; }
QPushButton:hover { background: #edf5fa; border-color: #86abc5; }
QPushButton:pressed { background: #dceaf3; }
QPushButton:disabled { color: #8a99a9; background: #eff2f5; border-color: #dde4ec; }
QPushButton#primary { background: #126d8b; color: #ffffff; border-color: #126d8b; }
QPushButton#primary:hover { background: #0e5d77; }
QPushButton#primary:disabled { background: #b6c9d1; border-color: #b6c9d1; color: #f8fafc; }
QProgressBar { border: none; background: #e8eef3; border-radius: 4px; min-height: 8px; }
QProgressBar::chunk { background: #1784a3; border-radius: 4px; }
QPlainTextEdit { background: #f8fafc; color: #44586e; border: 1px solid #dce3eb;
                 border-radius: 5px; padding: 5px; font-family: Consolas; font-size: 9pt; }
QScrollArea { background: transparent; border: none; }
"""

_DARK_STYLE = """
QMainWindow, QWidget#page { background: #17212b; color: #e6edf3; }
QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
QFrame#card { background: #202c38; border: 1px solid #344454; border-radius: 10px; }
QLabel { color: #d9e2ec; background: transparent; }
QLabel#title { font-size: 23pt; font-weight: 700; color: #f4f8fb; }
QLabel#sectionTitle { font-size: 12pt; font-weight: 650; color: #dcecf5; }
QLabel#muted { color: #aab9c7; }
QLineEdit { background: #16212b; border: 1px solid #4a5d70; border-radius: 5px;
            padding: 8px; color: #edf4f8; selection-background-color: #28627b; }
QLineEdit:focus { border: 1px solid #63b6d1; }
QLineEdit:disabled { background: #24313d; color: #8293a4; }
QPushButton { background: #263745; color: #e3edf4; border: 1px solid #4a5d70;
              border-radius: 5px; padding: 8px 13px; font-weight: 600; }
QPushButton:hover { background: #304758; border-color: #70b4ca; }
QPushButton:pressed { background: #1e5a72; }
QPushButton:disabled { color: #7b8b9a; background: #25313c; border-color: #344454; }
QPushButton#primary { background: #1784a3; color: #ffffff; border-color: #1784a3; }
QPushButton#primary:hover { background: #2197b7; }
QPushButton#primary:disabled { background: #3b5b68; border-color: #3b5b68; color: #aab9c7; }
QProgressBar { border: none; background: #30404e; border-radius: 4px; min-height: 8px; }
QProgressBar::chunk { background: #42b6d3; border-radius: 4px; }
QPlainTextEdit { background: #16212b; color: #c1d0dc; border: 1px solid #344454;
                 border-radius: 5px; padding: 5px; font-family: Consolas; font-size: 9pt; }
QScrollArea { background: transparent; border: none; }
"""


def _asset_path(name: str) -> Path:
    root = (
        Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2]
    )
    return root / "assets" / name


def _application_icon() -> QIcon:
    return QIcon(str(_asset_path("elios_colorizer.svg")))


def _theme_icon(dark_mode: bool) -> QIcon:
    return QIcon(str(_asset_path("theme-moon.svg" if dark_mode else "theme-sun.svg")))


@dataclass(frozen=True)
class SourceSelection:
    folder: str
    las_override: str | None = None
    calibration_override: str | None = None

    def kwargs(self) -> dict[str, str | None]:
        return {
            "folder": self.folder,
            "las_override": self.las_override,
            "calibration_override": self.calibration_override,
        }


class ServiceWorker(QObject):
    """Bridge ordinary service callbacks into queued Qt signals."""

    result = Signal(object)
    error = Signal(str, str)
    progress = Signal(str, float, str)
    finished = Signal()

    def __init__(
        self,
        backend: Any,
        kind: str,
        selection: SourceSelection,
        cancellation: threading.Event,
        output: str = "",
    ) -> None:
        super().__init__()
        self.backend = backend
        self.kind = kind
        self.selection = selection
        self.cancellation = cancellation
        self.output = output

    @Slot()
    def run(self) -> None:
        try:
            if self.kind == "inspect":
                result = self.backend.inspect_source(**self.selection.kwargs())
            else:
                result = self.backend.run_colorization(
                    **self.selection.kwargs(),
                    output=self.output,
                    progress=self.progress.emit,
                    cancelled=self.cancellation.is_set,
                )
            self.result.emit(result)
        except Exception as exc:
            self.error.emit(str(exc) or type(exc).__name__, traceback.format_exc())
        finally:
            self.finished.emit()


def _label(text: str, *, muted: bool = False) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if muted:
        label.setObjectName("muted")
    return label


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(19, 16, 19, 17)
    layout.setSpacing(10)
    heading = _label(title)
    heading.setObjectName("sectionTitle")
    layout.addWidget(heading)
    return card, layout


class Checklist(QWidget):
    """Compact, accessible status list whose labels never interpret file names as HTML."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[dict[str, Any]] = []
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(9)

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for row in rows:
            status = str(row.get("status", "warning"))
            word, color = {
                "ok": ("READY", "#187548"),
                "missing": ("NEEDED", "#b34330"),
                "warning": ("CHECK", "#926616"),
            }.get(status, ("CHECK", "#926616"))
            container = QWidget()
            grid = QGridLayout(container)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(2)
            state = _label(word)
            state.setStyleSheet(f"color: {color}; font-size: 8pt; font-weight: 700;")
            state.setMinimumWidth(49)
            state.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            name = _label(str(row.get("label", "Input")))
            name.setStyleSheet("font-weight: 600;")
            raw_detail = str(row.get("detail", ""))
            wrapped_detail = re.sub(r"([\\/_.-])", lambda match: match.group(1) + "\u200b", raw_detail)
            detail = _label(wrapped_detail, muted=True)
            detail.setToolTip(raw_detail)
            detail.setMinimumWidth(0)
            detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(state, 0, 0, 2, 1)
            grid.addWidget(name, 0, 1)
            grid.addWidget(detail, 1, 1)
            grid.setColumnStretch(1, 1)
            self.layout.addWidget(container)
        self.layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, backend: Any = None, *, restore_settings: bool = True) -> None:
        super().__init__()
        if backend is None:
            from . import service

            backend = service
        self.backend = backend
        self.settings = QSettings("EliosColorizer", "EliosColorizer")
        self._thread: QThread | None = None
        self._worker: ServiceWorker | None = None
        self._job_kind = ""
        self._job_selection: SourceSelection | None = None
        self._job_revision = -1
        self._revision = 0
        self._inspection_pending = False
        self._inspected_selection: SourceSelection | None = None
        self._ready = False
        self._cancellation = threading.Event()
        self._close_when_idle = False
        self._last_result: dict[str, Any] | None = None
        self._last_progress_message = ""
        self._run_started_at: float | None = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._dark_mode = self.settings.value("dark_mode", False, type=bool)

        self.setWindowTitle("Elios Colorizer")
        self.resize(1050, 900)
        self.setMinimumSize(820, 680)
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(1050, available.width() - 60), min(900, available.height() - 60))
        self._apply_theme()
        self._build_ui()
        self._apply_theme()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._start_pending_inspection)
        for edit in (self.source_edit, self.las_edit, self.calibration_edit):
            edit.textChanged.connect(self._inputs_changed)
        self.output_edit.textChanged.connect(self._update_actions)

        if restore_settings:
            self.source_edit.setText(str(self.settings.value("source", "")))
            self.output_edit.setText(str(self.settings.value("output", "")))
            self.calibration_edit.setText(str(self.settings.value('calibration', '')))
        self._show_empty_checklist()
        self._update_actions()
        if self.source_edit.text().strip():
            self._inputs_changed()

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 23)
        layout.setSpacing(16)
        title_row = QHBoxLayout()
        title = _label("Elios Colorizer")
        title.setObjectName("title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.theme_button = QPushButton()
        self.theme_button.setCheckable(True)
        self.theme_button.setChecked(self._dark_mode)
        self.theme_button.clicked.connect(self._toggle_theme)
        self.theme_button.setMinimumWidth(135)
        title_row.addWidget(self.theme_button)
        layout.addLayout(title_row)
        layout.addWidget(_label("Bring your flight's RGB imagery onto its recorded point cloud.", muted=True))

        source_card, source_layout = _card("1  Choose a flight")
        source_layout.addWidget(_label("Select the native Inspector flight folder. Files inside subfolders are discovered automatically.", muted=True))
        self.source_edit, self.source_button = self._file_row(source_layout, "Flight folder", "Select flight folder…", self._browse_source)
        self.las_edit, self.las_button = self._file_row(source_layout, "Point cloud", "Automatic — or choose an exported LAS / LAZ", self._browse_las)
        self.las_edit.setToolTip("Leave empty to find a point cloud in the flight folder. Select an exported cloud here when it is stored separately.")
        self.calibration_edit, self.calibration_button = self._file_row(source_layout, "Camera profile", "Automatic — or choose an RGB calibration profile", self._browse_calibration)
        source_layout.addWidget(_label("Camera profile is a one-time setup for each camera. It defines the lens and mounting geometry; the readiness check looks for a usable saved profile.", muted=True))
        self.calibration_help = QPushButton('Calibration guide')
        self.calibration_help.clicked.connect(self._open_calibration_guide)
        source_layout.addWidget(self.calibration_help, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(source_card)

        checks_row = QHBoxLayout()
        checks_row.setSpacing(16)
        checks_card, checks_layout = _card("2  Check flight data")
        checks_card.setMinimumWidth(380)
        checks_card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.checklist = Checklist()
        checks_layout.addWidget(self.checklist)
        self.summary = _label("Choose a flight folder to check its inputs.", muted=True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        checks_layout.addWidget(self.summary)
        checks_row.addWidget(checks_card, 3)
        dependency_card, dependency_layout = _card("Tools & libraries")
        dependency_card.setMinimumWidth(290)
        dependency_card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.dependencies = Checklist()
        dependency_layout.addWidget(self.dependencies)
        self.refresh_button = QPushButton("Refresh checks")
        self.refresh_button.clicked.connect(self._inputs_changed)
        dependency_layout.addWidget(self.refresh_button, alignment=Qt.AlignmentFlag.AlignLeft)
        checks_row.addWidget(dependency_card, 2)
        layout.addLayout(checks_row)

        output_card, output_layout = _card("3  Save the colorized cloud")
        self.output_edit, self.output_button = self._file_row(output_layout, "Output LAS", "Choose an output file…", self._browse_output)
        output_layout.addWidget(_label("The output keeps the recorded point positions and adds RGB values. Points without a reliable visible observation remain uncolored.", muted=True))
        action_row = QHBoxLayout()
        self.state_label = _label("Select a flight to begin.")
        self.state_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        action_row.addWidget(self.state_label, 1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        action_row.addWidget(self.cancel_button)
        self.run_button = QPushButton("Colorize point cloud")
        self.run_button.setObjectName("primary")
        self.run_button.setMinimumWidth(185)
        self.run_button.clicked.connect(self._start_colorization)
        action_row.addWidget(self.run_button)
        output_layout.addLayout(action_row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        output_layout.addWidget(self.progress_bar)
        self.elapsed_label = _label("Elapsed: 00:00", muted=True)
        output_layout.addWidget(self.elapsed_label)
        self.progress_detail = _label("All work runs locally on this computer.", muted=True)
        self.progress_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        output_layout.addWidget(self.progress_detail)

        result_row = QHBoxLayout()
        self.open_output_button = QPushButton("Open output folder")
        self.open_output_button.clicked.connect(self._open_output)
        self.open_report_button = QPushButton("Open processing report")
        self.open_report_button.clicked.connect(self._open_report)
        self.open_output_button.hide()
        self.open_report_button.hide()
        result_row.addWidget(self.open_output_button)
        result_row.addWidget(self.open_report_button)
        result_row.addStretch(1)
        self.details_button = QPushButton("Processing details")
        self.details_button.setCheckable(True)
        self.details_button.setStyleSheet("padding: 4px 8px; font-size: 9pt;")
        result_row.addWidget(self.details_button)
        output_layout.addLayout(result_row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        self.log.setMinimumHeight(90)
        self.log.setMaximumHeight(140)
        self.log.setPlaceholderText("Processing details appear here.")
        self.log.hide()
        self.details_button.toggled.connect(self.log.setVisible)
        output_layout.addWidget(self.log)
        layout.addWidget(output_card)
        layout.addStretch(1)
        scroll.setWidget(page)
        self.setCentralWidget(scroll)

    @staticmethod
    def _file_row(layout: QVBoxLayout, title: str, placeholder: str, callback: Any) -> tuple[QLineEdit, QPushButton]:
        row = QHBoxLayout()
        label = _label(title)
        label.setMinimumWidth(95)
        row.addWidget(label)
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setAccessibleName(title)
        edit.setClearButtonEnabled(True)
        edit.setMinimumWidth(0)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(edit, 1)
        button = QPushButton("Browse…")
        button.setAccessibleName(f"Browse {title.lower()}")
        button.clicked.connect(callback)
        row.addWidget(button)
        layout.addLayout(row)
        return edit, button

    def _selection(self) -> SourceSelection:
        return SourceSelection(
            self.source_edit.text().strip(),
            self.las_edit.text().strip() or None,
            self.calibration_edit.text().strip() or None,
        )

    def _apply_theme(self) -> None:
        self.setStyleSheet(_DARK_STYLE if self._dark_mode else _LIGHT_STYLE)
        if hasattr(self, "theme_button"):
            self.theme_button.setIcon(_theme_icon(self._dark_mode))
            self.theme_button.setText("Light mode" if self._dark_mode else "Dark mode")

    @Slot()
    def _toggle_theme(self) -> None:
        self._dark_mode = self.theme_button.isChecked()
        self.settings.setValue("dark_mode", self._dark_mode)
        self._apply_theme()

    @Slot()
    def _update_elapsed(self) -> None:
        if self._run_started_at is None:
            return
        elapsed = max(0, int(time.monotonic() - self._run_started_at))
        minutes, seconds = divmod(elapsed, 60)
        hours, minutes = divmod(minutes, 60)
        value = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
        self.elapsed_label.setText(f"Elapsed: {value}")

    def _show_empty_checklist(self) -> None:
        self.checklist.set_rows([{"label": "Flight inputs", "status": "warning", "detail": "Select a folder to discover point cloud, video, motion, tilt and synchronization data."}])
        self.dependencies.set_rows([{"label": "Local environment", "status": "warning", "detail": "Libraries and video tools are checked together with the flight."}])

    def _browse_source(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose an Inspector flight folder", self.source_edit.text())
        if folder:
            self.source_edit.setText(folder)
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(Path(folder).parent / f"{Path(folder).name}_colorized.las"))

    def _browse_las(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the source point cloud", self.las_edit.text() or self.source_edit.text(), "LAS point clouds (*.las *.laz);;All files (*)")
        if path:
            self.las_edit.setText(path)

    def _browse_calibration(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an RGB camera calibration profile", self.calibration_edit.text() or self.source_edit.text(), "Camera profile (*.json);;All files (*)")
        if path:
            self.calibration_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save the colorized point cloud", self.output_edit.text() or "colorized.las", "LAS point cloud (*.las)", options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            if not Path(path).suffix:
                path += ".las"
            self.output_edit.setText(path)

    @Slot()
    def _inputs_changed(self) -> None:
        self._revision += 1
        self._ready = False
        self._inspected_selection = None
        self._inspection_pending = True
        self.run_button.setEnabled(False)
        if self._job_kind != "run":
            self.state_label.setText("Checking flight data…" if self.source_edit.text().strip() else "Select a flight to begin.")
            self.summary.setText("Waiting for the current selection to be checked.")
        self._debounce.start()

    @Slot()
    def _start_pending_inspection(self) -> None:
        if self._thread is not None:
            self._inspection_pending = True
            return
        self._debounce.stop()
        self._inspection_pending = False
        selection = self._selection()
        if not selection.folder:
            self._show_empty_checklist()
            self.summary.setText("Choose a flight folder to check its inputs.")
            self.state_label.setText("Select a flight to begin.")
            self._update_actions()
            return
        self.state_label.setText("Checking flight data…")
        self.summary.setText("Discovering files and checking local tools…")
        self._launch_worker("inspect", selection)

    def _launch_worker(self, kind: str, selection: SourceSelection, output: str = "") -> None:
        if self._thread is not None:
            raise RuntimeError("Only one service worker may run at a time")
        self._job_kind = kind
        self._job_selection = selection
        self._job_revision = self._revision
        self._cancellation = threading.Event()
        self._thread = QThread(self)
        self._worker = ServiceWorker(self.backend, kind, selection, self._cancellation, output)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._update_actions()
        self._thread.start()

    @Slot(object)
    def _on_result(self, result: dict[str, Any]) -> None:
        if self._job_kind == "inspect":
            if self._job_revision != self._revision or self._job_selection != self._selection():
                return
            self._ready = bool(result.get("ready", False))
            self._inspected_selection = self._job_selection
            self.checklist.set_rows(result.get("checklist", []))
            self.dependencies.set_rows(result.get("dependencies", []))
            self.summary.setText(str(result.get("summary", "")))
            self.state_label.setText("Ready to colorize." if self._ready else "Resolve the required items above to continue.")
        else:
            self._last_result = result
            total = int(result.get("total_points", 0))
            colored = int(result.get("colored_points", 0))
            coverage = f" ({colored / total:.1%})" if total else ""
            self.state_label.setText("Colorization complete.")
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(1000)
            self._update_elapsed()
            self._elapsed_timer.stop()
            self.progress_detail.setText(f"{colored:,} of {total:,} points colored{coverage}. Saved to {result.get('output', self.output_edit.text())}")
            self.log.appendPlainText(self.progress_detail.text())
            self.open_output_button.setVisible(bool(result.get("output")))
            self.open_report_button.setVisible(bool(result.get("report")))

    @Slot(str, str)
    def _on_error(self, message: str, details: str) -> None:
        if self._job_kind == "inspect":
            if self._job_revision != self._revision:
                return
            self._ready = False
            self.state_label.setText("The flight could not be checked.")
            self.summary.setText(message)
            self.checklist.set_rows([{"label": "Flight inspection", "status": "missing", "detail": message}])
        elif self._cancellation.is_set():
            self.state_label.setText("Colorization cancelled.")
            self.progress_detail.setText("Processing stopped. You can change the inputs or run again.")
            self.log.appendPlainText(f"Stopped: {message}")
        else:
            self.state_label.setText("Colorization could not finish.")
            self.progress_detail.setText(message)
            self.log.appendPlainText(details)
        self.progress_bar.setRange(0, 1000)
        self._elapsed_timer.stop()

    @Slot()
    def _thread_finished(self) -> None:
        if self._thread is not None:
            self._thread.wait()
        self._thread = None
        self._worker = None
        self._job_kind = ""
        if self._close_when_idle:
            self.close()
            return
        self._update_actions()
        if self._inspection_pending:
            QTimer.singleShot(0, self._start_pending_inspection)

    @Slot(str, float, str)
    def _on_progress(self, stage: str, fraction: float, message: str) -> None:
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(round(max(0.0, min(1.0, fraction)) * 1000))
        self._update_elapsed()
        if not self._cancellation.is_set():
            self.state_label.setText(stage)
        self.progress_detail.setText(message)
        log_message = f"{stage}: {message}" if stage else message
        if log_message != self._last_progress_message:
            self.log.appendPlainText(log_message)
            self._last_progress_message = log_message

    @Slot()
    def _update_actions(self) -> None:
        running = self._job_kind == "run"
        idle = self._thread is None
        for widget in (self.source_edit, self.source_button, self.las_edit, self.las_button,
                       self.calibration_edit, self.calibration_button, self.output_edit, self.output_button):
            widget.setEnabled(not running)
        self.refresh_button.setEnabled(not running)
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running and not self._cancellation.is_set())
        self.cancel_button.setText("Stopping…" if running and self._cancellation.is_set() else "Cancel")
        output_valid = bool(self.output_edit.text().strip()) and Path(self.output_edit.text().strip()).suffix.lower() == ".las"
        can_run = idle and self._ready and self._inspected_selection == self._selection() and output_valid and not self._inspection_pending
        self.run_button.setEnabled(can_run)
        if not output_valid:
            self.run_button.setToolTip("Choose an output file with the .las extension.")
        else:
            self.run_button.setToolTip("" if can_run else "Complete the flight readiness checks first.")

    @Slot()
    def _start_colorization(self) -> None:
        if not self.run_button.isEnabled():
            return
        output = Path(self.output_edit.text().strip())
        if self._selection().las_override and output.resolve() == Path(self._selection().las_override).resolve():
            QMessageBox.warning(self, "Choose a different output", "The output must be a new file, separate from the source point cloud.")
            return
        if output.exists():
            QMessageBox.warning(self, "Choose a new output file", "That output file already exists. Choose a different name to keep the existing file.")
            return
        self.settings.setValue("source", self.source_edit.text().strip())
        self.settings.setValue("output", str(output))
        self.settings.setValue('calibration', self.calibration_edit.text().strip())
        self._last_result = None
        self._last_progress_message = ""
        self.open_output_button.hide()
        self.open_report_button.hide()
        self.log.clear()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self._run_started_at = time.monotonic()
        self.elapsed_label.setText("Elapsed: 00:00")
        self._elapsed_timer.start()
        self.state_label.setText("Starting colorization…")
        self.progress_detail.setText("Preparing local processing.")
        self._launch_worker("run", self._selection(), str(output))

    @Slot()
    def _cancel(self) -> None:
        if self._job_kind == "run":
            self._cancellation.set()
            self.state_label.setText("Stopping safely…")
            self.progress_detail.setText("Waiting for the current operation to finish and release its files.")
            self._update_actions()

    def _open_output(self) -> None:
        if self._last_result and self._last_result.get("output"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._last_result["output"]).resolve().parent)))

    def _open_report(self) -> None:
        if self._last_result and self._last_result.get("report"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._last_result["report"]).resolve())))

    def _open_calibration_guide(self) -> None:
        root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
        guide = root / 'docs' / 'CALIBRATION.md'
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(guide)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            if self._job_kind == "run" and not self._close_when_idle:
                answer = QMessageBox.question(
                    self, "Stop colorization and close?",
                    "Colorization is still running. Stop processing and close when the current operation has finished?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
            self._close_when_idle = True
            self._debounce.stop()
            self._inspection_pending = False
            self._cancellation.set()
            self.state_label.setText("Finishing the current operation before closing…")
            self._update_actions()
            event.ignore()
            return
        self._debounce.stop()
        event.accept()


def main() -> int:
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("Elios Colorizer")
    application.setOrganizationName("EliosColorizer")
    application.setWindowIcon(_application_icon())
    application.setFont(QFont("Segoe UI", 10))
    application.setStyle("Fusion")
    window = MainWindow()
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(_application_icon(), window)
        tray.setToolTip("Elios Colorizer")
        menu = QMenu()
        show_action = QAction("Show Elios Colorizer", menu)
        show_action.triggered.connect(window.showNormal)
        menu.addAction(show_action)
        menu.addSeparator()
        exit_action = QAction("Exit", menu)
        exit_action.triggered.connect(window.close)
        menu.addAction(exit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: window.showNormal()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        tray.show()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
