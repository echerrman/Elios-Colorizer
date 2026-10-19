"""Interactive point-cloud/video viewport for the calibration workbench."""
from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .calibration_workbench import render_cloud_composite, render_overlay


class CalibrationViewport(QOpenGLWidget):
    """OpenGL-backed viewport with vectorized cloud projection and EDL shading."""

    rendered = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(620, 380)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(
            "Wheel: zoom · drag: pan · in 3D Inspector, drag: orbit · double-click: reset view")
        self.frame = None
        self.xyz: np.ndarray | None = None
        self.calibration = None
        self.display_mode = "composite"
        self.video_opacity = .55
        self.point_size = 4
        self.target_points = 500_000
        self.color_mode = "elevation"
        self.edl_strength = 1.2
        self.zoom = 1.0
        self.pan_fraction = np.zeros(2, dtype=np.float64)
        self.context_angles = np.zeros(2, dtype=np.float64)
        self._image: QImage | None = None
        self._error = ""
        self._last_mouse: QPoint | None = None
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(70)
        self._render_timer.timeout.connect(self._render_now)

    def set_scene(self, frame: Any, xyz: np.ndarray, calibration: Any) -> None:
        self.frame, self.xyz, self.calibration = frame, xyz, calibration
        self.schedule_render()

    def set_display_mode(self, mode: str) -> None:
        self.display_mode = mode
        self.schedule_render()

    def set_video_opacity(self, value: int) -> None:
        self.video_opacity = float(np.clip(value / 100, 0, 1))
        self.schedule_render()

    def set_point_size(self, value: int) -> None:
        self.point_size = int(value)
        self.schedule_render()

    def set_density(self, value: int) -> None:
        self.target_points = int(value)
        self.schedule_render()

    def set_color_mode(self, value: str) -> None:
        self.color_mode = value
        self.schedule_render()

    def set_edl_strength(self, value: int) -> None:
        self.edl_strength = value / 100
        self.schedule_render()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.pan_fraction[:] = 0
        self.context_angles[:] = 0
        self.schedule_render()

    def schedule_render(self) -> None:
        self._render_timer.start()

    def _render_now(self) -> None:
        if self.frame is None or self.xyz is None or self.calibration is None:
            return
        try:
            width = int(np.clip(max(self.width(), 640), 640, 1600))
            pan = tuple(self.pan_fraction * np.array((width, width), dtype=np.float64))
            mode = self.display_mode
            if mode == "diagnostic":
                result = render_overlay(
                    self.frame, self.xyz, self.calibration,
                    opacity=max(.1, 1 - self.video_opacity),
                    point_radius=max(1, self.point_size // 2), preview_width=width)
                rgb = result.rgb
            else:
                result = render_cloud_composite(
                    self.frame, self.xyz, self.calibration,
                    target_points=self.target_points, point_radius=self.point_size,
                    video_opacity=self.video_opacity, color_mode=self.color_mode,
                    edl_strength=self.edl_strength, display_mode=mode,
                    preview_width=width, zoom=self.zoom, pan_px=pan,
                    context_angles=tuple(self.context_angles))
                rgb = result.rgb
            image = np.ascontiguousarray(rgb)
            self._image = QImage(
                image.data, image.shape[1], image.shape[0], image.strides[0],
                QImage.Format.Format_RGB888).copy()
            self._error = ""
            self.rendered.emit(result)
        except Exception as exc:  # viewport failures must not close the workbench
            self._error = str(exc)
        self.update()

    def paintGL(self) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#080c10"))
        if self._image is not None:
            target = self._image.size()
            target.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = (self.width() - target.width()) // 2
            y = (self.height() - target.height()) // 2
            painter.drawImage(x, y, self._image.scaled(
                target, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        if self.display_mode == "context":
            painter.setPen(QPen(QColor("#59d4ed"), 1))
            painter.drawLine(self.width() // 2 - 12, self.height() // 2,
                             self.width() // 2 + 12, self.height() // 2)
            painter.drawLine(self.width() // 2, self.height() // 2 - 12,
                             self.width() // 2, self.height() // 2 + 12)
            painter.drawText(12, 24, "3D Inspector · drag to orbit · alignment is judged in Composite")
        if self._error:
            painter.setPen(QColor("#ffb07a"))
            painter.drawText(self.rect().adjusted(20, 20, -20, -20),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self._error)
        painter.end()

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom = float(np.clip(self.zoom * factor, 1.0, 8.0))
        self.schedule_render()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._last_mouse = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._last_mouse is None:
            return
        current = event.position().toPoint()
        delta = current - self._last_mouse
        self._last_mouse = current
        if self.display_mode == "context":
            self.context_angles += (delta.y() * .25, delta.x() * .25)
            self.context_angles[0] = np.clip(self.context_angles[0], -85, 85)
        else:
            self.pan_fraction += (delta.x() / max(1, self.width()),
                                  delta.y() / max(1, self.height()))
        self.schedule_render()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._last_mouse = None
        self.unsetCursor()
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.reset_view()
        event.accept()
