"""Shared contracts for GUI tools."""

from .artifacts import ArtifactRegistry, ColorizedPointCloudArtifact
from .module import ToolDescriptor, ToolRegistry

__all__ = ["ArtifactRegistry", "ColorizedPointCloudArtifact", "ToolDescriptor", "ToolRegistry"]
