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
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import elios_colorizer.ui as ui_module
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

    def inspect_sources(self, flights, calibration_override=None, **kwargs):
        self.calls.append(tuple(item["folder"] for item in flights))
        return {"ready": True, "checklist": [{"label": "All flights", "status": "ok", "detail": "Ready"}],
                "dependencies": [], "summary": f"{len(flights)} flights"}

    def run_workflow(self, flights, output, calibration_override=None, *, mode,
                     maximum_color_distance_m, progress, cancelled, **kwargs):
        self.last_run = (flights, output, calibration_override, mode, maximum_color_distance_m)
        self.run_started.set()
        progress("Workflow", .5, "Working")
        return {"output": output, "report": str(Path(output) / "report.json"),
                "colored_points": 150, "total_points": 200}


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


def test_selecting_inputs_does_not_populate_output(ui):
    window = ui(FakeBackend())
    window.source_edit.setText("flight")
    window.las_edit.setText("source.las")
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    assert window.output_edit.text() == ""
    assert not window.run_button.isEnabled()


def test_action_buttons_and_color_balance_placeholder(ui):
    window = ui(FakeBackend())
    second = window._add_flight(trigger=False)
    assert window.add_flight_button.objectName() == "addFlightButton"
    assert "Add flight" in window.add_flight_button.text()
    assert second.remove_button.objectName() == "removeFlightButton"
    assert "✕" in second.remove_button.text() and "Remove" in second.remove_button.text()
    assert "↻" in window.refresh_button.text()
    assert not window.color_balance_check.isEnabled()
    assert window.color_balance_check.text() == "Apply color balancing"
    assert window.color_balance_badge.text() == "COMING SOON"
    assert "future update" in window.color_balance_check.toolTip()


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
    assert window.progress_badge.text() == "100%"
    assert "75.0%" in window.progress_detail.text()
    assert window.state_label.text() == "Colorization complete."
    assert window.eta_label.text() == "Estimated time left: Complete"
    assert window.source_edit.isEnabled()


def test_estimated_time_left_updates_during_processing(ui):
    window = ui(FakeBackend())
    window._run_started_at = time.monotonic() - 100
    window._on_progress("Validating alignment", .2, "Checking flights")
    assert "Waiting for colorization" in window.eta_label.text()
    now = time.monotonic()
    window._eta_samples = [(now - 12, .40), (now - 6, .45)]
    window._on_progress("Assigning RGB", .5, "Halfway through planned views")
    first = window.eta_label.text()
    assert first.startswith("Estimated time left: ")
    assert "Calculating" not in first
    assert window._elapsed_timer.interval() == 1000
    window._eta_as_of -= 5
    window._update_elapsed()
    assert window.eta_label.text() != first


def test_warning_details_are_collapsed_and_expandable(ui):
    window = ui(FakeBackend())
    window.checklist.set_rows([{"label":"Warnings","status":"warning","detail":"2 source notes.",
                                "details":["Upper: first note","Lower: second note"]}])
    button = next(item for item in window.checklist.findChildren(QPushButton)
                  if item.objectName() == "warningToggle")
    details = next(label for label in window.checklist.findChildren(QLabel)
                   if "Upper: first note" in label.text())
    assert details.isHidden()
    button.click(); QApplication.processEvents()
    assert not details.isHidden()


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


def test_disk_space_recommendation_is_workflow_aware_and_can_block_start(ui, tmp_path, monkeypatch):
    backend = FakeBackend();window = ui(backend)
    prepare(window, tmp_path / "result.las")
    window._disk_space_allows_start = lambda *_: False
    window._start_colorization()
    assert not backend.run_started.is_set()
    window._disk_space_allows_start = MainWindow._disk_space_allows_start.__get__(window, MainWindow)

    second = window._add_flight(trigger=False)
    selection = window._selection()
    gib = 1024**3
    window.mode_combo.setCurrentIndex(1)
    window.alignment_combo.setCurrentIndex(0)
    assert window._recommended_free_space_bytes(window._selection()) == 25*gib
    window.alignment_combo.setCurrentIndex(1)
    assert window._recommended_free_space_bytes(window._selection()) == 30*gib

    seen = []
    monkeypatch.setattr(ui_module.shutil, "disk_usage", lambda _path: type("Usage", (), {"free": 10*gib})())
    window._show_low_disk_warning = lambda free, recommended, location: seen.append((free,recommended,location)) or True
    assert window._disk_space_allows_start(window._selection(), tmp_path / "merged.las")
    assert seen[0][0:2] == (10*gib,30*gib)


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


