"""Behavioral tests for worker isolation, stale checks, and cancellation."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from elios_colorizer.ui import MainWindow


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("GUI condition did not become true")
        QApplication.processEvents()
        # Release Python's GIL so the Python QThread worker can make progress.
        time.sleep(.01)


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.block_first = False
        self.run_started = threading.Event()
        self.run_cancelled = threading.Event()
        self.block_run = False
        self.last_run = None

    def inspect_source(self, folder, las_override=None, calibration_override=None):
        self.calls.append(folder)
        if len(self.calls) == 1:
            self.first_started.set()
            if self.block_first:
                if not self.release_first.wait(4):
                    raise RuntimeError("Test did not release first inspection")
        return {
            "ready": folder != "missing",
            "checklist": [{"label": "Flight", "status": "ok", "detail": folder}],
            "dependencies": [{"label": "Video", "status": "ok", "detail": "Ready"}],
            "summary": folder,
        }

    def run_colorization(self, folder, output, las_override=None, calibration_override=None, *, progress, cancelled):
        self.last_run = (folder, output, las_override, calibration_override)
        self.run_started.set()
        progress("Projecting", 0.5, "Projecting visible points")
        if self.block_run:
            for _ in range(400):
                if cancelled():
                    self.run_cancelled.set()
                    raise RuntimeError("Cancelled by user")
                threading.Event().wait(0.01)
            raise RuntimeError("Test did not cancel processing")
        return {"output": output, "report": str(Path(output).with_suffix(".json")), "colored_points": 75, "total_points": 100}


@pytest.fixture
def ui(app):
    windows = []

    def create(backend):
        window = MainWindow(backend, restore_settings=False)
        windows.append(window)
        return window

    yield create
    for window in windows:
        window.backend.release_first.set()
        window._cancellation.set()
        wait_until(lambda: window._thread is None)
        window.close()
        window.deleteLater()
    QApplication.processEvents()


def prepare(window, output):
    window.source_edit.setText("flight")
    window.output_edit.setText(str(output))
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)


def test_ready_requires_validated_inputs_and_las_output(ui, tmp_path):
    window = ui(FakeBackend())
    assert not window.run_button.isEnabled()
    prepare(window, tmp_path / "result.las")
    assert window.run_button.isEnabled()
    window.output_edit.setText(str(tmp_path / "result.e57"))
    assert not window.run_button.isEnabled()
    window.output_edit.setText(str(tmp_path / "result.las"))
    assert window.run_button.isEnabled()
    window.calibration_edit.setText("new-camera.json")
    assert not window.run_button.isEnabled()


def test_old_inspection_cannot_enable_new_source(ui, tmp_path):
    backend = FakeBackend()
    backend.block_first = True
    window = ui(backend)
    window.source_edit.setText("old-flight")
    window.output_edit.setText(str(tmp_path / "result.las"))
    window._start_pending_inspection()
    wait_until(backend.first_started.is_set)
    window.source_edit.setText("new-flight")
    assert not window.run_button.isEnabled()
    backend.release_first.set()
    wait_until(lambda: backend.calls == ["old-flight", "new-flight"] and window._thread is None)
    assert window.summary.text() == "new-flight"
    assert window.run_button.isEnabled()
    QTest.qWait(400)
    assert backend.calls == ["old-flight", "new-flight"]


def test_processing_receives_inputs_and_reports_coverage(ui, tmp_path):
    backend = FakeBackend()
    window = ui(backend)
    window.las_edit.setText("source.las")
    window.calibration_edit.setText("camera.json")
    output = tmp_path / "result.las"
    prepare(window, output)
    window._start_colorization()
    assert not window.source_edit.isEnabled()
    assert not window.run_button.isEnabled()
    wait_until(lambda: window._thread is None)
    assert backend.last_run == ("flight", str(output), "source.las", "camera.json")
    assert window.progress_bar.value() == 1000
    assert "75.0%" in window.progress_detail.text()
    assert window.state_label.text() == "Colorization complete."
    assert window.source_edit.isEnabled()


def test_cancel_stays_responsive_and_waits_for_worker(ui, tmp_path):
    backend = FakeBackend()
    backend.block_run = True
    window = ui(backend)
    prepare(window, tmp_path / "result.las")
    window._start_colorization()
    wait_until(backend.run_started.is_set)
    window._cancel()
    assert window._cancellation.is_set()
    assert not window.cancel_button.isEnabled()
    wait_until(lambda: window._thread is None)
    assert backend.run_cancelled.is_set()
    assert window.state_label.text() == "Colorization cancelled."
    assert window.run_button.isEnabled()


def test_missing_inputs_block_processing(ui, tmp_path):
    window = ui(FakeBackend())
    window.source_edit.setText("missing")
    window.output_edit.setText(str(tmp_path / "result.las"))
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    assert not window.run_button.isEnabled()
    window._start_colorization()
    assert not window.backend.run_started.is_set()


def test_long_paths_stay_inside_panels(ui):
    window = ui(FakeBackend())
    window.resize(820, 680)
    long_path = 'C:/' + '/'.join(['very_long_flight_folder_name'] * 14) + '/flight_obcbag.mcap'
    window.source_edit.setText(long_path)
    window.checklist.set_rows([
        {'label': 'Position and orientation', 'status': 'ok', 'detail': long_path},
        {'label': 'Camera tilt and video timing', 'status': 'ok', 'detail': long_path},
    ])
    window.dependencies.set_rows([
        {'label': 'A deliberately long dependency label', 'status': 'ok', 'detail': '1.2.3'}
    ])
    window.show()
    QApplication.processEvents()

    scroll = window.centralWidget()
    assert scroll.horizontalScrollBar().maximum() == 0
    assert any('\u200b' in label.text() for label in window.checklist.findChildren(QLabel))
