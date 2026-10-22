"""Discover Inspector 5 assets without coupling filesystem parsing to the GUI."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Iterable


class InspectorDiscoveryError(ValueError):
    """The selected folder cannot be used as an Inspector asset."""


@dataclass(frozen=True)
class InspectorFlight:
    id: str
    inspection_id: str
    name: str
    folder_path: Path
    datetime_utc: str | None = None
    duration: Any = None
    identifier: str | None = None
    flags: Any = None
    metadata: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class InspectorInspection:
    id: str
    asset_id: str
    name: str
    folder_path: Path
    flights: tuple[InspectorFlight, ...] = ()


@dataclass(frozen=True)
class InspectorAsset:
    id: str
    name: str
    folder_path: Path
    workspace_path: Path
    inspections: tuple[InspectorInspection, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def flight_count(self) -> int:
        return sum(len(item.flights) for item in self.inspections)


def match_export_folders(flights: Iterable[InspectorFlight], parent: str | Path):
    """Match Inspector's default ``... Flight N`` export folder names."""
    root = Path(parent).expanduser().resolve()
    selected = list(flights)
    if not root.is_dir():
        return {}, ["The selected export parent folder does not exist."]
    try:
        candidates = [item.resolve() for item in root.iterdir() if item.is_dir()]
    except OSError as exc:
        return {}, [f"The export parent folder cannot be read: {exc}"]
    matches: dict[str, Path] = {}
    problems: list[str] = []
    for flight in selected:
        number_match = re.search(r"\bflight[\s_-]*(\d+)\b", flight.name, re.IGNORECASE)
        if not number_match:
            problems.append(f'{flight.name}: no "Flight N" number was available for automatic matching.')
            continue
        number = int(number_match.group(1))
        suffix = re.compile(rf"(?:^|[^a-z0-9])flight[\s_-]*0*{number}\s*$", re.IGNORECASE)
        found = [folder for folder in candidates if suffix.search(folder.name)]
        if len(found) == 1:
            matches[flight.id] = found[0]
        elif not found:
            problems.append(f"{flight.name}: no folder ending in Flight {number} was found.")
        else:
            problems.append(f"{flight.name}: multiple folders ending in Flight {number} were found.")
    return matches, problems