def test_multi_flight_modes_and_optional_distance(ui, tmp_path):
    backend = FakeBackend()
    window = ui(backend)
    assert not window.mode_combo.isVisible()
    assert not window.distance_spin.isEnabled()
    window.distance_check.setChecked(True)
    assert window.distance_spin.isEnabled()
    window.distance_check.setChecked(False)
    second = window._add_flight()
    window.show()
    QApplication.processEvents()
    assert window.flight_rows[0].name_edit.text() == "Flight 1"
    assert second.name_edit.text() == "Flight 2"
    assert second.name_edit.maxLength() == 48
    assert window.mode_combo.isVisible()
    window.source_edit.setText("flight-one")
    window.las_edit.setText("one.las")
    second.folder_edit.setText("flight-two")
    second.las_edit.setText("two.las")
    window.calibration_edit.setText("shared.json")
    output = tmp_path / "separate"
    window.output_edit.setText(str(output))
    window.distance_check.setChecked(True)
    window.distance_spin.setValue(6.5)
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    assert window.run_button.isEnabled()
    window._start_colorization()
    wait_until(lambda: window._thread is None)
    flights, saved, calibration, mode, distance = backend.last_run
    assert [item["folder"] for item in flights] == ["flight-one", "flight-two"]
    assert (saved, calibration, mode, distance) == (str(output), "shared.json", "separate", 6.5)
    window.mode_combo.setCurrentIndex(1)
    assert "closely aligned" in window.mode_help.text()
    assert window.alignment_panel.isVisible()
    assert window.manual_panel.isVisible()
    assert window.flight_rows[0].transform_edit.isVisible()
    window.flight_rows[0].name_edit.setText("Bottom Cap")
    window._renumber()
    assert window.flight_rows[0].name_edit.text() == "Bottom Cap"
    assert second.name_edit.text() == "Flight 2"
    window.flight_rows[0].transform_source.setCurrentIndex(1)
    assert window.flight_rows[0].matrix_text.isVisible()
    assert window.flight_rows[0].selection().name == "Bottom Cap"
    assert window.flight_rows[0].selection().transform_values.startswith("1 0 0 0")
    window.alignment_combo.setCurrentIndex(1)
    assert window.automatic_panel.isVisible()
    assert not window.flight_rows[0].transform_edit.isVisible()
    assert not window.run_button.isEnabled()  # merged mode needs a .las output
    window._remove_flight(second)
    QApplication.processEvents()
    assert window.mode_combo.currentData() == "separate"
    assert not window.mode_combo.isVisible()


def test_duplicate_flight_names_block_readiness(ui, tmp_path):
    window = ui(FakeBackend())
    second = window._add_flight()
    window.flight_rows[0].name_edit.setText("Tank floor")
    second.name_edit.setText("  TANK   FLOOR ")
    window.source_edit.setText("first")
    second.folder_edit.setText("second")
    window.output_edit.setText(str(tmp_path / "results"))
    window._start_pending_inspection()
    assert not window.run_button.isEnabled()
    assert all(row.name_edit.property("duplicateName") for row in window.flight_rows)
    assert window.checklist.rows[0]["label"] == "Flight names"
    second.name_edit.setText("Tank wall")
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    assert not any(row.name_edit.property("duplicateName") for row in window.flight_rows)


def test_only_camera_calibration_and_theme_are_restored_between_sessions(app, monkeypatch):
    class MemorySettings:
        values = {
            "flights": '[{"folder":"old-flight","las_override":"old.las"}]',
            "source": "old-flight",
            "output": "old-output.las",
            "calibration": "saved-camera.json",
            "mode": "merge",
            "distance_enabled": True,
            "distance_m": 12.0,
            "dark_mode": True,
        }

        def __init__(self, *_): pass
        def value(self, key, default=None, **_): return self.values.get(key, default)
        def setValue(self, key, value): self.values[key] = value
        def remove(self, key): self.values.pop(key, None)

    monkeypatch.setattr(ui_module, "QSettings", MemorySettings)
    window = MainWindow(FakeBackend(), restore_settings=True)
    try:
        assert len(window.flight_rows) == 1
        assert window.source_edit.text() == ""
        assert window.las_edit.text() == ""
        assert window.output_edit.text() == ""
        assert window.calibration_edit.text() == "saved-camera.json"
        assert window.mode_combo.currentData() == "separate"
        assert not window.distance_check.isChecked()
        assert window._dark_mode
        assert window.theme_button.isChecked()
        window.source_edit.setText("new-flight")
        window.las_edit.setText("new.las")
        window.output_edit.setText("new-output.las")
        window.calibration_edit.setText("new-camera.json")
        window._save_settings()
        assert MemorySettings.values == {"calibration": "new-camera.json", "dark_mode": True}
    finally:
        window._debounce.stop()
        window.close()
        window.deleteLater()
        QApplication.processEvents()
