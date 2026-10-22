"""Typed results that can be handed from one desktop tool to another."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class ColorizedPointCloudArtifact:
    """A completed colorization result consumable by future tools."""

    path: Path
    report_path: Path | None = None
    source_flights: tuple[str, ...] = ()
    colored_points: int = 0
    total_points: int = 0
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def coverage(self) -> float | None:
        return self.colored_points / self.total_points if self.total_points else None


class ArtifactRegistry:
    """Session-scoped catalog for safe, typed handoff between tool modules."""

    def __init__(self) -> None:
        self._artifacts: list[ColorizedPointCloudArtifact] = []

    def add(self, artifact: ColorizedPointCloudArtifact) -> None:
        if any(existing.id == artifact.id for existing in self._artifacts):
            raise ValueError(f"Artifact id is already registered: {artifact.id}")
        self._artifacts.append(artifact)

    def all(self) -> tuple[ColorizedPointCloudArtifact, ...]:
        return tuple(self._artifacts)

    def latest(self) -> ColorizedPointCloudArtifact | None:
        return self._artifacts[-1] if self._artifacts else None