def _metadata_file(folder: Path, filename: str) -> Path | None:
    expected = filename.casefold()
    try:
        return next((item for item in folder.iterdir()
                     if item.is_file() and item.name.casefold() == expected), None)
    except OSError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InspectorDiscoveryError(f"Cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise InspectorDiscoveryError(f"{path.name} must contain a JSON object.")
    return value


def _required_text(data: dict[str, Any], key: str, source: Path) -> str:
    value = data.get(key)
    if not isinstance(value, (str, int)) or not str(value).strip():
        raise InspectorDiscoveryError(f"{source.name} has no valid {key}.")
    return str(value).strip()


def resolve_workspace_root(asset_folder: str | Path) -> Path:
    """Resolve ``.../<workspace>/assets/<asset id>`` without assuming UUID text."""
    folder = Path(asset_folder).expanduser().resolve()
    if not folder.is_dir():
        raise InspectorDiscoveryError(f"Asset folder does not exist: {folder}")
    if not _metadata_file(folder, "asset.json"):
        raise InspectorDiscoveryError("The selected folder does not contain asset.json.")
    for ancestor in (folder.parent, *folder.parents):
        if ancestor.name.casefold() == "assets":
            return ancestor.parent
    raise InspectorDiscoveryError(
        "The asset is not inside an Inspector workspace 'assets' folder.")


def _workspace_folder(workspace: Path, name: str) -> Path | None:
    try:
        return next((item for item in workspace.iterdir()
                     if item.is_dir() and item.name.casefold() == name.casefold()), None)
    except OSError:
        return None


def _json_files(folder: Path | None, filename: str) -> Iterable[Path]:
    if folder is None:
        return ()
    try:
        return tuple(sorted((item for item in folder.rglob(filename) if item.is_file()),
                            key=lambda item: str(item).casefold()))
    except OSError:
        return ()


def discover_asset(asset_folder: str | Path) -> InspectorAsset:
    """Scan workspace metadata once and build one ID-linked asset hierarchy."""
    folder = Path(asset_folder).expanduser().resolve()
    workspace = resolve_workspace_root(folder)
    asset_path = _metadata_file(folder, "asset.json")
    assert asset_path is not None
    asset_data = _read_json(asset_path)
    asset_id = _required_text(asset_data, "id", asset_path)
    asset_name = str(asset_data.get("name") or asset_id).strip()
    warnings: list[str] = []

    inspection_root = _workspace_folder(workspace, "inspections")
    flight_root = _workspace_folder(workspace, "flights")
    if inspection_root is None:
        warnings.append(f"Inspection folder not found in {workspace}.")
    if flight_root is None:
        warnings.append(f"Flight folder not found in {workspace}.")

    inspections_by_asset_id: dict[str, list[tuple[dict[str, Any], Path]]] = defaultdict(list)
    for metadata_path in _json_files(inspection_root, "inspection.json"):
        try:
            data = _read_json(metadata_path)
            inspection_id = _required_text(data, "id", metadata_path)
            linked_asset_id = _required_text(data, "asset_id", metadata_path)
        except InspectorDiscoveryError as exc:
            warnings.append(f"Skipped {metadata_path.parent.name}: {exc}")
            continue
        data = dict(data, id=inspection_id, asset_id=linked_asset_id)
        inspections_by_asset_id[linked_asset_id].append((data, metadata_path.parent.resolve()))

    flights_by_inspection_id: dict[str, list[InspectorFlight]] = defaultdict(list)
    for metadata_path in _json_files(flight_root, "flight.json"):
        try:
            data = _read_json(metadata_path)
            flight_id = _required_text(data, "id", metadata_path)
            inspection_id = _required_text(data, "inspection_id", metadata_path)
        except InspectorDiscoveryError as exc:
            warnings.append(f"Skipped {metadata_path.parent.name}: {exc}")
            continue
        flight_folder = metadata_path.parent.resolve()
        if not flight_folder.is_dir():
            warnings.append(f"Skipped missing flight folder for {flight_id}.")
            continue
        flights_by_inspection_id[inspection_id].append(InspectorFlight(
            id=flight_id,
            inspection_id=inspection_id,
            name=str(data.get("name") or data.get("identifier") or flight_id).strip(),
            folder_path=flight_folder,
            datetime_utc=str(data["datetime_utc"]).strip() if data.get("datetime_utc") is not None else None,
            duration=data.get("duration"),
            identifier=str(data["identifier"]).strip() if data.get("identifier") is not None else None,
            flags=data.get("flags"),
            metadata=data,
        ))

    inspections: list[InspectorInspection] = []
    for data, inspection_folder in inspections_by_asset_id.get(asset_id, []):
        inspection_id = data["id"]
        flights = tuple(sorted(flights_by_inspection_id.get(inspection_id, []),
                               key=lambda item: ((item.datetime_utc or ""), item.name.casefold(), item.id)))
        if not flights:
            warnings.append(f'Inspection "{data.get("name") or inspection_id}" has no matching flights.')
        inspections.append(InspectorInspection(
            id=inspection_id,
            asset_id=asset_id,
            name=str(data.get("name") or inspection_id).strip(),
            folder_path=inspection_folder,
            flights=flights,
        ))
    inspections.sort(key=lambda item: (item.name.casefold(), item.id))
    if not inspections:
        warnings.append("No inspections in this workspace match the selected asset.")
    return InspectorAsset(asset_id, asset_name, folder, workspace, tuple(inspections), tuple(warnings))


def flight_feature_available(flight: InspectorFlight, *terms: str) -> bool | None:
    """Best-effort availability hint from Inspector's evolving metadata schema."""
    wanted = tuple(term.casefold() for term in terms)
    found: list[bool] = []

    def visit(value: Any, key: str = "") -> None:
        key_matches = any(term in key.casefold() for term in wanted)
        if key_matches and isinstance(value, bool):
            found.append(value)
        elif key_matches and value is not None and isinstance(value, (str, int, float)):
            found.append(bool(value) and str(value).casefold() not in ("false", "none", "0"))
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, key)

    visit(flight.flags)
    for key, value in flight.metadata.items():
        if key not in {"flags", "id", "inspection_id", "name"}:
            visit(value, str(key))
    return any(found) if found else (False if found else None)
