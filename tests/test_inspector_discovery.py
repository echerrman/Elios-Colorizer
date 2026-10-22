from __future__ import annotations

import json
from pathlib import Path

import pytest

from elios_colorizer.inspector_discovery import (
    InspectorDiscoveryError, InspectorFlight, discover_asset, flight_feature_available,
    match_export_folders, resolve_workspace_root)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_discovers_only_id_linked_inspections_and_flights(tmp_path):
    workspace = tmp_path / "Main Workspace"
    asset = workspace / "assets" / "asset-1"
    write_json(asset / "asset.json", {"id": "asset-1", "name": "Bow Tank"})
    write_json(workspace / "inspections" / "inspection-a" / "inspection.json",
               {"id": "inspection-a", "asset_id": "asset-1", "name": "August 6"})
    write_json(workspace / "inspections" / "other" / "inspection.json",
               {"id": "inspection-other", "asset_id": "asset-2", "name": "Other"})
    write_json(workspace / "flights" / "flight-1" / "flight.json", {
        "id": "flight-1", "inspection_id": "inspection-a", "name": "Flight 1",
        "datetime_utc": "2026-08-06T12:00:00Z", "duration": 91,
        "flags": {"thermal_available": True}})
    write_json(workspace / "flights" / "orphan" / "flight.json",
               {"id": "orphan", "inspection_id": "missing", "name": "Orphan"})

    result = discover_asset(asset)

    assert resolve_workspace_root(asset) == workspace.resolve()
    assert result.name == "Bow Tank"
    assert result.flight_count == 1
    assert [item.name for item in result.inspections] == ["August 6"]
    flight = result.inspections[0].flights[0]
    assert flight.folder_path == (workspace / "flights" / "flight-1").resolve()
    assert flight_feature_available(flight, "thermal") is True


def test_bad_unrelated_metadata_is_skipped_with_warnings(tmp_path):
    workspace = tmp_path / "Workspace"
    asset = workspace / "assets" / "asset-1"
    write_json(asset / "asset.json", {"id": "asset-1", "name": "Asset"})
    malformed = workspace / "inspections" / "bad" / "inspection.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("not json", encoding="utf-8")
    write_json(workspace / "inspections" / "empty" / "inspection.json",
               {"id": "inspection-a", "asset_id": "asset-1", "name": "Empty"})
    write_json(workspace / "flights" / "bad" / "flight.json", {"id": "missing-link"})

    result = discover_asset(asset)

    assert result.flight_count == 0
    assert any("Skipped bad" in warning for warning in result.warnings)
    assert any("no matching flights" in warning for warning in result.warnings)


def test_rejects_non_asset_and_asset_outside_workspace(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(InspectorDiscoveryError, match="asset.json"):
        discover_asset(plain)
    write_json(plain / "asset.json", {"id": "asset-1", "name": "Asset"})
    with pytest.raises(InspectorDiscoveryError, match="'assets' folder"):
        discover_asset(plain)


def test_missing_workspace_collections_returns_useful_warnings(tmp_path):
    asset = tmp_path / "Workspace" / "assets" / "asset-1"
    write_json(asset / "asset.json", {"id": "asset-1", "name": "Asset"})

    result = discover_asset(asset)

    assert result.inspections == ()
    assert any("Inspection folder not found" in warning for warning in result.warnings)
    assert any("Flight folder not found" in warning for warning in result.warnings)


def test_matches_default_inspector_export_folder_suffixes(tmp_path):
    exports = tmp_path / "Exports"
    (exports / "Tank Survey - Flight 1").mkdir(parents=True)
    (exports / "Tank Survey - Flight 6").mkdir()
    (exports / "unrelated").mkdir()
    flights = [
        InspectorFlight("one", "inspection", "Flight 1", tmp_path / "native-one"),
        InspectorFlight("six", "inspection", "Flight 6", tmp_path / "native-six"),
    ]

    matches, problems = match_export_folders(flights, exports)

    assert problems == []
    assert matches["one"].name == "Tank Survey - Flight 1"
    assert matches["six"].name == "Tank Survey - Flight 6"


def test_export_matching_returns_partial_results_for_manual_fallback(tmp_path):
    exports = tmp_path / "Exports"
    (exports / "Inspection - Flight 1").mkdir(parents=True)
    flights = [
        InspectorFlight("one", "inspection", "Flight 1", tmp_path / "native-one"),
        InspectorFlight("two", "inspection", "Flight 2", tmp_path / "native-two"),
    ]

    matches, problems = match_export_folders(flights, exports)

    assert set(matches) == {"one"}
    assert any("Flight 2" in problem for problem in problems)
