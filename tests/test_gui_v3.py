"""Structural and behavioral checks for the production 3.0 GUI."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from elios_colorizer.gui import ApplicationWindow
from elios_colorizer.gui.core import ArtifactRegistry, ColorizedPointCloudArtifact, ToolDescriptor, ToolRegistry
from elios_colorizer.gui.design_system import ToggleSwitch


class PassiveBackend:
    def inspect_source(self, folder, las_override=None, calibration_override=None):
        return {"ready": True, "checklist": [], "dependencies": [], "summary": "Ready"}

    def run_colorization(self, folder, output, las_override=None, calibration_override=None,
                         *, progress, cancelled, **_kwargs):
        progress("Assigning RGB", .75, "Applying observations")
        return {"output": output, "report": f"{output}.json", "colored_points": 90, "total_points": 100}


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("GUI condition did not become true")
        QApplication.processEvents()
        time.sleep(.01)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    instance = ApplicationWindow(PassiveBackend(), restore_settings=False)
    yield instance
    instance._cancellation.set()
    instance.close()
    instance.deleteLater()
    QApplication.processEvents()


def test_registry_rejects_duplicate_tool_ids(app):
    registry = ToolRegistry()
    descriptor = ToolDescriptor("example", "Example", "Test", lambda owner: owner)
    registry.register(descriptor)
    assert registry.get("example") is descriptor
    with pytest.raises(ValueError):
        registry.register(descriptor)


def test_artifacts_are_typed_and_keep_completion_order(tmp_path):
    registry = ArtifactRegistry()
    first = ColorizedPointCloudArtifact(tmp_path / "first.las", colored_points=75, total_points=100)
    second = ColorizedPointCloudArtifact(tmp_path / "second.las", source_flights=("North",))
    registry.add(first)
    registry.add(second)
    assert first.coverage == .75
    assert registry.all() == (first, second)
    assert registry.latest() is second
    with pytest.raises(ValueError):
        registry.add(first)


def test_shell_registers_colorization_and_preserves_future_extension_point(window):
    assert [tool.id for tool in window.tool_registry.descriptors()] == ["colorization"]
    assert window.tool_buttons["colorization"].isChecked()
    assert window.tool_host.currentWidget() is window.colorization_workspace
    assert isinstance(window.theme_button, ToggleSwitch)
    assert window.theme_sun_icon.pixmap() and not window.theme_sun_icon.pixmap().isNull()
    assert window.theme_moon_icon.pixmap() and not window.theme_moon_icon.pixmap().isNull()
    assert window.findChild(type(window.summary_rail), "toolRail") is not None
    assert window.windowTitle() == "Elios Colorizer"
    assert window.version_label.text() == "Version 3.0.0"


def test_workflow_navigation_reuses_functional_cards(window):
    workspace = window.colorization_workspace
    assert workspace.current_page_id == "inputs"
    assert workspace.pages.widget(0).isAncestorOf(window.flight_card)
    workspace.set_page("validation")
    assert workspace.current_page_id == "validation"
    assert workspace.pages.currentWidget().isAncestorOf(window.check_card)
    window.source_edit.setText("flight")
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    workspace.set_page("processing")
    assert workspace.pages.currentWidget().isAncestorOf(window.processing_card)
    workspace.set_page("results")
    assert workspace.current_page_id == "results"
    assert workspace.pages.currentWidget().isAncestorOf(window.result_panel)


def test_run_and_persistent_summary_follow_configuration(window, tmp_path):
    window.flight_rows[0].name_edit.setText("North wall")
    window.source_edit.setText("flight")
    window.output_edit.setText(str(tmp_path / "colored.las"))
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    window.colorization_workspace.set_page("run")
    assert "North wall" in window.colorization_workspace.review_inputs.text()
    assert str(tmp_path / "colored.las") in window.colorization_workspace.review_outputs.text()
    assert window.summary_flights.text() == "1 flight"
    assert window.summary_output.text() == str(tmp_path / "colored.las")


def test_summary_rail_collapses_at_compact_width(window, app):
    window.show()
    window.resize(1000, 760)
    app.processEvents()
    assert not window.summary_rail.isVisibleTo(window)
    window.resize(1300, 760)
    app.processEvents()
    assert window.summary_rail.isVisibleTo(window)


def test_completed_result_is_available_for_future_tools(window, tmp_path):
    window.flight_rows[0].name_edit.setText("North wall")
    window._record_result_artifact({
        "output": str(tmp_path / "colored.las"),
        "report": str(tmp_path / "report.json"),
        "colored_points": 90,
        "total_points": 100,
    })
    artifact = window.artifacts.latest()
    assert artifact is not None
    assert artifact.path == tmp_path / "colored.las"
    assert artifact.source_flights == ("North wall",)
    assert artifact.coverage == .9
    window._last_result = {"output": str(artifact.path)}
    window.colorization_workspace.show_result()
    assert window.colorization_workspace.current_page_id == "results"
    assert not window.colorization_workspace.results_empty.isVisibleTo(window.colorization_workspace)


def test_light_and_dark_design_system_cover_dialogs_and_sliders(window):
    window._dark_mode = False
    window.theme_button.setChecked(False)
    window._apply_theme()
    assert "#e7ecef" in window.styleSheet()
    assert "QDialog QSlider::handle:horizontal" in window.styleSheet()
    window._dark_mode = True
    window.theme_button.setChecked(True)
    window._apply_theme()
    assert "#111922" in window.styleSheet()
    assert window.theme_button.isChecked()


def test_production_shell_runs_unchanged_colorization_boundary(window, tmp_path):
    window.source_edit.setText("flight")
    window.output_edit.setText(str(tmp_path / "colored.las"))
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    assert window.run_button.isEnabled()
    window.colorization_workspace.set_page("run")
    assert window.run_button.isEnabled()
    window.run_button.click()
    wait_until(lambda: window._thread is None)
    assert window.colorization_workspace.current_page_id == "results"
    assert window._last_result["colored_points"] == 90
    assert window.artifacts.latest().path == tmp_path / "colored.las"


def test_run_review_uses_start_blockers_not_running_button_state(window, tmp_path):
    window.source_edit.setText("flight")
    window.output_edit.setText(str(tmp_path / "ready.las"))
    window._debounce.stop()
    window._ready = True
    window._inspected_selection = window._inspection_selection()
    window._inspection_pending = False
    window._update_actions()
    workspace = window.colorization_workspace
    workspace.set_page("run")
    assert workspace.review_status.text() == "Ready to colorize"
    assert window.progress_badge.width() == 64
    window._job_kind = "run"
    window._update_actions()
    assert workspace.review_status.text() == "Colorization in progress"
    window._job_kind = ""
    window._update_actions()


def test_failed_and_cancelled_runs_have_explicit_result_states(window):
    workspace = window.colorization_workspace
    workspace.show_failure("Synthetic failure")
    assert workspace.current_page_id == "results"
    assert workspace.results_title.text() == "Colorization could not finish"
    assert workspace.results_note.text() == "Synthetic failure"
    workspace.show_failure("Processing stopped safely.", cancelled=True)
    assert workspace.results_title.text() == "Colorization cancelled"


def test_secondary_calibration_entry_obeys_busy_state(window):
    window._job_kind = "run"
    window._update_actions()
    assert not window.calibration_workbench_button.isEnabled()
    window._job_kind = ""
    window._update_actions()
    assert window.calibration_workbench_button.isEnabled()


def test_validation_blocks_processing_until_checks_pass(window):
    workspace = window.colorization_workspace
    workspace.set_page("processing")
    assert workspace.current_page_id == "validation"
    assert not workspace.navigation_buttons["processing"].isEnabled()
    window.source_edit.setText("flight")
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    workspace.refresh_access()
    assert workspace.navigation_buttons["processing"].isEnabled()
    workspace.set_page("processing")
    assert workspace.current_page_id == "processing"


def test_merged_input_and_advanced_settings_are_inline(window):
    workspace = window.colorization_workspace
    assert workspace.pages.widget(0).isAncestorOf(window.alignment_panel)
    assert workspace.pages.widget(2).isAncestorOf(workspace.advanced_panel)
    assert workspace.advanced_panel.preset_combo.currentData() == "__default__"
    assert window.advanced_settings.sample_frequency_hz == 1.0


def test_processing_settings_do_not_invalidate_passed_input_validation(window):
    window.source_edit.setText("flight")
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    inspected = window._inspected_selection
    window.colorization_workspace.set_page("processing")
    window.distance_check.setChecked(True)
    window.distance_spin.setValue(12.5)
    window.color_balance_check.setChecked(True)
    window.colorization_workspace.advanced_panel.sample_frequency.setValue(2.0)
    window._update_actions()
    assert window._inspected_selection == inspected
    assert window.colorization_workspace.current_page_id == "processing"
    assert window.colorization_workspace.validation_passed()


def test_run_output_summary_marks_missing_destination(window):
    window.source_edit.setText("flight")
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    window.output_edit.clear()
    window.colorization_workspace.set_page("run")
    assert window.colorization_workspace.review_outputs.property("missingOutput") is True


def test_mouse_wheel_scrolls_page_without_changing_processing_controls(window, app):
    window.source_edit.setText("flight")
    window._start_pending_inspection()
    wait_until(lambda: window._thread is None)
    window.colorization_workspace.set_page("processing")
    window.resize(1000, 680)
    window.show()
    app.processEvents()
    spin = window.colorization_workspace.advanced_panel.sample_frequency
    scroll = window.colorization_workspace._scrolls[2].verticalScrollBar()
    original_value = spin.value()
    original_scroll = scroll.value()
    event = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    QApplication.sendEvent(spin, event)
    app.processEvents()
    assert spin.value() == original_value
    assert scroll.value() > original_scroll
