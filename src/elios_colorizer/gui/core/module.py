"""Small, explicit extension point for first-party desktop tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import QWidget


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """Metadata and factory used by the application shell.

    Tools receive the owning application window so they can use shared services,
    but the shell never needs to know about their internal pages or controllers.
    """

    id: str
    title: str
    description: str
    factory: Callable[[QWidget], QWidget]


class ToolRegistry:
    """Ordered registry for the first-party tools available in this build."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor) -> None:
        if not descriptor.id or descriptor.id in self._tools:
            raise ValueError(f"Tool id must be non-empty and unique: {descriptor.id!r}")
        self._tools[descriptor.id] = descriptor

    def get(self, tool_id: str) -> ToolDescriptor:
        return self._tools[tool_id]

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self._tools.values())
