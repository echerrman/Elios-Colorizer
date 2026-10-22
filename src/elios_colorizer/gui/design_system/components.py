"""Small reusable controls for the Elios 3.x visual language."""

from __future__ import annotations

from PySide6.QtCore import Property, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QLabel


class ToggleSwitch(QAbstractButton):
    """Accessible compact switch used for immediate binary preferences."""

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Toggle")

    def sizeHint(self) -> QSize:
        return QSize(48, 26)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            track = QColor("#9aa8b2")
        elif self.isChecked():
            track = QColor("#1590ad")
        else:
            track = QColor("#778995")
        rect = QRectF(1, 3, self.width() - 2, self.height() - 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        diameter = rect.height() - 6
        x = rect.right() - diameter - 3 if self.isChecked() else rect.left() + 3
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(x, rect.top() + 3, diameter, diameter))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor("#55c9e3"))
            painter.drawRoundedRect(QRectF(.5, .5, self.width() - 1, self.height() - 1), 13, 13)

    checkedState = Property(bool, QAbstractButton.isChecked, QAbstractButton.setChecked)


class StatusBadge(QLabel):
    """Text-and-color status that never relies on color alone."""

    def __init__(self, text: str = "Not checked", tone: str = "neutral", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("statusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(text, tone)

    def set_status(self, text: str, tone: str = "neutral") -> None:
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
