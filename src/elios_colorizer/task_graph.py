"""Deterministic tile/frame task planning for the projection engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class ProjectionTask:
    offset: int
    length: int
    frame_indices: tuple[int, ...]

    @property
    def work_units(self) -> int:
        return self.length * len(self.frame_indices)


@dataclass(frozen=True)
class ProjectionTaskGraph:
    tasks: tuple[ProjectionTask, ...]
    possible_work_units: int

    @property
    def active_work_units(self) -> int:
        return sum(task.work_units for task in self.tasks)

    @property
    def rejected_work_units(self) -> int:
        return self.possible_work_units - self.active_work_units

    @property
    def frame_masks(self) -> dict[int, tuple[int, ...]]:
        return {task.offset: task.frame_indices for task in self.tasks}


def build_projection_task_graph(
        bounds_by_offset: Mapping[int, object], lengths_by_offset: Mapping[int, int],
        frames: Sequence[object], may_be_visible: Callable[[object, object], bool],
) -> ProjectionTaskGraph:
    """Build ordered independent tile tasks without changing frame chronology."""
    tasks = []
    possible = 0
    for offset, bounds in bounds_by_offset.items():
        length = lengths_by_offset[offset]
        possible += length * len(frames)
        indices = tuple(index for index, frame in enumerate(frames)
                        if may_be_visible(bounds, frame))
        if indices:
            tasks.append(ProjectionTask(offset, length, indices))
    return ProjectionTaskGraph(tuple(tasks), possible)
