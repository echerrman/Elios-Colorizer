"""Registration metadata for the colorization tool."""

from ...core import ToolDescriptor
from .workspace import ColorizationWorkspace


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        id="colorization",
        title="Colorization",
        description="Project recorded RGB imagery onto one or more point clouds.",
        factory=lambda owner: ColorizationWorkspace(owner),
    )
