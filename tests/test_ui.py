"""Behavioral tests for worker isolation, stale checks, and cancellation."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QTime, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QGroupBox

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

    def run_colorization(self, folder, output, las_override=None, calibration_override=None, *, progress, cancelled, illumination_balancing=False, **kwargs):
        self.illumination_balancing = illumination_balancing
        self.processing_settings = kwargs
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

    def create(backend, **kwargs):
        window = MainWindow(backend, restore_settings=False, **kwargs)
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
    (tmp_path / "result.las").touch();window._update_actions()
    assert not window.run_button.isEnabled()
    (tmp_path / "result.las").unlink();window._update_actions()
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
    assert second.remove_button.text() == "Remove"
    assert second.duplicate_button.text() == "Duplicate"
    assert second.up_button.width() == second.down_button.width() == 38
    assert window.bulk_add_button.text() == "Import Flights…"
    assert not window.bulk_add_button.icon().isNull()
    assert "↻" in window.refresh_button.text()
    assert window.color_balance_check.isEnabled()
    assert window.color_balance_check.text() == "Illumination Balancing"
    assert window.color_balance_check.objectName() == window.distance_check.objectName() == "distanceToggle"
    assert not window.color_balance_check.isChecked()
    window.color_balance_check.setChecked(True)
    assert window._selection().illumination_balancing
    assert not window._inspection_selection().illumination_balancing


def test_responsive_layout_switches_to_two_columns(ui, app):
    window = ui(FakeBackend())
    processing_title = window.processing_card.findChild(QLabel, "sectionTitle")
    assert processing_title.text() == "Choose Processing and Output"
    assert not processing_title.wordWrap()
    window.resize(1000, 850)
    QApplication.processEvents()
    assert not window._wide_layout
    assert window.workflow_layout.getItemPosition(window.workflow_layout.indexOf(window.processing_card))[:2] == (2, 0)
    # Offscreen Qt test screens can clamp top-level window sizes, so exercise
    # the same breakpoint method with the intended viewport width directly.
    window._apply_responsive_layout(1500)
    assert window._wide_layout
    assert window.workflow_layout.getItemPosition(window.workflow_layout.indexOf(window.processing_card)) == (0, 1, 2, 1)


def test_startup_reveals_only_fully_initialized_window(ui, app):
    window = ui(FakeBackend(), startup_hidden=True)
    assert window.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    assert window.windowOpacity() == 0.0
    assert not window.updatesEnabled()
    ui_module._show_fully_initialized(window, app)
    assert window.isVisible()
    assert not window.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    assert window.windowOpacity() == 1.0
    assert window.updatesEnabled()


def test_startup_splash_has_visible_staged_progress(app):
    splash = ui_module.StartupSplash(dark=True)
    try:
        initial = splash.progress.value()
        for _ in range(5):
            splash._advance_progress()
        assert initial < splash.progress.value() < 100
        splash.complete()
        assert splash.progress.value() == 100
        assert splash.status.text() == "Ready"
    finally:
        splash.close()
        splash.deleteLater()


def test_flight_controls_remain_aligned_across_window_sizes(ui, app):
    window = ui(FakeBackend())
    window._add_flight(trigger=False); window._add_flight(trigger=False)
    window.flight_rows[1].set_readiness({"ready": True, "point_count": 12, "video_duration_s": 8})
    window.flight_rows[2].set_readiness({"ready": False, "point_count": 12, "video_duration_s": 8})
    for width in (820, 1100, 1600):
        window.resize(width, 820); window._apply_responsive_layout(width); window.show(); QApplication.processEvents()
        assert len({row.up_button.width() for row in window.flight_rows}) == 1
        assert len({row.duplicate_button.width() for row in window.flight_rows}) == 1
        assert len({row.remove_button.width() for row in window.flight_rows}) == 1
        assert all(row.folder_button.width() == row.las_button.width() == 100 for row in window.flight_rows)
        assert window.scroll_area.horizontalScrollBar().maximum() == 0
    window._apply_responsive_layout(1600); QApplication.processEvents()
    assert window.settings_chips[0].height() < 50
    assert window.processing_plan_label.height() < 70


def test_per_flight_progress_panel_tracks_local_and_aggregate_progress(ui):
    window = ui(FakeBackend())
    window.flight_progress.reset(["Upper Wall", "Lower Wall"])
    window._on_progress("Upper Wall: Assigning RGB", .25,
                        "CUDA GPU 1 · 2 flight jobs active · flight 40.0%")
    upper = window.flight_progress._rows["Upper Wall"]
    assert upper[0].value() == 400
    assert upper[1].text() == "40%"
    assert "Assigning RGB" in upper[2].text()
    assert "flight 40.0%" not in window.progress_detail.text()
    assert window.progress_bar.value() == 250
    window._on_progress("Lower Wall: Assigning RGB", .1,
                        "CUDA GPU 2 · 2 flight jobs active · flight 10.0%")
    assert window.progress_bar.value() == 250
    window.flight_progress.toggle.setChecked(False)
    assert not window.flight_progress.body.isVisible()


def test_flight_cards_support_bulk_add_collapse_duplicate_and_reorder(ui, tmp_path):
    window = ui(FakeBackend())
    first_folder = tmp_path / "Upper"; second_folder = tmp_path / "Lower"
    first_folder.mkdir(); second_folder.mkdir()
    window._add_flight_folders([first_folder, second_folder])
    assert [row.folder_edit.text() for row in window.flight_rows] == [str(first_folder), str(second_folder)]
    assert [row.name_edit.text() for row in window.flight_rows] == ["Flight 1", "Flight 2"]
    replacement = tmp_path / "Replacement"; replacement.mkdir()
    window.flight_rows[0].las_edit.path_dropped.emit(str(replacement))
    assert window.flight_rows[0].las_edit.text() == str(replacement)
    assert len(window.flight_rows) == 2
    field = window.flight_rows[0].folder_edit
    field._set_drop_highlight(True)
    assert field.property("folderDropActive") is True
    field._set_drop_highlight(False)
    window._set_application_drop_highlight(True)
    assert not window.drop_overlay.isHidden()
    assert window.app_shell_layout.contentsMargins().left() == 0
    window._set_application_drop_highlight(False)
    assert window.drop_overlay.isHidden()
    merged = tmp_path / "merged.las"; merged.touch()
    window.merged_edit.path_dropped.emit(str(merged))
    assert window.merged_edit.text() == str(merged)
    first = window.flight_rows[0]
    first.collapse_button.setChecked(False)
    assert first.body.isHidden() and not first.compact_summary.isHidden()
    window._duplicate_flight(first)
    duplicate = window.flight_rows[-1]
    assert duplicate.folder_edit.text() == str(first_folder)
    window._move_flight(duplicate, -1)
    assert window.flight_rows[1] is duplicate


def test_sticky_focus_preflight_settings_and_output_preview(ui, tmp_path):
    window = ui(FakeBackend())
    window._last_inspection_result = {
        "point_count": 2_000_000, "video_duration_s": 120, "estimated_views": 120}
    window.advanced_settings = ui_module.AdvancedProcessingSettings(5, 4, 8, True, 10, 30, True)
    output = tmp_path / "result.las"
    window.output_edit.setText(str(output));window._update_processing_summaries()
    assert [chip.text() for chip in window.settings_chips] == ["5 fps", "Exclude outer 4%", "Strong blur", "Fusion On", "00:00:10–00:00:30"]
    assert "approximately 100 sampled frames" in window.preflight_summary.text()
    assert "Will create: result.las" in window.output_preview.text()
    assert window.sticky_bar.parent() is window.centralWidget()
    window._apply_responsive_layout(1600)
    before = window.workflow_layout.getItemPosition(window.workflow_layout.indexOf(window.processing_card))
    window._set_processing_focus(True)
    assert not window.flight_card.isHidden() and not window.check_card.isHidden()
    assert not window.processing_card.isHidden() and not window.sticky_bar.isHidden()
    assert window.workflow_layout.getItemPosition(window.workflow_layout.indexOf(window.processing_card)) == before
    window._set_processing_focus(False)
    output.touch();window._update_processing_summaries()
    assert "Conflict:" in window.output_preview.text()


def test_actionable_readiness_results_and_completed_flight_action(ui, tmp_path):
    window = ui(FakeBackend());second = window._add_flight(trigger=False)
    second.collapse_button.setChecked(False)
    window._navigate_to_check({"label": "Existing point cloud", "detail": "Flight 2: missing"})
    assert second.collapse_button.isChecked() and window.focusWidget() is second.las_edit
    output = tmp_path / "lower.las";output.touch()
    window.flight_progress.reset(["Upper", "Lower"], {"Lower": output})
    window.flight_progress.update_flight("Lower", 1, "Complete")
    assert window.flight_progress._rows["Lower"][3].isEnabled()
    report = tmp_path / "report.json"
    report.write_text('{"summary":{"elapsed_seconds":65,"warnings_count":2,"frames":{"used":50,"rejected":3}}}')
    window._show_result_summary({"output":str(output),"report":str(report),"colored_points":75,"total_points":100,"processing_strategy":"parallel_separate_outputs"})
    assert not window.result_panel.isHidden()
    assert "75.0%" in window.result_headline.text()
    assert "50 frames used" in window.result_summary.text()


def test_advanced_dialog_is_grouped_and_summarizes_active_values(ui):
    dialog = ui_module.AdvancedProcessingDialog(ui_module.AdvancedProcessingSettings(5, 4, 8, True, 10, 30, True))
    headings = [label.text() for label in dialog.findChildren(QLabel) if label.objectName() == "dialogSectionTitle"]
    assert headings == ["Processing preset", "Frame selection", "Color quality", "Video time range"]
    assert "5 fps" in dialog.active_summary.text() and "Fusion On" in dialog.active_summary.text()
    dialog.deleteLater()


def test_advanced_processing_defaults_resets_and_selection(ui):
    window = ui(FakeBackend())
    dialog = ui_module.AdvancedProcessingDialog(ui_module.AdvancedProcessingSettings(4, 12, 8), window)
    assert dialog.sample_frequency.minimum() == .25 and dialog.sample_frequency.maximum() == 30
    dialog.findChild(QPushButton, 'sampleFrequencyIncreaseButton').click()
    assert dialog.sample_frequency.value() == 4.25
    dialog.findChild(QPushButton, 'edgeExclusionDecreaseButton').click()
    assert dialog.edge_exclusion.value() == 11

    dialog.sample_frequency.setValue(4);dialog.edge_exclusion.setValue(12)
    assert dialog.values() == ui_module.AdvancedProcessingSettings(4, 12, 8)
    dialog.reset_defaults()
    assert dialog.values() == ui_module.AdvancedProcessingSettings()
    window.advanced_settings = ui_module.AdvancedProcessingSettings(2, 5, 0)
    selection = window._selection()
    assert selection.advanced.sample_frequency_hz == 2
    assert selection.advanced.image_edge_exclusion_percent == 5
    assert window._inspection_selection().advanced == ui_module.AdvancedProcessingSettings()


def test_resource_switch_is_clickable_across_entire_track(app):
    switch = ui_module.ToggleSwitch()
    switch.show()
    assert not switch.isChecked()
    QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(switch.width()-3,switch.height()//2))
    assert switch.isChecked()
    switch.close();switch.deleteLater()


def test_advanced_presets_and_time_range_controls(ui):
    window = ui(FakeBackend())
    settings = ui_module.AdvancedProcessingSettings(5, 4, 8, True, 15, 75, True)
    dialog = ui_module.AdvancedProcessingDialog(settings, window, available_duration_s=125)
    assert dialog.time_range_check.isChecked()
    assert dialog.start_time.time() == QTime(0, 0, 15)
    assert dialog.end_time.time() == QTime(0, 1, 15)
    assert dialog.end_time.isEnabled()
    assert dialog.fusion_check.isChecked()
    assert dialog.save_preset("Glare wall") == "Glare wall"
    assert dialog.presets()["Glare wall"] == settings
    dialog.reset_defaults()
    assert dialog.values() == ui_module.AdvancedProcessingSettings()
    index = dialog.preset_combo.findData("Glare wall")
    dialog.preset_combo.setCurrentIndex(index)
    assert dialog.values() == settings
    assert dialog.delete_preset("Glare wall")
    assert not dialog.presets()
    with pytest.raises(ValueError, match="1–48"):
        dialog.save_preset("")


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
    window.color_balance_check.setChecked(True)
    window.advanced_settings = ui_module.AdvancedProcessingSettings(2, 5, 8)
    window._start_colorization()
    assert not window.source_edit.isEnabled()
    assert not window.run_button.isEnabled()
    wait_until(lambda: window._thread is None)
    assert backend.illumination_balancing
    assert backend.processing_settings == {
        'sample_interval_s': .5, 'image_border_fraction': .05, 'minimum_sharpness': 8,
        'resource_settings': ui_module.ResourcePreferences().to_dict()}
    assert backend.last_run == ("flight", str(output), "source.las", "camera.json")
    assert window.progress_bar.value() == 1000
    assert window.progress_badge.text() == "100%"
    assert "75.0%" in window.progress_detail.text()
    assert window.state_label.text() == "Colorization complete."
    assert window.eta_label.text() == "Estimated time left: Complete"
    assert window.source_edit.isEnabled()


def test_processing_receives_enabled_time_range(ui, tmp_path):
    backend = FakeBackend()
    window = ui(backend)
    prepare(window, tmp_path / "range.las")
    window.advanced_settings = ui_module.AdvancedProcessingSettings(5, 2, 2, True, 10, 25, True)
    window._start_colorization()
    wait_until(lambda: window._thread is None)
    assert backend.processing_settings["start_s"] == 10
    assert backend.processing_settings["end_s"] == 25
    assert backend.processing_settings["multi_frame_fusion"] is True


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

    scroll = window.scroll_area
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


def test_camera_theme_and_resource_preferences_are_restored_between_sessions(app, monkeypatch):
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
            "resource_preferences_json": '{"use_recommended":false,"cpu_percent":50,"memory_percent":45,"cuda_enabled":false,"max_gpus":2,"vram_percent":65,"concurrent_flights":3,"process_priority":"low"}',
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
        assert window.resource_preferences == ui_module.ResourcePreferences(False,50,45,False,2,65,3,"low")
        window.source_edit.setText("new-flight")
        window.las_edit.setText("new.las")
        window.output_edit.setText("new-output.las")
        window.calibration_edit.setText("new-camera.json")
        window._save_settings()
        assert MemorySettings.values == {
            "calibration": "new-camera.json", "dark_mode": True,
            "resource_preferences_json": '{"concurrent_flights":3,"cpu_percent":50,"cuda_enabled":false,"max_gpus":2,"memory_percent":45,"process_priority":"low","use_recommended":false,"vram_percent":65}'}
    finally:
        window._debounce.stop()
        window.close()
        window.deleteLater()
        QApplication.processEvents()
