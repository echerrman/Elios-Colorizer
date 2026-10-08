"""Desktop interface for single- and multi-flight colorization."""
from __future__ import annotations
import json, math, os, re, shutil, sys, threading, time, traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from PySide6.QtCore import QObject, QSettings, QThread, QTime, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QAbstractSpinBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QInputDialog, QMenu, QScrollArea, QTimeEdit, QGroupBox,
    QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget, QAbstractItemView, QListView, QTreeView, QSlider, QSpinBox)
from .runtime import ResourcePreferences, detect_hardware, plan_resources, process_priority

_LIGHT_STYLE = """
QMainWindow,QDialog,QWidget#page,QWidget#appShell{background:#e9eff4;color:#182b3d} QWidget{font-family:'Segoe UI';font-size:10pt} QGroupBox,QCheckBox{color:#20364a}
QDialog QWidget{font-size:9pt} QDialog QPushButton{padding:6px 10px} QDialog QLineEdit,QDialog QComboBox,QDialog QDoubleSpinBox,QDialog QSpinBox,QDialog QTimeEdit{padding:6px}
QFrame#card{background:#fff;border:1px solid #b9cad6;border-radius:10px} QFrame#flightRow{background:#f5f9fb;border:1px solid #c2d1dc;border-radius:7px}
QFrame#mainHeader{background:#d9eef6;border:1px solid #83c2d5;border-radius:10px} QFrame#flightsCard{background:#fff;border:1px solid #b9cad6;border-radius:10px} QFrame#validationCard{background:#f6fbfe;border:1px solid #9fc9d8;border-radius:10px}
QFrame#flightProgressPanel{background:#f5f9fb;border:1px solid #bfd0db;border-radius:7px} QFrame#flightProgressRow{background:transparent;border-top:1px solid #cad7df}
QFrame#processingCard{background:#f4fafc;border:2px solid #3097b6;border-radius:10px} QFrame#alignmentPanel{background:#e0f1f5;border:1px solid #65a8bb;border-radius:7px}
QLabel{color:#20364a;background:transparent} QLabel#title{font-size:23pt;font-weight:700;color:#102a3d} QLabel#sectionTitle{font-size:15pt;font-weight:750;color:#075f80;background:#dceff6;border-left:5px solid #1784a3;border-radius:5px;padding:8px 11px} QLabel#muted{color:#526a7d} QLabel#callout{color:#174d64;background:#d9edf3;border:1px solid #b2d5df;border-radius:5px;padding:7px}
QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox{background:#fff;border:1px solid #9fb5c4;border-radius:5px;padding:8px;color:#142c40} QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus,QSpinBox:focus{border-color:#157b9a}
QLineEdit[folderDropActive="true"]{background:#e1f6ec;border:3px solid #1d9b5f;color:#123e2a}
QFrame#folderDropOverlay{background:transparent;border:4px dashed #1d9b5f;border-radius:8px}
QLineEdit#flightTitleEdit{background:transparent;border:1px solid transparent;border-radius:3px;padding:3px 5px;color:#126d8b;font-size:12pt;font-weight:750} QLineEdit#flightTitleEdit:hover{background:#eef6f9;border-color:#b9d7e2} QLineEdit#flightTitleEdit:focus{background:#fff;border-color:#317da6}
QLineEdit#flightTitleEdit[duplicateName="true"]{background:#fff2ef;border-color:#b34330;color:#8e2e1f}
QCheckBox#distanceToggle{color:#17384d;font-weight:700;background:#dceaf0;border:1px solid #7fa6b7;border-radius:5px;padding:8px 11px;spacing:8px} QCheckBox#distanceToggle:hover{background:#d0e5ed;border-color:#448ba4} QCheckBox#distanceToggle:checked{color:#fff;background:#157b9a;border-color:#0d607a} QCheckBox#distanceToggle:disabled{color:#748795;background:#e4ebef;border-color:#bdcbd3}
QCheckBox#cudaAccelerationCheck::indicator{width:16px;height:16px;border:2px solid #648294;border-radius:3px;background:#fff} QCheckBox#cudaAccelerationCheck::indicator:checked{background:#247d68;border-color:#155c4c} QCheckBox#cudaAccelerationCheck::indicator:disabled{background:#d5dde2;border-color:#9cabb5}
QPushButton{background:#fff;color:#173f59;border:1px solid #a9bdca;border-radius:5px;padding:8px 13px;font-weight:600} QPushButton:hover{background:#e4f1f6;border-color:#5b91a7} QPushButton:disabled{color:#8494a1;background:#e9eef1} QPushButton#accentButton{background:#d9edf4;border-color:#448da7;color:#0d5873}
QPushButton#flightProgressToggle{background:transparent;border:none;text-align:left;padding:7px 4px;color:#25465f}
QPushButton#primary{background:#126d8b;color:#fff;border-color:#126d8b} QPushButton#primary:disabled{background:#b6c9d1}
QPushButton#removeFlightButton{background:#fff0ed;border-color:#d58a7d;color:#993d31} QPushButton#removeFlightButton:hover{background:#f9ddd7;border-color:#bd5c4c} QPushButton#addFlightButton{background:#e7f5eb;border-color:#72ad84;color:#256b3e} QPushButton#addFlightButton:hover{background:#d7eedf;border-color:#55966a}
QCheckBox#comingSoonToggle{color:#8794a2;background:#eef1f4;border:1px dashed #b7c0c9;border-radius:5px;padding:8px 11px} QLabel#comingSoonBadge{color:#657483;background:#dfe5ea;border-radius:8px;padding:3px 8px;font-size:8pt;font-weight:700}
QProgressBar{border:none;background:#e8eef3;border-radius:4px;min-height:8px} QProgressBar::chunk{background:#1784a3}
QLabel#percentBadge{background:#126d8b;color:#fff;border-radius:12px;padding:5px 10px;font-size:10pt;font-weight:700}
QLabel#settingsChip{background:#e7f2f6;color:#195873;border:1px solid #b9d7e2;border-radius:9px;padding:4px 8px;font-weight:600} QLabel#readyBadge{color:#187548;font-size:8pt;font-weight:700} QLabel#resultHeadline{font-size:12pt;font-weight:700;color:#126d8b}
QLabel#warningCallout{color:#7a5200;background:#fff2c7;border:1px solid #ddb84e;border-radius:5px;padding:8px;font-weight:600}
QFrame#hardwareLoadingOverlay{background:rgba(255,255,255,220);border:1px solid #76a7b8;border-radius:6px}
QFrame#dialogHero{background:#d9eef6;border:1px solid #83c2d5;border-radius:9px} QLabel#dialogTitle{font-size:18pt;font-weight:750;color:#075f80} QLabel#dialogSubtitle{color:#42657a}
QFrame#recommendCard{background:#d8f2f4;border:2px solid #19a6b7;border-radius:10px} QCheckBox#recommendToggle{font-size:13pt;font-weight:700;color:#12465a;spacing:12px}
QFrame#dialogSection{background:#fff;border:1px solid #b9cad6;border-radius:9px} QLabel#dialogSectionTitle{font-size:11pt;font-weight:700;color:#163c55} QLabel#dialogSectionNote{color:#5a7183;font-size:9pt}
QFrame#metricCpu{background:#e7f1ff;border:1px solid #8ab5e9;border-radius:8px} QFrame#metricMemory{background:#f0e9ff;border:1px solid #b298e8;border-radius:8px} QFrame#metricGpu{background:#e4f5e9;border:1px solid #82be92;border-radius:8px} QFrame#metricParallel{background:#e1f5f7;border:1px solid #77bec8;border-radius:8px}
QLabel#metricIcon{font-size:19pt;font-weight:700} QLabel#metricTitle{font-size:9pt;font-weight:700;color:#496173} QLabel#metricValue{font-weight:650;color:#18364b} QFrame#manualPane{background:#f6f9fb;border:1px solid #c6d4de;border-radius:8px}
QFrame#cpuControlPane,QFrame#framePanel{background:#f3f8fe;border:1px solid #a9c9e9;border-radius:8px} QFrame#gpuControlPane{background:#f2faf5;border:1px solid #a5cfb1;border-radius:8px} QFrame#colorPanel{background:#f7f3fe;border:1px solid #c1afe4;border-radius:8px} QFrame#timePanel{background:#f0f9fa;border:1px solid #9fcbd1;border-radius:8px}
QPushButton#primaryDialogButton{background:#1199b3;color:white;border-color:#07839b} QPushButton#primaryDialogButton:hover{background:#0d8299} QLabel#dialogValue{font-weight:700;color:#126d8b}
QFrame#stickyBar{background:#fff;border-top:1px solid #aebfcb} QFrame#resultPanel,QFrame#preflightPanel{background:#f5f9fb;border:1px solid #bfd0db;border-radius:7px}
QPushButton#inlineButton{padding:4px 7px;min-width:24px} QPushButton:focus,QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus,QCheckBox:focus{border:2px solid #126d8b}
QDoubleSpinBox::up-button,QDoubleSpinBox::down-button{width:22px;background:#d9e8ef;border-left:1px solid #8faaba} QDoubleSpinBox::up-button{border-bottom:1px solid #a9bdc8;border-top-right-radius:4px} QDoubleSpinBox::down-button{border-bottom-right-radius:4px}
QFileDialog QToolButton{background:#e5f2f6;color:#123e55;border:1px solid #75a5b7;border-radius:5px;padding:6px} QFileDialog QToolButton:hover{background:#cce7ef;border-color:#287c99}
QScrollBar:vertical{background:#d5e0e7;width:14px;margin:3px;border:none;border-radius:7px} QScrollBar::handle:vertical{background:#5f879a;min-height:34px;margin:1px;border-radius:6px} QScrollBar::handle:vertical:hover{background:#36748e} QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0} QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}
QScrollBar:horizontal{background:#d5e0e7;height:14px;margin:3px;border:none;border-radius:7px} QScrollBar::handle:horizontal{background:#5f879a;min-width:34px;margin:1px;border-radius:6px} QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0} QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{background:transparent}
QPlainTextEdit{background:#f8fafc;color:#344f65;border:1px solid #b9cad6;border-radius:5px;font-family:Consolas;font-size:9pt} QScrollArea{background:transparent;border:none}
"""
_DARK_STYLE = """
QMainWindow,QDialog,QWidget#page,QWidget#appShell{background:#17212b;color:#e6edf3} QWidget{font-family:'Segoe UI';font-size:10pt} QGroupBox,QCheckBox{color:#d9e2ec}
QDialog QWidget{font-size:9pt} QDialog QPushButton{padding:6px 10px} QDialog QLineEdit,QDialog QComboBox,QDialog QDoubleSpinBox,QDialog QSpinBox,QDialog QTimeEdit{padding:6px}
QFrame#card{background:#202c38;border:1px solid #344454;border-radius:10px} QFrame#flightRow{background:#1b2732;border:1px solid #344454;border-radius:7px}
QFrame#mainHeader{background:#162b3d;border:1px solid #31526b;border-radius:10px} QFrame#flightsCard{background:#1d2935;border:1px solid #3b5265;border-radius:10px} QFrame#validationCard{background:#1a2d38;border:1px solid #356478;border-radius:10px}
QFrame#flightProgressPanel{background:#1b2732;border:1px solid #344454;border-radius:7px} QFrame#flightProgressRow{background:transparent;border-top:1px solid #344454}
QFrame#processingCard{background:#1b2d38;border:2px solid #347f98;border-radius:10px} QFrame#alignmentPanel{background:#193643;border:1px solid #3b8198;border-radius:7px}
QLabel{color:#d9e2ec;background:transparent} QLabel#title{font-size:23pt;font-weight:700;color:#f4f8fb} QLabel#sectionTitle{font-size:15pt;font-weight:750;color:#8bdcf0;background:#183b49;border-left:5px solid #42b6d3;border-radius:5px;padding:8px 11px} QLabel#muted{color:#aab9c7} QLabel#callout{color:#d8f1f7;background:#234652;border-radius:5px;padding:7px}
QLineEdit,QComboBox,QDoubleSpinBox,QSpinBox{background:#16212b;border:1px solid #4a5d70;border-radius:5px;padding:8px;color:#edf4f8}
QLineEdit[folderDropActive="true"]{background:#183f32;border:3px solid #63d99a;color:#effff6}
QFrame#folderDropOverlay{background:transparent;border:4px dashed #63d99a;border-radius:8px}
QLineEdit#flightTitleEdit{background:transparent;border:1px solid transparent;border-radius:3px;padding:3px 5px;color:#65c7df;font-size:12pt;font-weight:750} QLineEdit#flightTitleEdit:hover{background:#233541;border-color:#426779} QLineEdit#flightTitleEdit:focus{background:#16212b;border-color:#42b6d3}
QLineEdit#flightTitleEdit[duplicateName="true"]{background:#3b2526;border-color:#d66b57;color:#ffd7cf}
QCheckBox#distanceToggle{font-weight:700;background:#263745;border:1px solid #567084;border-radius:5px;padding:8px 11px;spacing:8px} QCheckBox#distanceToggle:checked{background:#214b5c;border-color:#42b6d3}
QCheckBox#cudaAccelerationCheck::indicator{width:16px;height:16px;border:2px solid #7891a0;border-radius:3px;background:#14212b} QCheckBox#cudaAccelerationCheck::indicator:checked{background:#2f8a72;border-color:#8bd6bd} QCheckBox#cudaAccelerationCheck::indicator:disabled{background:#34424d;border-color:#657581}
QPushButton{background:#263745;color:#e3edf4;border:1px solid #4a5d70;border-radius:5px;padding:8px 13px;font-weight:600} QPushButton:hover{background:#304758} QPushButton:disabled{color:#7b8b9a;background:#25313c} QPushButton#accentButton{background:#214b5c;border-color:#42a9c3;color:#e5f7fb}
QPushButton#flightProgressToggle{background:transparent;border:none;text-align:left;padding:7px 4px;color:#e3edf4}
QPushButton#primary{background:#1784a3;color:#fff;border-color:#1784a3} QPushButton#primary:disabled{background:#3b5b68}
QPushButton#removeFlightButton{background:#422b2d;border-color:#995b55;color:#ffc7be} QPushButton#removeFlightButton:hover{background:#523235;border-color:#c67568} QPushButton#addFlightButton{background:#213a2c;border-color:#4f8a66;color:#c8f0d5} QPushButton#addFlightButton:hover{background:#294936;border-color:#68a87e}
QCheckBox#comingSoonToggle{color:#81909e;background:#222f3a;border:1px dashed #4b5d6d;border-radius:5px;padding:8px 11px} QLabel#comingSoonBadge{color:#afbdc9;background:#34434f;border-radius:8px;padding:3px 8px;font-size:8pt;font-weight:700}
QProgressBar{border:none;background:#30404e;border-radius:4px;min-height:8px} QProgressBar::chunk{background:#42b6d3}
QLabel#percentBadge{background:#1784a3;color:#fff;border-radius:12px;padding:5px 10px;font-size:10pt;font-weight:700}
QLabel#settingsChip{background:#263f4b;color:#d8f1f7;border:1px solid #426779;border-radius:9px;padding:4px 8px;font-weight:600} QLabel#readyBadge{color:#7ee2a8;font-size:8pt;font-weight:700} QLabel#resultHeadline{font-size:12pt;font-weight:700;color:#64c5df}
QLabel#warningCallout{color:#ffe49a;background:#493d20;border:1px solid #a98734;border-radius:5px;padding:8px;font-weight:600}
QFrame#hardwareLoadingOverlay{background:rgba(23,33,43,225);border:1px solid #426779;border-radius:6px}
QFrame#dialogHero{background:#162b3d;border:1px solid #31526b;border-radius:9px} QLabel#dialogTitle{font-size:18pt;font-weight:750;color:#91dcf1} QLabel#dialogSubtitle{color:#9bb8cb}
QFrame#recommendCard{background:#123d4a;border:2px solid #23bcd1;border-radius:10px} QCheckBox#recommendToggle{font-size:13pt;font-weight:700;color:#eefbff;spacing:12px}
QFrame#dialogSection{background:#1d2935;border:1px solid #344b5d;border-radius:9px} QLabel#dialogSectionTitle{font-size:11pt;font-weight:700;color:#f0f6fa} QLabel#dialogSectionNote{color:#9db3c3;font-size:9pt}
QFrame#metricCpu{background:#172f49;border:1px solid #315f8e;border-radius:8px} QFrame#metricMemory{background:#292547;border:1px solid #63579a;border-radius:8px} QFrame#metricGpu{background:#183b31;border:1px solid #39765d;border-radius:8px} QFrame#metricParallel{background:#153b47;border:1px solid #32768a;border-radius:8px}
QLabel#metricIcon{font-size:19pt;font-weight:700} QLabel#metricTitle{font-size:9pt;font-weight:700;color:#a9bdca} QLabel#metricValue{font-weight:650;color:#eef5f9} QFrame#manualPane{background:#192532;border:1px solid #344b5d;border-radius:8px}
QFrame#cpuControlPane,QFrame#framePanel{background:#172b40;border:1px solid #315c83;border-radius:8px} QFrame#gpuControlPane{background:#19332c;border:1px solid #386b56;border-radius:8px} QFrame#colorPanel{background:#282541;border:1px solid #5c5689;border-radius:8px} QFrame#timePanel{background:#17333e;border:1px solid #326a7b;border-radius:8px}
QPushButton#primaryDialogButton{background:#25bdd4;color:#08232d;border-color:#25bdd4} QPushButton#primaryDialogButton:hover{background:#54cde0} QLabel#dialogValue{font-weight:700;color:#68d4e5}
QFrame#stickyBar{background:#202c38;border-top:1px solid #4a5d70} QFrame#resultPanel,QFrame#preflightPanel{background:#1b2732;border:1px solid #344454;border-radius:7px}
QPushButton#inlineButton{padding:4px 7px;min-width:24px} QPushButton:focus,QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus,QCheckBox:focus{border:2px solid #42b6d3}
QFileDialog QToolButton{background:#d8eaf0;color:#102a3a;border:1px solid #7295a5;border-radius:5px;padding:6px} QFileDialog QToolButton:hover{background:#fff;border-color:#72c6dc}
QScrollBar:vertical{background:#121b24;width:14px;margin:3px;border:none;border-radius:7px} QScrollBar::handle:vertical{background:#536b7b;min-height:34px;margin:1px;border-radius:6px} QScrollBar::handle:vertical:hover{background:#6d9caf} QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0} QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}
QScrollBar:horizontal{background:#121b24;height:14px;margin:3px;border:none;border-radius:7px} QScrollBar::handle:horizontal{background:#536b7b;min-width:34px;margin:1px;border-radius:6px} QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0} QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{background:transparent}
QPlainTextEdit{background:#16212b;color:#c1d0dc;border:1px solid #344454;border-radius:5px;font-family:Consolas;font-size:9pt} QScrollArea{background:transparent;border:none}
"""

def _asset_path(name):
    root=Path(getattr(sys,"_MEIPASS",Path(sys.executable).parent)) if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[2]
    return root/"assets"/name
def _label(text,muted=False):
    x=QLabel(text); x.setTextFormat(Qt.TextFormat.PlainText); x.setWordWrap(True)
    if muted:x.setObjectName("muted")
    return x
def _card(title):
    card=QFrame(objectName="card"); box=QVBoxLayout(card); box.setContentsMargins(19,16,19,17); box.setSpacing(10)
    title_label=_label(title); title_label.setObjectName("sectionTitle"); box.addWidget(title_label); return card,box

class NoWheelComboBox(QComboBox):
    """Prevent accidental option changes while the page is being scrolled."""
    def wheelEvent(self,event):
        event.ignore()

class FolderDropLineEdit(QLineEdit):
    """Folder field that accepts a single directory without creating a flight."""
    path_dropped=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self.setAcceptDrops(True);self.setProperty("folderDropActive",False);self.path_dropped.connect(self.setText)
    def _set_drop_highlight(self,active):
        if self.property("folderDropActive")==active:return
        self.setProperty("folderDropActive",active);self.style().unpolish(self);self.style().polish(self);self.update()
    @staticmethod
    def _folder(event):
        urls=event.mimeData().urls() if event.mimeData().hasUrls() else []
        return Path(urls[0].toLocalFile()) if len(urls)==1 else None
    def dragEnterEvent(self,event):
        path=self._folder(event)
        if path and path.is_dir():
            self._set_drop_highlight(True)
            window=self.window()
            if hasattr(window,"_set_application_drop_highlight"):window._set_application_drop_highlight(False)
            event.acceptProposedAction()
        else:event.ignore()
    def dragLeaveEvent(self,event):
        self._set_drop_highlight(False)
        window=self.window()
        if hasattr(window,"_set_application_drop_highlight"):window._set_application_drop_highlight(True)
        event.accept()
    def dropEvent(self,event):
        path=self._folder(event)
        self._set_drop_highlight(False)
        window=self.window()
        if hasattr(window,"_set_application_drop_highlight"):window._set_application_drop_highlight(False)
        if path and path.is_dir():self.path_dropped.emit(str(path));event.acceptProposedAction()
        else:event.ignore()

class LasDropLineEdit(FolderDropLineEdit):
    """File field that accepts one LAS or LAZ point cloud."""
    @staticmethod
    def _folder(event):
        urls=event.mimeData().urls() if event.mimeData().hasUrls() else []
        if len(urls)!=1:return None
        path=Path(urls[0].toLocalFile())
        return path if path.is_file() and path.suffix.lower() in (".las",".laz") else None

@dataclass(frozen=True)
class AdvancedProcessingSettings:
    sample_frequency_hz:float=1.0
    image_edge_exclusion_percent:float=2.0
    minimum_sharpness:float=2.0
    time_range_enabled:bool=False
    start_time_s:float=0.0
    end_time_s:float|None=None
    multi_frame_fusion:bool=False

    def to_dict(self):
        return {"sample_frequency_hz":self.sample_frequency_hz,
                "image_edge_exclusion_percent":self.image_edge_exclusion_percent,
                "minimum_sharpness":self.minimum_sharpness,
                "time_range_enabled":self.time_range_enabled,
                "start_time_s":self.start_time_s,"end_time_s":self.end_time_s,
                "multi_frame_fusion":self.multi_frame_fusion}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict):raise ValueError("Preset must be an object.")
        result=cls(float(value.get("sample_frequency_hz",1)),float(value.get("image_edge_exclusion_percent",2)),
                   float(value.get("minimum_sharpness",2)),bool(value.get("time_range_enabled",False)),
                   float(value.get("start_time_s",0)),
                   None if value.get("end_time_s") is None else float(value["end_time_s"]),
                   bool(value.get("multi_frame_fusion",False)))
        if not .25<=result.sample_frequency_hz<=30:raise ValueError("Preset sampling frequency is outside the supported range.")
        if not 0<=result.image_edge_exclusion_percent<=15:raise ValueError("Preset edge exclusion is outside the supported range.")
        if result.minimum_sharpness not in (0.,2.,8.):raise ValueError("Preset blur rejection is unsupported.")
        if not math.isfinite(result.start_time_s) or result.start_time_s<0:raise ValueError("Preset start time is invalid.")
        if result.end_time_s is not None and (not math.isfinite(result.end_time_s) or result.end_time_s<=result.start_time_s):raise ValueError("Preset end time must follow its start time.")
        return result

def _dialog_hero(title,subtitle):
    panel=QFrame(objectName="dialogHero");layout=QVBoxLayout(panel);layout.setContentsMargins(15,10,15,10);layout.setSpacing(2)
    heading=_label(title);heading.setObjectName("dialogTitle");heading.setWordWrap(False);layout.addWidget(heading)
    note=_label(subtitle);note.setObjectName("dialogSubtitle");layout.addWidget(note);return panel

def _dialog_section(title,note=""):
    panel=QFrame(objectName="dialogSection");layout=QVBoxLayout(panel);layout.setContentsMargins(11,9,11,10);layout.setSpacing(6)
    heading=_label(title);heading.setObjectName("dialogSectionTitle");heading.setWordWrap(False);layout.addWidget(heading)
    if note:
        description=_label(note);description.setObjectName("dialogSectionNote");layout.addWidget(description)
    return panel,layout

def _metric_card(object_name,icon,title):
    panel=QFrame(objectName=object_name);layout=QVBoxLayout(panel);layout.setContentsMargins(11,9,11,9);layout.setSpacing(3)
    heading=_label(title.upper());heading.setObjectName("metricTitle");layout.addWidget(heading);value=_label("Detecting…");value.setObjectName("metricValue");layout.addWidget(value,1);return panel,value

class AdvancedProcessingDialog(QDialog):
    BLUR_LEVELS=(("Off",0.0),("Normal",2.0),("Strong",8.0))
    def __init__(self,settings:AdvancedProcessingSettings,parent=None,*,presets=None,available_duration_s=None):
        super().__init__(parent);self.setStyleSheet(parent.styleSheet() if parent is not None else "");self.setWindowTitle("Advanced Processing Settings");self.setModal(True);self.resize(900,650);self.setMinimumSize(820,600);self.setSizeGripEnabled(True)
        self._presets=dict(presets or {});self._applying_preset=False;self.available_duration_s=available_duration_s
        root=QVBoxLayout(self);root.setContentsMargins(12,11,12,11);root.setSpacing(8);root.addWidget(_dialog_hero("Advanced Processing","Tune frame selection, color quality, fusion, and the video interval used for this run."))
        preset_panel,preset_box=_dialog_section("Processing preset","Reuse a proven configuration or save the current settings for future flights.");preset_row=QHBoxLayout();preset_row.addWidget(_label("Saved preset"));self.preset_combo=NoWheelComboBox();self.preset_combo.setObjectName("processingPresetCombo");preset_row.addWidget(self.preset_combo,1)
        self.save_preset_button=QPushButton("Save current…");self.save_preset_button.clicked.connect(self._prompt_save_preset);preset_row.addWidget(self.save_preset_button)
        self.delete_preset_button=QPushButton("Delete");self.delete_preset_button.clicked.connect(self._confirm_delete_preset);preset_row.addWidget(self.delete_preset_button);preset_box.addLayout(preset_row);root.addWidget(preset_panel)
        self.active_summary=_label("");self.active_summary.setObjectName("callout");root.addWidget(self.active_summary)
        settings_grid=QGridLayout();settings_grid.setHorizontalSpacing(12);settings_grid.setVerticalSpacing(12);settings_grid.setColumnStretch(0,1);settings_grid.setColumnStretch(1,1);root.addLayout(settings_grid,1)
        frame_group,frame_box=_dialog_section("Frame selection","Control how frequently usable RGB frames are considered.");frame_group.setObjectName("framePanel");frame_grid=QGridLayout();frame_grid.setColumnStretch(1,1);frame_box.addLayout(frame_grid);settings_grid.addWidget(frame_group,0,0,1,2)
        color_group,color_box=_dialog_section("Color quality","Reject weak observations or combine several strong views per point.");color_group.setObjectName("colorPanel");color_grid=QGridLayout();color_grid.setColumnStretch(1,1);color_box.addLayout(color_grid);settings_grid.addWidget(color_group,1,0)
        time_group,time_box=_dialog_section("Video time range","Optionally use only a synchronized portion of every selected flight.");time_group.setObjectName("timePanel");time_grid=QGridLayout();time_grid.setColumnStretch(1,1);time_box.addLayout(time_grid);settings_grid.addWidget(time_group,1,1)
        self.sample_frequency=QDoubleSpinBox();self.sample_frequency.setObjectName("sampleFrequencySpin");self.sample_frequency.setRange(.25,30.0);self.sample_frequency.setDecimals(2);self.sample_frequency.setSingleStep(.25);self.sample_frequency.setSuffix(" frames/sec");self.sample_frequency.setValue(settings.sample_frequency_hz)
        self.edge_exclusion=QDoubleSpinBox();self.edge_exclusion.setObjectName("edgeExclusionSpin");self.edge_exclusion.setRange(0,15);self.edge_exclusion.setDecimals(1);self.edge_exclusion.setSingleStep(1);self.edge_exclusion.setSuffix(" % per edge");self.edge_exclusion.setValue(settings.image_edge_exclusion_percent)
        self.blur_rejection=NoWheelComboBox();self.blur_rejection.setObjectName("blurRejectionCombo")
        for label,value in self.BLUR_LEVELS:self.blur_rejection.addItem(label,value)
        self.blur_rejection.setCurrentIndex(next((i for i,(_,value) in enumerate(self.BLUR_LEVELS) if value==settings.minimum_sharpness),1))
        self._add_row(frame_grid,0,"Frame sampling frequency",self._arrow_control(self.sample_frequency,"sampleFrequency"),"How often RGB frames are considered. Range: 0.25–30; default: 1 frame/sec.",lambda:self.sample_frequency.setValue(1.0))
        self._add_row(color_grid,0,"Image edge exclusion",self._arrow_control(self.edge_exclusion,"edgeExclusion"),"Ignore projected pixels inside this border on all four edges. Range: 0–15%; default: 2%.",lambda:self.edge_exclusion.setValue(2.0))
        self._add_row(color_grid,2,"Blur rejection",self.blur_rejection,"Strong rejects more soft frames; Off accepts every frame. Default: Normal.",lambda:self.blur_rejection.setCurrentIndex(1))
        self.fusion_check=QCheckBox("Robust multi-frame color fusion");self.fusion_check.setObjectName("multiFrameFusionCheck");color_grid.addWidget(self.fusion_check,4,0,1,3)
        color_grid.addWidget(_label("Combines several strong views per point and rejects color outliers such as glare. Uses more temporary disk space. Default: Off.",True),5,0,1,3)
        self.time_range_check=QCheckBox("Process only a time range");self.time_range_check.setObjectName("timeRangeCheck");time_grid.addWidget(self.time_range_check,0,0,1,3)
        time_controls=QWidget();time_layout=QHBoxLayout(time_controls);time_layout.setContentsMargins(0,0,0,0);time_layout.addWidget(_label("Start"));self.start_time=QTimeEdit();self.start_time.setObjectName("startTimeEdit");self.start_time.setDisplayFormat("HH:mm:ss");time_layout.addWidget(self.start_time);time_layout.addSpacing(12);time_layout.addWidget(_label("End"));self.end_time=QTimeEdit();self.end_time.setObjectName("endTimeEdit");self.end_time.setDisplayFormat("HH:mm:ss");time_layout.addWidget(self.end_time);self.through_end_check=QCheckBox("Through end of video");self.through_end_check.setObjectName("throughVideoEndCheck");time_layout.addWidget(self.through_end_check);time_layout.addStretch();time_grid.addWidget(time_controls,1,0,1,3)
        duration_note=(f" Detected duration: approximately {self._format_seconds(available_duration_s)}." if available_duration_s else "")
        time_grid.addWidget(_label("Times are elapsed from each flight's first synchronized RGB frame."+duration_note,True),2,0,1,3)
        self.time_range_check.toggled.connect(self._update_time_controls);self.through_end_check.toggled.connect(self._update_time_controls)
        self.preset_combo.currentIndexChanged.connect(self._preset_selected)
        for signal in (self.sample_frequency.valueChanged,self.edge_exclusion.valueChanged,
                       self.blur_rejection.currentIndexChanged,self.time_range_check.toggled,
                       self.start_time.timeChanged,self.end_time.timeChanged,self.through_end_check.toggled,
                       self.fusion_check.toggled):signal.connect(self._mark_custom);signal.connect(self._update_active_summary)
        controls=QHBoxLayout();reset_all=QPushButton("Restore Defaults");reset_all.setObjectName("resetAllAdvancedButton");reset_all.clicked.connect(self.reset_defaults);controls.addWidget(reset_all);controls.addStretch()
        cancel=QPushButton("Cancel");cancel.clicked.connect(self.reject);save=QPushButton("Apply Settings",objectName="primaryDialogButton");save.clicked.connect(self.accept);controls.addWidget(cancel);controls.addWidget(save);root.addLayout(controls)
        self._set_values(settings);self._rebuild_preset_combo();self._update_time_controls();self._update_active_summary()
    def _update_active_summary(self,*_):
        values=self.values();blur=next(label for label,value in self.BLUR_LEVELS if value==values.minimum_sharpness);window=("Full video" if not values.time_range_enabled else f"{self._format_seconds(values.start_time_s)} to "+(self._format_seconds(values.end_time_s) if values.end_time_s is not None else "end"));self.active_summary.setText(f"{values.sample_frequency_hz:g} fps  •  {values.image_edge_exclusion_percent:g}% edges  •  {blur} blur rejection  •  Fusion {'On' if values.multi_frame_fusion else 'Off'}  •  {window}")
    @staticmethod
    def _add_row(grid,row,title,control,description,reset):
        label=_label(title);label.setStyleSheet("font-weight:600");grid.addWidget(label,row,0);grid.addWidget(control,row,1);button=QPushButton("Reset");button.clicked.connect(reset);grid.addWidget(button,row,2);grid.addWidget(_label(description,True),row+1,0,1,3)
    @staticmethod
    def _arrow_control(spin,prefix):
        container=QWidget();layout=QHBoxLayout(container);layout.setContentsMargins(0,0,0,0);layout.setSpacing(6)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        decrease=QPushButton("◀");decrease.setObjectName(f"{prefix}DecreaseButton");decrease.setToolTip("Decrease value");decrease.setAccessibleName("Decrease value")
        increase=QPushButton("▶");increase.setObjectName(f"{prefix}IncreaseButton");increase.setToolTip("Increase value");increase.setAccessibleName("Increase value")
        for button in (decrease,increase):button.setMinimumWidth(44);button.setAutoRepeat(True);button.setAutoRepeatDelay(400);button.setAutoRepeatInterval(75)
        decrease.clicked.connect(spin.stepDown);increase.clicked.connect(spin.stepUp)
        layout.addWidget(decrease);layout.addWidget(spin,1);layout.addWidget(increase);return container
    def reset_defaults(self):
        self._set_values(AdvancedProcessingSettings());self.preset_combo.setCurrentIndex(0)
    def values(self):
        return AdvancedProcessingSettings(self.sample_frequency.value(),self.edge_exclusion.value(),float(self.blur_rejection.currentData()),
                                          self.time_range_check.isChecked(),float(self.start_time.time().msecsSinceStartOfDay()/1000),
                                          None if self.through_end_check.isChecked() else float(self.end_time.time().msecsSinceStartOfDay()/1000),
                                          self.fusion_check.isChecked())
    def presets(self):return dict(self._presets)
    @staticmethod
    def _format_seconds(value):
        total=max(0,int(round(value or 0)));hours,remainder=divmod(total,3600);minutes,seconds=divmod(remainder,60);return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    @staticmethod
    def _time(value):
        total=max(0,min(86399,int(round(value or 0))));hours,remainder=divmod(total,3600);minutes,seconds=divmod(remainder,60);return QTime(hours,minutes,seconds)
    def _set_values(self,settings):
        self._applying_preset=True
        self.sample_frequency.setValue(settings.sample_frequency_hz);self.edge_exclusion.setValue(settings.image_edge_exclusion_percent)
        self.blur_rejection.setCurrentIndex(next((i for i,(_,value) in enumerate(self.BLUR_LEVELS) if value==settings.minimum_sharpness),1))
        self.time_range_check.setChecked(settings.time_range_enabled);self.start_time.setTime(self._time(settings.start_time_s))
        self.fusion_check.setChecked(settings.multi_frame_fusion)
        end=settings.end_time_s if settings.end_time_s is not None else self.available_duration_s
        self.end_time.setTime(self._time(end));self.through_end_check.setChecked(settings.end_time_s is None)
        self._applying_preset=False;self._update_time_controls()
    def _update_time_controls(self):
        enabled=self.time_range_check.isChecked();self.start_time.setEnabled(enabled);self.through_end_check.setEnabled(enabled);self.end_time.setEnabled(enabled and not self.through_end_check.isChecked())
    def _rebuild_preset_combo(self,selected=None):
        self._applying_preset=True;self.preset_combo.clear();self.preset_combo.addItem("Custom settings",None);self.preset_combo.addItem("Default settings","__default__")
        for name in sorted(self._presets,key=str.casefold):self.preset_combo.addItem(name,name)
        if selected:
            index=self.preset_combo.findData(selected);self.preset_combo.setCurrentIndex(max(0,index))
        self._applying_preset=False;self._update_delete_button()
    def _preset_selected(self):
        if self._applying_preset:return
        name=self.preset_combo.currentData()
        if name=="__default__":self._set_values(AdvancedProcessingSettings())
        elif name in self._presets:self._set_values(self._presets[name])
        self._update_delete_button()
    def _update_delete_button(self):self.delete_preset_button.setEnabled(self.preset_combo.currentData() in self._presets)
    def _mark_custom(self,*_):
        if self._applying_preset or self.preset_combo.currentIndex()==0:return
        self._applying_preset=True;self.preset_combo.setCurrentIndex(0);self._applying_preset=False;self._update_delete_button()
    def save_preset(self,name):
        name=" ".join(str(name).split())
        if not name or len(name)>48:raise ValueError("Preset names must contain 1–48 characters.")
        if name.casefold() in ("default settings","custom settings"):raise ValueError("Choose a different preset name.")
        existing=next((item for item in self._presets if item.casefold()==name.casefold()),None)
        if existing and existing!=name:del self._presets[existing]
        self._presets[name]=self.values();self._rebuild_preset_combo(name);return name
    def delete_preset(self,name):
        if name not in self._presets:return False
        del self._presets[name];self._rebuild_preset_combo();return True
    def _prompt_save_preset(self):
        name,ok=QInputDialog.getText(self,"Save processing preset","Preset name:")
        if not ok:return
        existing=next((item for item in self._presets if item.casefold()==" ".join(name.split()).casefold()),None)
        if existing and QMessageBox.question(self,"Replace preset",f'Replace the saved preset "{existing}"?')!=QMessageBox.StandardButton.Yes:return
        try:self.save_preset(name)
        except ValueError as exc:QMessageBox.warning(self,"Cannot save preset",str(exc))
    def _confirm_delete_preset(self):
        name=self.preset_combo.currentData()
        if name in self._presets and QMessageBox.question(self,"Delete preset",f'Delete the saved preset "{name}"?')==QMessageBox.StandardButton.Yes:self.delete_preset(name)
    def accept(self):
        values=self.values()
        if values.time_range_enabled and values.end_time_s is not None and values.end_time_s<=values.start_time_s:
            QMessageBox.warning(self,"Invalid time range","End time must be later than start time.");return
        super().accept()

class LoadingSpinner(QWidget):
    """Small theme-aware indeterminate circular activity indicator."""
    def __init__(self,parent=None):
        super().__init__(parent);self._angle=0;self.setFixedSize(44,44);self._timer=QTimer(self);self._timer.setInterval(55);self._timer.timeout.connect(self._advance)
    def start(self):self._angle=0;self._timer.start();self.show();self.update()
    def stop(self):self._timer.stop();self.hide()
    def _advance(self):self._angle=(self._angle+30)%360;self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);color=self.palette().highlight().color();pen=QPen(color,4,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap);painter.setPen(pen);rect=self.rect().adjusted(6,6,-6,-6);painter.drawArc(rect,(90-self._angle)*16,-250*16)

class ToggleSwitch(QCheckBox):
    """Compact accessible switch with no checkbox focus rectangle."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setFixedSize(54,30);self.setCursor(Qt.CursorShape.PointingHandCursor);self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    def hitButton(self,position):return self.rect().contains(position)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track=QColor("#24bdd2" if self.isChecked() else "#627482");painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(track);painter.drawRoundedRect(1,3,52,24,12,12)
        painter.setBrush(QColor("#f8fcfe"));x=29 if self.isChecked() else 5;painter.drawEllipse(x,5,20,20)

class ResourceSettingsDialog(QDialog):
    """Persistent hardware detection and bounded manual resource controls."""
    def __init__(self,preferences:ResourcePreferences,parent=None):
        super().__init__(parent);self.setStyleSheet(parent.styleSheet() if parent is not None else "");self.setWindowTitle("Application Settings");self.setModal(True);self.resize(980,700);self.setMinimumSize(900,650);self.setSizeGripEnabled(True);self._inventory=None
        root=QVBoxLayout(self);root.setContentsMargins(12,11,12,11);root.setSpacing(8);root.addWidget(_dialog_hero("Hardware Resources","Configure how Elios Colorizer uses this computer during processing."))
        recommended_card=QFrame(objectName="recommendCard");recommended_layout=QHBoxLayout(recommended_card);recommended_layout.setContentsMargins(18,13,18,13);recommended_layout.setSpacing(13);self.recommended=ToggleSwitch();self.recommended.setChecked(preferences.use_recommended);self.recommended.setToolTip("Automatically select CPU, memory, CUDA, and flight concurrency for this machine and workload.");recommended_layout.addWidget(self.recommended);recommended_title=_label("Use Recommended Resources");recommended_title.setObjectName("dialogSectionTitle");recommended_title.setWordWrap(False);recommended_layout.addWidget(recommended_title);recommended_layout.addStretch();recommended_note=_label("Automatically adapts to the selected workload and detected hardware.");recommended_note.setObjectName("dialogSubtitle");recommended_layout.addWidget(recommended_note);root.addWidget(recommended_card)
        hardware,hardware_box=_dialog_section("Detected hardware","Current capabilities used to calculate safe recommended settings.");hardware_stack=QGridLayout();hardware_stack.setContentsMargins(0,0,0,0);content=QWidget();content_box=QVBoxLayout(content);content_box.setContentsMargins(0,0,0,0);header=QHBoxLayout();header.addStretch();self.refresh_button=QPushButton("↻  Refresh Hardware");header.addWidget(self.refresh_button);content_box.addLayout(header)
        metrics=QHBoxLayout();metrics.setSpacing(7);cpu_card,self.cpu_metric=_metric_card("metricCpu","CPU","CPU");memory_card,self.memory_metric=_metric_card("metricMemory","RAM","Memory");gpu_card,self.gpu_metric=_metric_card("metricGpu","GPU","Graphics");parallel_card,self.parallel_metric=_metric_card("metricParallel","⇄","CUDA / Parallelization")
        for metric in (cpu_card,memory_card,gpu_card,parallel_card):metrics.addWidget(metric,1)
        content_box.addLayout(metrics);self.hardware_summary=_label("");self.hardware_summary.hide();hardware_stack.addWidget(content,0,0)
        self.loading_overlay=QFrame(objectName="hardwareLoadingOverlay");loading_box=QVBoxLayout(self.loading_overlay);loading_box.addStretch();self.loading_spinner=LoadingSpinner();loading_box.addWidget(self.loading_spinner,alignment=Qt.AlignmentFlag.AlignCenter);loading_text=_label("Scanning hardware…");loading_text.setAlignment(Qt.AlignmentFlag.AlignCenter);loading_box.addWidget(loading_text);loading_box.addStretch();self.loading_overlay.hide();hardware_stack.addWidget(self.loading_overlay,0,0);hardware_box.addLayout(hardware_stack);root.addWidget(hardware)
        manual,manual_box=_dialog_section("Manual resource limits","Fine-tune worker threads, memory, GPU use, and flight scheduling.");self.custom_group=manual;columns=QHBoxLayout();columns.setSpacing(12);manual_box.addLayout(columns)
        cpu_pane=QFrame(objectName="cpuControlPane");cpu_grid=QGridLayout(cpu_pane);cpu_grid.setContentsMargins(11,9,11,9);cpu_grid.setVerticalSpacing(5);cpu_grid.setColumnStretch(1,1);columns.addWidget(cpu_pane,1)
        gpu_pane=QFrame(objectName="gpuControlPane");grid=QGridLayout(gpu_pane);grid.setContentsMargins(11,9,11,9);grid.setVerticalSpacing(5);grid.setColumnStretch(1,1);columns.addWidget(gpu_pane,1)
        self.cpu_slider=self._slider(10,100,5,preferences.cpu_percent);self.cpu_value=_label("");self.cpu_value.setObjectName("dialogValue");cpu_grid.addWidget(_label("CPU capacity target"),0,0);cpu_grid.addWidget(self.cpu_slider,0,1);cpu_grid.addWidget(self.cpu_value,0,2)
        cpu_grid.addWidget(_label("Limits processing worker threads.",True),1,0,1,3)
        self.memory_slider=self._slider(20,90,5,preferences.memory_percent);self.memory_value=_label("");self.memory_value.setObjectName("dialogValue");cpu_grid.addWidget(_label("Memory budget"),2,0);cpu_grid.addWidget(self.memory_slider,2,1);cpu_grid.addWidget(self.memory_value,2,2);cpu_grid.addWidget(_label("Shared total RAM budget across concurrent flights.",True),3,0,1,3);cpu_grid.setRowStretch(4,1)
        self.cuda_check=QCheckBox("Enable CUDA acceleration");self.cuda_check.setObjectName("cudaAccelerationCheck");self.cuda_check.setChecked(preferences.cuda_enabled);grid.addWidget(self.cuda_check,0,0,1,3)
        self.gpu_count=QSpinBox();self.gpu_count.setMinimum(1);self.gpu_count.setValue(preferences.max_gpus);grid.addWidget(_label("Maximum CUDA GPUs"),1,0);grid.addWidget(self.gpu_count,1,1);self.gpu_value=_label("");self.gpu_value.setObjectName("dialogValue");grid.addWidget(self.gpu_value,1,2)
        self.vram_slider=self._slider(30,90,5,preferences.vram_percent);self.vram_value=_label("");self.vram_value.setObjectName("dialogValue");grid.addWidget(_label("VRAM budget"),2,0);grid.addWidget(self.vram_slider,2,1);grid.addWidget(self.vram_value,2,2)
        self.concurrent_flights=QSpinBox();self.concurrent_flights.setMinimum(1);self.concurrent_flights.setValue(preferences.concurrent_flights);grid.addWidget(_label("Maximum concurrent flights"),3,0);grid.addWidget(self.concurrent_flights,3,1);grid.addWidget(_label("Limited by selected flights.",True),3,2)
        self.priority=NoWheelComboBox();self.priority.addItem("Low — leave more responsiveness","low");self.priority.addItem("Normal","normal");self.priority.addItem("High — favor processing","high");self.priority.setCurrentIndex(max(0,self.priority.findData(preferences.process_priority)));grid.addWidget(_label("Processing priority"),4,0);grid.addWidget(self.priority,4,1,1,2)
        manual_box.addWidget(_label("These are processing budgets, not exact operating-system utilization caps.",True));root.addWidget(manual,1)
        self.warning=_label("");self.warning.setObjectName("warningCallout");self.warning.hide();root.addWidget(self.warning)
        self.effective=_label("");self.effective.setObjectName("callout");root.addWidget(self.effective)
        controls=QHBoxLayout();reset=QPushButton("Restore Recommended");reset.clicked.connect(self._restore_recommended);controls.addWidget(reset);controls.addStretch();cancel=QPushButton("Cancel");cancel.clicked.connect(self.reject);save=QPushButton("Save Settings",objectName="primaryDialogButton");save.clicked.connect(self.accept);controls.addWidget(cancel);controls.addWidget(save);root.addLayout(controls)
        self.recommended.toggled.connect(self._update);self.refresh_button.clicked.connect(lambda:self.refresh_hardware(True));self.cuda_check.toggled.connect(self._update)
        for control in (self.cpu_slider,self.memory_slider,self.vram_slider,self.gpu_count,self.concurrent_flights):control.valueChanged.connect(self._update)
        self.priority.currentIndexChanged.connect(self._update);self.refresh_hardware(False)
    @staticmethod
    def _slider(low,high,step,value):
        slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(low,high);slider.setSingleStep(step);slider.setPageStep(step);slider.setValue(value);return slider
    def refresh_hardware(self,force=False):
        if force:
            if self.loading_overlay.isVisible():return
            self.refresh_button.setEnabled(False);self.loading_overlay.show();self.loading_overlay.raise_();self.loading_spinner.start();QTimer.singleShot(500,lambda:self._finish_hardware_refresh(True));return
        self._finish_hardware_refresh(False)
    def _finish_hardware_refresh(self,force):
        try:self._inventory=detect_hardware(refresh_cuda=force)
        finally:
            self.loading_spinner.stop();self.loading_overlay.hide();self.refresh_button.setEnabled(True)
        item=self._inventory;gib=1024**3
        memory=(f"{item.available_memory_bytes/gib:.1f} GiB available of {item.total_memory_bytes/gib:.1f} GiB" if item.available_memory_bytes and item.total_memory_bytes else "memory unavailable")
        gpu=(f"{item.cuda_devices} CUDA GPU(s) · {item.gpu_name} · {item.gpu_total_memory_bytes/gib:.1f} GiB VRAM" if item.cuda_available and item.gpu_total_memory_bytes else item.cuda_summary)
        parallel=("GPU and multi-flight parallel scheduling available" if item.cuda_available else ("CPU multi-flight parallel scheduling available" if item.logical_cpus>=8 else "limited CPU parallel scheduling"))
        self.hardware_summary.setText(f"CPU: {item.cpu_name}\n{item.logical_cpus} logical processors · RAM: {memory}\nGPU: {gpu}\n{parallel}")
        self.cpu_metric.setText(f"{item.cpu_name}\n{item.logical_cpus} logical processors")
        self.memory_metric.setText(memory)
        self.gpu_metric.setText(gpu)
        self.parallel_metric.setText(parallel)
        self.gpu_count.setMaximum(max(1,item.cuda_devices));self.concurrent_flights.setMaximum(max(1,min(32,item.logical_cpus)));self._update()
    def values(self):
        return ResourcePreferences(self.recommended.isChecked(),self.cpu_slider.value(),self.memory_slider.value(),self.cuda_check.isChecked(),self.gpu_count.value(),self.vram_slider.value(),self.concurrent_flights.value(),str(self.priority.currentData()))
    def _restore_recommended(self):
        defaults=ResourcePreferences();self.recommended.setChecked(True);self.cpu_slider.setValue(defaults.cpu_percent);self.memory_slider.setValue(defaults.memory_percent);self.cuda_check.setChecked(defaults.cuda_enabled);self.gpu_count.setValue(min(defaults.max_gpus,self.gpu_count.maximum()));self.vram_slider.setValue(defaults.vram_percent);self.concurrent_flights.setValue(min(defaults.concurrent_flights,self.concurrent_flights.maximum()));self.priority.setCurrentIndex(self.priority.findData(defaults.process_priority));self._update()
    def _update(self,*_):
        if self._inventory is None:return
        custom=not self.recommended.isChecked();self.custom_group.setEnabled(custom);item=self._inventory;gib=1024**3
        threads=max(1,round(item.logical_cpus*self.cpu_slider.value()/100));self.cpu_value.setText(f"{self.cpu_slider.value()}% · {threads} / {item.logical_cpus} threads")
        memory_bytes=int((item.available_memory_bytes or 0)*self.memory_slider.value()/100);self.memory_value.setText(f"{self.memory_slider.value()}% · {memory_bytes/gib:.1f} GiB")
        cuda_allowed=custom and item.cuda_available;self.cuda_check.setEnabled(cuda_allowed);self.cuda_check.setText(("✓  " if self.cuda_check.isChecked() else "")+"Enable CUDA acceleration");cuda_selected=cuda_allowed and self.cuda_check.isChecked();self.gpu_count.setEnabled(cuda_selected);self.vram_slider.setEnabled(cuda_selected);self.gpu_value.setText(f"of {item.cuda_devices} detected")
        vram=int((item.gpu_free_memory_bytes or item.gpu_total_memory_bytes or 0)*self.vram_slider.value()/100);self.vram_value.setText(f"{self.vram_slider.value()}% · {vram/gib:.1f} GiB")
        prefs=self.values();recommended=plan_resources(50_000_000,available_memory_bytes=item.available_memory_bytes,logical_cpus=item.logical_cpus,cuda_available=item.cuda_available,cuda_devices=item.cuda_devices,flight_count=max(1,self.concurrent_flights.value()),available_vram_bytes=item.gpu_free_memory_bytes)
        warnings=[]
        if custom and threads>recommended.worker_threads:warnings.append(f"CPU target exceeds the recommended {recommended.worker_threads} workers.")
        if custom and self.memory_slider.value()>70:warnings.append("High memory budget may reduce system responsiveness or increase paging.")
        if cuda_selected and self.vram_slider.value()>80:warnings.append("High VRAM budget leaves limited room for the display and other GPU applications.")
        if custom and self.concurrent_flights.value()>recommended.concurrent_flights:warnings.append(f"Flight concurrency exceeds the recommended {recommended.concurrent_flights} job(s).")
        if custom and self.priority.currentData()=="high":warnings.append("High priority can make other applications less responsive during processing.")
        self.warning.setText("⚠  "+" ".join(warnings));self.warning.setVisible(bool(warnings))
        if not custom:self.effective.setText(f"Recommended mode · currently up to {recommended.worker_threads} CPU workers · {recommended.concurrent_flights} concurrent flight job(s) · {recommended.cuda_devices} CUDA GPU(s). Final values adapt to the selected data.")
        else:self.effective.setText(f"Custom limits · {threads} CPU workers · {self.memory_slider.value()}% available-memory budget · up to {self.concurrent_flights.value()} concurrent flight job(s) · "+(f"{self.gpu_count.value()} CUDA GPU(s)" if cuda_selected else "CPU only"))

@dataclass(frozen=True)
class FlightSelection:
    folder:str; las_override:str|None=None; name:str=""; transform_path:str|None=None; transform_values:str|None=None
    def to_dict(self): return {"folder":self.folder,"las_override":self.las_override,"name":self.name,"transform_path":self.transform_path,"transform_values":self.transform_values}
@dataclass(frozen=True)
class WorkflowSelection:
    flights:tuple[FlightSelection,...]; calibration_override:str|None=None
    mode:str="separate"; maximum_color_distance_m:float|None=None
    alignment_method:str="manual"; merged_source:str|None=None; cloudcompare_executable:str|None=None
    illumination_balancing:bool=False
    advanced:AdvancedProcessingSettings=AdvancedProcessingSettings()
    resources:ResourcePreferences=ResourcePreferences()
@dataclass(frozen=True)
class SourceSelection: # Public compatibility with 0.2.x.
    folder:str; las_override:str|None=None; calibration_override:str|None=None

class ServiceWorker(QObject):
    result=Signal(object); error=Signal(str,str); progress=Signal(str,float,str); finished=Signal()
    def __init__(self,backend,kind,selection,cancellation,output=""):
        super().__init__(); self.backend=backend; self.kind=kind; self.selection=selection; self.cancellation=cancellation; self.output=output
    @Slot()
    def run(self):
        try:
            priority=self.selection.resources.process_priority if self.kind=="run" and not self.selection.resources.use_recommended else "normal"
            with process_priority(priority):
                flights=[x.to_dict() for x in self.selection.flights]
                if self.kind=="inspect":
                    if len(flights)==1: result=self.backend.inspect_source(folder=flights[0]["folder"],las_override=flights[0]["las_override"],calibration_override=self.selection.calibration_override)
                    else: result=self.backend.inspect_sources(flights,calibration_override=self.selection.calibration_override,mode=self.selection.mode,alignment_method=self.selection.alignment_method,merged_source=self.selection.merged_source,cloudcompare_executable=self.selection.cloudcompare_executable)
                elif len(flights)==1 and self.selection.mode=="separate":
                    kw=dict(folder=flights[0]["folder"],las_override=flights[0]["las_override"],output=self.output,calibration_override=self.selection.calibration_override,progress=self.progress.emit,cancelled=self.cancellation.is_set,resource_settings=self.selection.resources.to_dict())
                    if self.selection.maximum_color_distance_m is not None: kw["maximum_color_distance_m"]=self.selection.maximum_color_distance_m
                    if self.selection.illumination_balancing: kw["illumination_balancing"]=True
                    if self.selection.advanced.multi_frame_fusion:kw["multi_frame_fusion"]=True
                    kw.update(sample_interval_s=1/self.selection.advanced.sample_frequency_hz,
                              image_border_fraction=self.selection.advanced.image_edge_exclusion_percent/100,
                              minimum_sharpness=self.selection.advanced.minimum_sharpness)
                    if self.selection.advanced.time_range_enabled:
                        kw.update(start_s=self.selection.advanced.start_time_s,end_s=self.selection.advanced.end_time_s)
                    result=self.backend.run_colorization(**kw)
                else:
                    result=self.backend.run_workflow(flights,self.output,calibration_override=self.selection.calibration_override,mode=self.selection.mode,
                        alignment_method=self.selection.alignment_method,merged_source=self.selection.merged_source,cloudcompare_executable=self.selection.cloudcompare_executable,
                        illumination_balancing=self.selection.illumination_balancing,maximum_color_distance_m=self.selection.maximum_color_distance_m,
                        sample_interval_s=1/self.selection.advanced.sample_frequency_hz,
                        image_border_fraction=self.selection.advanced.image_edge_exclusion_percent/100,
                        minimum_sharpness=self.selection.advanced.minimum_sharpness,
                        multi_frame_fusion=self.selection.advanced.multi_frame_fusion,
                        start_s=(self.selection.advanced.start_time_s if self.selection.advanced.time_range_enabled else None),
                        end_s=(self.selection.advanced.end_time_s if self.selection.advanced.time_range_enabled else None),
                        resource_settings=self.selection.resources.to_dict(),
                        progress=self.progress.emit,cancelled=self.cancellation.is_set)
            self.result.emit(result)
        except Exception as exc:self.error.emit(str(exc) or type(exc).__name__,traceback.format_exc())
        finally:self.finished.emit()

class Checklist(QWidget):
    activated=Signal(object)
    def __init__(self):
        super().__init__(); self.rows=[]; self.box=QVBoxLayout(self); self.box.setContentsMargins(0,0,0,0)
    def set_rows(self,rows):
        self.rows=rows
        while self.box.count():
            item=self.box.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        for row in rows:
            status=row.get("status","warning"); word,color={"ok":("READY","#187548"),"missing":("NEEDED","#b34330")}.get(status,("CHECK","#926616"))
            panel=QWidget(); grid=QGridLayout(panel); grid.setContentsMargins(0,2,0,2); grid.setColumnStretch(1,1)
            state=_label(word); state.setStyleSheet(f"color:{color};font-size:8pt;font-weight:700"); state.setMinimumWidth(49)
            name=_label(str(row.get("label","Input"))); name.setStyleSheet("font-weight:600")
            raw=str(row.get("detail","")); detail=_label(re.sub(r"([\\/_.-])",lambda m:m.group(1)+"\u200b",raw),True)
            detail.setToolTip(raw); detail.setMinimumWidth(0); detail.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
            grid.addWidget(state,0,0,2,1); grid.addWidget(name,0,1); grid.addWidget(detail,1,1)
            if status=="missing":
                fix=QPushButton("Go to input",objectName="inlineButton");fix.setAccessibleName(f"Go to input for {row.get('label','missing item')}");fix.clicked.connect(lambda _=False,value=dict(row):self.activated.emit(value));grid.addWidget(fix,0,2,2,1)
            details=[str(value) for value in row.get("details",[]) if str(value).strip()]
            if details:
                expanded=_label("\n".join(f"• {value}" for value in details),True); expanded.setWordWrap(True); expanded.hide(); grid.addWidget(expanded,3,1)
                toggle=QPushButton(f"Show warnings ({len(details)})"); toggle.setObjectName("warningToggle"); toggle.setCheckable(True); toggle.setMaximumWidth(180); grid.addWidget(toggle,2,1,alignment=Qt.AlignmentFlag.AlignLeft)
                def toggle_details(checked, *, target=expanded, button=toggle, count=len(details)):
                    target.setVisible(checked);button.setText("Hide warnings" if checked else f"Show warnings ({count})")
                toggle.toggled.connect(toggle_details)
            self.box.addWidget(panel)
        self.box.addStretch(1)

class FlightRow(QFrame):
    changed=Signal();remove_requested=Signal(object);move_requested=Signal(object,int);duplicate_requested=Signal(object)
    def __init__(self,number):
        super().__init__(objectName="flightRow");outer=QVBoxLayout(self);outer.setContentsMargins(12,8,12,10);outer.setSpacing(7)
        self._number=number;self._uses_default_name=True;self._setting_default_name=False
        header=QHBoxLayout();self.collapse_button=QPushButton("Hide",objectName="inlineButton");self.collapse_button.setFixedWidth(78);self.collapse_button.setCheckable(True);self.collapse_button.setChecked(True);self.collapse_button.setToolTip("Collapse or expand this flight");self.collapse_button.setAccessibleName("Collapse flight details");header.addWidget(self.collapse_button)
        self.name_edit=QLineEdit(f"Flight {number}");self.name_edit.setObjectName("flightTitleEdit");self.name_edit.setMaxLength(48);self.name_edit.setToolTip("Click to rename this flight (48 characters maximum).");self.name_edit.setMinimumWidth(120);self.name_edit.setMaximumWidth(420);header.addWidget(self.name_edit,1)
        self.readiness_badge=_label("NOT CHECKED");self.readiness_badge.setObjectName("readyBadge");self.readiness_badge.setFixedWidth(112);self.readiness_badge.setAlignment(Qt.AlignmentFlag.AlignCenter);header.addWidget(self.readiness_badge)
        self.up_button=QPushButton("↑",objectName="inlineButton");self.down_button=QPushButton("↓",objectName="inlineButton");self.duplicate_button=QPushButton("Duplicate",objectName="inlineButton")
        for button in (self.up_button,self.down_button):button.setFixedWidth(38)
        self.duplicate_button.setFixedWidth(100)
        for button,text in ((self.up_button,"Move flight up"),(self.down_button,"Move flight down"),(self.duplicate_button,"Duplicate flight configuration")):button.setToolTip(text);button.setAccessibleName(text);header.addWidget(button)
        self.remove_button=QPushButton("Remove");self.remove_button.setObjectName("removeFlightButton");self.remove_button.setFixedWidth(86);header.addWidget(self.remove_button);outer.addLayout(header)
        self.heading=self.name_edit
        self.compact_summary=_label("Choose flight and export folders.",True);self.compact_summary.hide();outer.addWidget(self.compact_summary)
        self.body=QWidget();grid=QGridLayout(self.body);grid.setContentsMargins(0,0,0,0);grid.setColumnMinimumWidth(0,145);grid.setColumnStretch(1,1);outer.addWidget(self.body)
        self.folder_edit,self.folder_button=self._path("Select or drop native Inspector flight folder…",True)
        self.las_edit,self.las_button=self._path("Select or drop matching Inspector export folder…",True)
        self.transform_source=NoWheelComboBox();self.transform_source.addItem("Upload matrix file","file");self.transform_source.addItem("Enter matrix values","values")
        self.transform_edit,self.transform_button=self._path("Optional 4 x 4 matrix file (blank = identity)…")
        self.transform_label=_label("Alignment matrix")
        grid.addWidget(_label("Native flight folder"),0,0);grid.addWidget(self.folder_edit,0,1);grid.addWidget(self.folder_button,0,2)
        grid.addWidget(_label("Inspector export folder"),1,0);grid.addWidget(self.las_edit,1,1);grid.addWidget(self.las_button,1,2)
        transform_row=QWidget();transform_box=QHBoxLayout(transform_row);transform_box.setContentsMargins(0,0,0,0);transform_box.setSpacing(7);transform_box.addWidget(self.transform_source);transform_box.addWidget(self.transform_edit,1)
        grid.addWidget(self.transform_label,2,0);grid.addWidget(transform_row,2,1);grid.addWidget(self.transform_button,2,2)
        self.matrix_text=QPlainTextEdit();self.matrix_text.setPlaceholderText("Four rows with four values each");self.matrix_text.setPlainText("1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1");self.matrix_text.setMaximumHeight(88);self.matrix_text.setStyleSheet("font-family:Consolas;font-size:9pt");grid.addWidget(self.matrix_text,3,1,1,2)
        self.folder_edit.setAccessibleName("Native Inspector flight folder");self.las_edit.setAccessibleName("Inspector export folder");self.transform_edit.setAccessibleName("Alignment matrix file")
        self.folder_edit.textChanged.connect(self.changed);self.las_edit.textChanged.connect(self.changed);self.name_edit.textChanged.connect(self._name_changed);self.name_edit.editingFinished.connect(self._finish_name_edit);self.transform_edit.textChanged.connect(self.changed);self.matrix_text.textChanged.connect(self.changed)
        self.remove_button.clicked.connect(lambda:self.remove_requested.emit(self));self.up_button.clicked.connect(lambda:self.move_requested.emit(self,-1));self.down_button.clicked.connect(lambda:self.move_requested.emit(self,1));self.duplicate_button.clicked.connect(lambda:self.duplicate_requested.emit(self));self.collapse_button.toggled.connect(self.set_expanded)
        self.folder_button.clicked.connect(self._browse_folder);self.las_button.clicked.connect(self._browse_las);self.transform_button.clicked.connect(self._browse_transform);self.transform_source.currentIndexChanged.connect(self._transform_source_changed)
        self.set_transform_visible(False)
    def set_expanded(self,value):
        self.body.setVisible(value);self.compact_summary.setVisible(not value);self.collapse_button.setText("Hide" if value else "Show");self.collapse_button.setAccessibleName(("Collapse" if value else "Expand")+" flight details")
    def set_readiness(self,report):
        ready=bool(report and report.get("ready"));self.readiness_badge.setText("READY" if ready else ("NEEDS ATTENTION" if report else "NOT CHECKED"));self.readiness_badge.setStyleSheet("color:#2e9d61;font-size:8pt;font-weight:700" if ready else "color:#b45f36;font-size:8pt;font-weight:700")
        if report:
            points=int(report.get("point_count") or 0);duration=float(report.get("video_duration_s") or 0);self.compact_summary.setText(f"{points:,} points · {AdvancedProcessingDialog._format_seconds(duration)} video · "+("Ready" if ready else "Needs attention"))
    def _name_changed(self,*_):
        if not self._setting_default_name:self._uses_default_name=False
        self.changed.emit()
    def _finish_name_edit(self):
        if not self.name_edit.text().strip():
            self._uses_default_name=True
            self.set_default_number(self._number)
    def set_default_number(self,number):
        self._number=number
        if self._uses_default_name:
            self._setting_default_name=True; self.name_edit.setText(f"Flight {number}"); self._setting_default_name=False
    @staticmethod
    def _path(placeholder,folder_drop=False):
        edit=FolderDropLineEdit() if folder_drop else QLineEdit(); edit.setPlaceholderText(placeholder); edit.setClearButtonEnabled(True); edit.setMinimumWidth(0)
        if folder_drop:edit.setToolTip("Drop one folder here to fill this field.")
        button=QPushButton("Browse…");button.setFixedWidth(100);return edit,button
    def _browse_folder(self):
        path=QFileDialog.getExistingDirectory(self,"Choose an Inspector flight folder",self.folder_edit.text())
        if path:self.folder_edit.setText(path)
    def _browse_las(self):
        path=QFileDialog.getExistingDirectory(self,"Choose Inspector export folder",self.las_edit.text() or self.folder_edit.text())
        if path:self.las_edit.setText(path)
    def _browse_transform(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose source-to-merged transform",self.transform_edit.text() or self.las_edit.text(),"Transformation matrix (*.txt *.csv);;All files (*)")
        if path:self.transform_edit.setText(path)
    def set_transform_visible(self,value):
        self.transform_label.setVisible(value);self.transform_source.setVisible(value);self._transforms_visible=value;self._transform_source_changed()
    def _transform_source_changed(self,*_):
        values=getattr(self,"_transforms_visible",False) and self.transform_source.currentData()=="values"
        files=getattr(self,"_transforms_visible",False) and not values
        self.transform_edit.setVisible(files);self.transform_button.setVisible(files);self.matrix_text.setVisible(values)
        self.changed.emit()
    def selection(self):
        values=self.matrix_text.toPlainText().strip() if self.transform_source.currentData()=="values" else None
        path=self.transform_edit.text().strip() or None if self.transform_source.currentData()=="file" else None
        return FlightSelection(self.folder_edit.text().strip(),self.las_edit.text().strip() or None,self.name_edit.text().strip(),path,values)
    def set_enabled(self,value):
        for x in (self.name_edit,self.folder_edit,self.folder_button,self.las_edit,self.las_button,self.transform_source,self.transform_edit,self.transform_button,self.matrix_text,self.remove_button,self.up_button,self.down_button,self.duplicate_button):x.setEnabled(value)

class FlightProgressPanel(QFrame):
    """Compact, expandable per-flight progress without replacing overall progress."""
    def __init__(self,parent=None):
        super().__init__(parent,objectName="flightProgressPanel");self._rows={};self._outputs={}
        root=QVBoxLayout(self);root.setContentsMargins(10,5,10,8);root.setSpacing(4)
        self.toggle=QPushButton("Hide flight progress",objectName="flightProgressToggle");self.toggle.setCheckable(True);self.toggle.setChecked(True);root.addWidget(self.toggle)
        self.body=QWidget();self.body_layout=QVBoxLayout(self.body);self.body_layout.setContentsMargins(3,0,3,0);self.body_layout.setSpacing(0);root.addWidget(self.body)
        self.toggle.toggled.connect(self._set_expanded);self.hide()
    def _set_expanded(self,expanded):
        self.body.setVisible(expanded);self.toggle.setText(("Hide" if expanded else "Show")+f" flight progress ({len(self._rows)})")
    def reset(self,names,outputs=None):
        while self.body_layout.count():
            item=self.body_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self._rows={};self._outputs=dict(outputs or {})
        for name in names:
            row=QFrame(objectName="flightProgressRow");grid=QGridLayout(row);grid.setContentsMargins(3,7,3,7);grid.setHorizontalSpacing(10);grid.setColumnStretch(1,1)
            title=_label(name);title.setStyleSheet("font-weight:600");title.setMinimumWidth(120)
            bar=QProgressBar();bar.setRange(0,1000);bar.setTextVisible(False)
            percent=_label("0%");percent.setMinimumWidth(38);percent.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
            status=_label("Waiting",True);status.setMinimumWidth(0)
            open_button=QPushButton("Open",objectName="inlineButton");open_button.setEnabled(False);open_button.setToolTip("Open this completed flight output");open_button.setAccessibleName(f"Open completed output for {name}");open_button.clicked.connect(lambda _=False,n=name:self._open_flight(n))
            if name not in self._outputs:open_button.hide()
            grid.addWidget(title,0,0);grid.addWidget(bar,0,1);grid.addWidget(percent,0,2);grid.addWidget(open_button,0,3);grid.addWidget(status,1,0,1,4)
            self.body_layout.addWidget(row);self._rows[name]=(bar,percent,status,open_button)
        self.toggle.setChecked(True);self._set_expanded(True);self.setVisible(bool(names))
    def update_flight(self,name,fraction,status):
        values=self._rows.get(name)
        if not values:return
        bar,percent,label,open_button=values
        if fraction is not None:
            value=max(0,min(1000,round(fraction*1000)));bar.setValue(max(bar.value(),value));percent.setText(f"{round(bar.value()/10):d}%")
        label.setText(status)
        if bar.value()>=1000 and name in self._outputs and Path(self._outputs[name]).exists():open_button.setEnabled(True)
    def finish_all(self,status="Complete"):
        for name,(bar,percent,label,open_button) in self._rows.items():
            bar.setValue(1000);percent.setText("100%");label.setText(status);open_button.setEnabled(name in self._outputs and Path(self._outputs[name]).exists())
    def stop_active(self,status):
        for bar,_percent,label,_open_button in self._rows.values():
            if bar.value()<1000:label.setText(status)
    def _open_flight(self,name):
        path=Path(self._outputs[name]).resolve()
        if path.exists():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

class MainWindow(QMainWindow):
    def __init__(self,backend=None,restore_settings=True,startup_hidden=False):
        super().__init__()
        self._startup_hidden=bool(startup_hidden)
        if self._startup_hidden:
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True);self.setWindowOpacity(0.0);self.setUpdatesEnabled(False)
        if backend is None:
            from . import service as backend
        self.backend=backend; self.settings=QSettings("EliosColorizer","EliosColorizer"); self.flight_rows=[]
        self._thread=self._worker=None; self._job_kind=""; self._job_selection=None; self._job_revision=self._revision=0
        self._inspection_pending=False; self._inspected_selection=None; self._ready=False; self._cancellation=threading.Event(); self._close_when_idle=False
        self._last_result=None; self._last_inspection_result=None; self._last_progress_message=""; self._run_started_at=None; self._progress_fraction=0.0; self._eta_seconds=None; self._eta_as_of=None; self._eta_samples=[]; self._eta_rate=None; self._dark_mode=False
        self.advanced_settings=AdvancedProcessingSettings();self.advanced_presets={};self.resource_preferences=ResourcePreferences()
        self._elapsed_timer=QTimer(self); self._elapsed_timer.setInterval(1000); self._elapsed_timer.timeout.connect(self._update_elapsed)
        self.setWindowTitle("Elios Colorizer");self.setAcceptDrops(True);self.resize(1080,920);self.setMinimumSize(820,680);self._build()
        self._debounce=QTimer(self); self._debounce.setSingleShot(True); self._debounce.setInterval(350); self._debounce.timeout.connect(self._start_pending_inspection)
        self.calibration_edit.textChanged.connect(self._calibration_changed);self.output_edit.textChanged.connect(self._update_actions);self.output_edit.textChanged.connect(self._update_processing_summaries)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed); self.alignment_combo.currentIndexChanged.connect(self._mode_changed); self.distance_check.toggled.connect(self._distance_changed)
        self.merged_edit.textChanged.connect(self._inputs_changed); self.cloudcompare_edit.textChanged.connect(self._inputs_changed);self.color_balance_check.toggled.connect(self._update_processing_summaries);self.distance_spin.valueChanged.connect(self._update_processing_summaries)
        if restore_settings:self._restore_settings()
        self.theme_button.setChecked(self._dark_mode);self._apply_theme()
        self._sync_aliases();self._show_empty();self._mode_changed();self._install_shortcuts();self._update_processing_summaries()
        if any(x.folder_edit.text().strip() for x in self.flight_rows):self._inputs_changed()
    def _build(self):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop)
        page=QWidget(objectName="page");page.setMaximumWidth(1900);root=QVBoxLayout(page); root.setContentsMargins(28,24,28,23); root.setSpacing(16)
        top=QHBoxLayout();title=_label("Elios Colorizer");title.setObjectName("title");top.addWidget(title);top.addStretch();self.theme_button=QPushButton();self.theme_button.setCheckable(True);self.theme_button.setChecked(self._dark_mode);self.theme_button.clicked.connect(self._toggle_theme);top.addWidget(self.theme_button);self.settings_button=QPushButton("⚙  Settings");self.settings_button.clicked.connect(self._show_application_settings);top.addWidget(self.settings_button);root.addLayout(top);root.addWidget(_label("Bring one or more flights' RGB imagery onto their recorded point clouds.",True))
        self.workflow_layout=QGridLayout();self.workflow_layout.setHorizontalSpacing(16);self.workflow_layout.setVerticalSpacing(16);root.addLayout(self.workflow_layout)
        card,box=_card("Choose Flights"); box.addWidget(_label("For each flight, select its native Inspector folder and matching export folder. The export must contain the LAS/LAZ and *-trajectory.csv. One camera profile is shared by all flights.",True)); self.flights_layout=QVBoxLayout(); box.addLayout(self.flights_layout)
        add_row=QHBoxLayout();self.add_flight_button=QPushButton("+  Add flight");self.add_flight_button.setObjectName("addFlightButton");self.add_flight_button.clicked.connect(self._add_flight);add_row.addWidget(self.add_flight_button);self.bulk_add_button=QPushButton("Import Flights…");self.bulk_add_button.setIcon(QIcon(str(_asset_path("import-flights.svg"))));self.bulk_add_button.clicked.connect(self._bulk_add_flights);add_row.addWidget(self.bulk_add_button);add_row.addStretch();add_row.addWidget(_label("Tip: drop a folder into a field, or elsewhere to add a flight.",True));box.addLayout(add_row)
        self.calibration_edit,self.calibration_button=self._file_row(box,"Camera profile","Choose shared RGB calibration…",self._browse_calibration); self.flight_card=card;self.flight_card.setObjectName("flightsCard"); self._add_flight(trigger=False)
        card,box=_card("Check All Flight Data"); self.checklist=Checklist();self.checklist.activated.connect(self._navigate_to_check); box.addWidget(self.checklist); self.summary=_label("Choose flight folders.",True); box.addWidget(self.summary)
        row=QHBoxLayout(); self.refresh_button=QPushButton("↻  Refresh checks"); self.refresh_button.clicked.connect(self._inputs_changed); row.addWidget(self.refresh_button); row.addStretch(); self.tools_summary=_label("Local libraries are checked automatically.",True); row.addWidget(self.tools_summary); box.addLayout(row); self.dependencies=Checklist(); self.dependencies.hide(); box.addWidget(self.dependencies); self.check_card=card;self.check_card.setObjectName("validationCard")
        card,box=_card("Choose Processing and Output"); card.setObjectName("processingCard");section_title=box.itemAt(0).widget();section_title.setWordWrap(False);section_title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);box.removeWidget(section_title);section_header=QHBoxLayout();section_header.addWidget(section_title,1);self.advanced_button=QPushButton("Advanced processing settings…");self.advanced_button.setObjectName("accentButton");self.advanced_button.clicked.connect(self._show_advanced_settings);section_header.addWidget(self.advanced_button);box.insertLayout(0,section_header);self._update_advanced_summary(); row=QHBoxLayout(); self.mode_label=_label("Result"); row.addWidget(self.mode_label); self.mode_combo=NoWheelComboBox(); self.mode_combo.addItem("Separate colorized LAS files","separate"); self.mode_combo.addItem("Align and merge into one cloud","merge"); row.addWidget(self.mode_combo,1); box.addLayout(row)
        self.mode_help=_label("",True); box.addWidget(self.mode_help)
        self.processing_plan_label=_label("Hardware strategy will be selected automatically when processing starts.",True);self.processing_plan_label.setObjectName("callout");box.addWidget(self.processing_plan_label)
        chips=QHBoxLayout();self.settings_chips=[]
        for text in ("1 fps","Exclude outer 2%","Normal blur","Fusion Off","Full video"):
            chip=_label(text);chip.setObjectName("settingsChip");chip.setAlignment(Qt.AlignmentFlag.AlignCenter);chips.addWidget(chip);self.settings_chips.append(chip)
        chips.addStretch();box.addLayout(chips)
        self.preflight_panel=QFrame(objectName="preflightPanel");preflight_box=QVBoxLayout(self.preflight_panel);preflight_box.setContentsMargins(10,8,10,8);preflight_box.addWidget(_label("Preflight workload",False));self.preflight_summary=_label("Select and check flight data to estimate the workload.",True);preflight_box.addWidget(self.preflight_summary);box.addWidget(self.preflight_panel)
        self.alignment_panel=QFrame(objectName="alignmentPanel"); align_box=QVBoxLayout(self.alignment_panel); align_box.setContentsMargins(12,10,12,10); align_box.setSpacing(8)
        row=QHBoxLayout(); row.addWidget(_label("Alignment")); self.alignment_combo=NoWheelComboBox(); self.alignment_combo.addItem("Use a CloudCompare-aligned merged cloud (Recommended)","manual"); self.alignment_combo.addItem("Align automatically with CloudCompare","automatic"); row.addWidget(self.alignment_combo,1); align_box.addLayout(row)
        self.alignment_help=_label(""); self.alignment_help.setObjectName("callout"); align_box.addWidget(self.alignment_help)
        self.alignment_guide_button=QPushButton("CloudCompare instructions…",objectName="accentButton"); self.alignment_guide_button.clicked.connect(self._show_alignment_guide); align_box.addWidget(self.alignment_guide_button,alignment=Qt.AlignmentFlag.AlignLeft)
        self.manual_panel=QWidget(); manual_box=QVBoxLayout(self.manual_panel); manual_box.setContentsMargins(0,0,0,0); self.merged_edit,self.merged_button=self._file_row(manual_box,"Merged cloud","Choose or drop the aligned, merged LAS/LAZ…",self._browse_merged,LasDropLineEdit); align_box.addWidget(self.manual_panel)
        self.automatic_panel=QWidget(); automatic_box=QVBoxLayout(self.automatic_panel); automatic_box.setContentsMargins(0,0,0,0); self.cloudcompare_edit,self.cloudcompare_button=self._file_row(automatic_box,"CloudCompare","Detected automatically, or browse to CloudCompare.exe…",self._browse_cloudcompare); align_box.addWidget(self.automatic_panel)
        box.addWidget(self.alignment_panel)
        row=QHBoxLayout(); self.distance_check=QCheckBox("Limit colorization distance"); self.distance_check.setObjectName("distanceToggle"); self.distance_spin=QDoubleSpinBox(); self.distance_spin.setRange(.2,40); self.distance_spin.setValue(8); self.distance_spin.setSuffix(" m"); row.addWidget(self.distance_check); row.addWidget(self.distance_spin); row.addStretch(); box.addLayout(row)
        row=QHBoxLayout(); self.color_balance_check=QCheckBox("Illumination Balancing"); self.color_balance_check.setObjectName("distanceToggle"); self.color_balance_check.setToolTip("Use overlapping views to conservatively correct center-to-edge lighting. Leaves RGB unchanged when evidence is insufficient."); row.addWidget(self.color_balance_check); row.addStretch(); box.addLayout(row)
        self.output_edit,self.output_button=self._file_row(box,"Output","Choose output…",self._browse_output);self.output_preview=_label("Choose an output location to preview generated files.",True);box.addWidget(self.output_preview)
        self.progress_detail=_label("",True); box.addWidget(self.progress_detail)
        self.flight_progress=FlightProgressPanel();box.addWidget(self.flight_progress)
        self.result_panel=QFrame(objectName="resultPanel");result_box=QVBoxLayout(self.result_panel);result_box.setContentsMargins(12,10,12,10);self.result_headline=_label("Processing complete");self.result_headline.setObjectName("resultHeadline");result_box.addWidget(self.result_headline);self.result_summary=_label("",True);result_box.addWidget(self.result_summary);row=QHBoxLayout();self.open_output_button=QPushButton("Open output");self.open_report_button=QPushButton("Open processing report");self.open_output_button.clicked.connect(self._open_output);self.open_report_button.clicked.connect(self._open_report);row.addWidget(self.open_output_button);row.addWidget(self.open_report_button);row.addStretch();result_box.addLayout(row);self.result_panel.hide();box.addWidget(self.result_panel)
        row=QHBoxLayout();row.addStretch(); self.details_button=QPushButton("Processing details"); self.details_button.setCheckable(True); row.addWidget(self.details_button); box.addLayout(row)
        self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(1500); self.log.setMaximumHeight(150); self.log.hide(); self.details_button.toggled.connect(self.log.setVisible); box.addWidget(self.log);box.addStretch(1);self.processing_card=card
        root.addStretch();scroll.setWidget(page)
        self.sticky_bar=QFrame(objectName="stickyBar");sticky=QGridLayout(self.sticky_bar);sticky.setContentsMargins(18,9,18,10);sticky.setColumnStretch(0,1)
        self.state_label=_label("Select a flight to begin.");sticky.addWidget(self.state_label,0,0)
        self.cancel_button=QPushButton("Cancel");self.cancel_button.clicked.connect(self._cancel);self.cancel_button.hide();sticky.addWidget(self.cancel_button,0,1)
        self.run_button=QPushButton("Colorize point cloud",objectName="primary");self.run_button.clicked.connect(self._start_colorization);sticky.addWidget(self.run_button,0,2)
        self.progress_bar=QProgressBar();self.progress_bar.setRange(0,1000);self.progress_bar.setTextVisible(False);sticky.addWidget(self.progress_bar,1,0,1,2)
        self.progress_badge=_label("0%");self.progress_badge.setObjectName("percentBadge");self.progress_badge.setAlignment(Qt.AlignmentFlag.AlignCenter);self.progress_badge.setMinimumWidth(58);sticky.addWidget(self.progress_badge,1,2)
        self.elapsed_label=_label("Elapsed: 00:00",True);sticky.addWidget(self.elapsed_label,2,0);self.eta_label=_label("Estimated time left: —",True);self.eta_label.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter);sticky.addWidget(self.eta_label,2,1,1,2)
        shell=QWidget(objectName="appShell");shell_layout=QVBoxLayout(shell);shell_layout.setContentsMargins(0,0,0,0);shell_layout.setSpacing(0);shell_layout.addWidget(scroll,1);shell_layout.addWidget(self.sticky_bar);self.setCentralWidget(shell);self.app_shell=shell;self.app_shell_layout=shell_layout;self.drop_overlay=QFrame(shell,objectName="folderDropOverlay");self.drop_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True);self.drop_overlay.hide();self._application_drop_active=False;self.scroll_area=scroll;self._wide_layout=None;self._layout_state=None;self._apply_responsive_layout()
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"drop_overlay"):self.drop_overlay.setGeometry(self.app_shell.rect())
        if hasattr(self,"workflow_layout"):self._apply_responsive_layout()
    def _apply_responsive_layout(self,available_width=None):
        wide=(self.width() if available_width is None else available_width)>=1480
        state=wide
        if state==self._layout_state:return
        for card in (self.flight_card,self.check_card,self.processing_card):self.workflow_layout.removeWidget(card)
        if wide:
            self.workflow_layout.addWidget(self.flight_card,0,0)
            self.workflow_layout.addWidget(self.check_card,1,0)
            self.workflow_layout.addWidget(self.processing_card,0,1,2,1)
            self.workflow_layout.setColumnStretch(0,1);self.workflow_layout.setColumnStretch(1,1)
        else:
            self.workflow_layout.addWidget(self.flight_card,0,0)
            self.workflow_layout.addWidget(self.check_card,1,0)
            self.workflow_layout.addWidget(self.processing_card,2,0)
            self.workflow_layout.setColumnStretch(0,1);self.workflow_layout.setColumnStretch(1,0)
        self._wide_layout=wide;self._layout_state=state
    @staticmethod
    def _file_row(box,title,placeholder,callback,edit_type=QLineEdit):
        row=QHBoxLayout(); label=_label(title); label.setMinimumWidth(95); row.addWidget(label); edit=edit_type(); edit.setPlaceholderText(placeholder); edit.setClearButtonEnabled(True); edit.setMinimumWidth(0); row.addWidget(edit,1); button=QPushButton("Browse…");button.setFixedWidth(100);button.clicked.connect(callback);row.addWidget(button);box.addLayout(row);return edit,button
    def _add_flight(self,checked=False,trigger=True):
        row=FlightRow(len(self.flight_rows)+1);row.changed.connect(self._inputs_changed);row.remove_requested.connect(self._remove_flight);row.move_requested.connect(self._move_flight);row.duplicate_requested.connect(self._duplicate_flight);self.flight_rows.append(row);self.flights_layout.addWidget(row);self._renumber();self._sync_aliases()
        if trigger:self._inputs_changed();self._mode_changed()
        return row
    def _remove_flight(self,row):
        if len(self.flight_rows)==1:return
        self.flight_rows.remove(row); row.deleteLater(); self._renumber(); self._sync_aliases()
        if len(self.flight_rows)==1 and self.mode_combo.currentData()=="merge":self.mode_combo.setCurrentIndex(0)
        self._inputs_changed(); self._mode_changed()
    def _move_flight(self,row,direction):
        index=self.flight_rows.index(row);target=index+direction
        if not 0<=target<len(self.flight_rows):return
        self.flight_rows[index],self.flight_rows[target]=self.flight_rows[target],self.flight_rows[index]
        self.flights_layout.removeWidget(row);self.flights_layout.insertWidget(target,row);self._renumber();self._sync_aliases();self._inputs_changed();self._mode_changed()
    def _duplicate_flight(self,source):
        row=self._add_flight(trigger=False);row.folder_edit.setText(source.folder_edit.text());row.las_edit.setText(source.las_edit.text());row.name_edit.setText(f"{source.name_edit.text().strip()} copy"[:48]);row.transform_source.setCurrentIndex(source.transform_source.currentIndex());row.transform_edit.setText(source.transform_edit.text());row.matrix_text.setPlainText(source.matrix_text.toPlainText());self._inputs_changed();self._mode_changed()
    def _bulk_add_flights(self):
        dialog=QFileDialog(self,"Select one or more native Inspector flight folders");dialog.setFileMode(QFileDialog.FileMode.Directory);dialog.setOption(QFileDialog.Option.DontUseNativeDialog,True);dialog.setOption(QFileDialog.Option.ShowDirsOnly,True)
        for view in [*dialog.findChildren(QListView),*dialog.findChildren(QTreeView)]:view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if dialog.exec():self._add_flight_folders([Path(value) for value in dialog.selectedFiles()])
    def _add_flight_folders(self,folders):
        paths=[]
        for value in folders:
            path=Path(value)
            if path.is_dir() and path not in paths:paths.append(path)
        if not paths:return
        first=self.flight_rows[0]
        for index,path in enumerate(paths):
            row=first if index==0 and not first.folder_edit.text().strip() else self._add_flight(trigger=False)
            row.folder_edit.setText(str(path))
        self._inputs_changed();self._mode_changed()
    def dragEnterEvent(self,event):
        if any(Path(url.toLocalFile()).is_dir() for url in event.mimeData().urls()):self._set_application_drop_highlight(True);event.acceptProposedAction()
    def dragMoveEvent(self,event):
        if any(Path(url.toLocalFile()).is_dir() for url in event.mimeData().urls()):self._set_application_drop_highlight(True);event.acceptProposedAction()
    def dragLeaveEvent(self,event):
        self._set_application_drop_highlight(False);event.accept()
    def dropEvent(self,event):
        self._set_application_drop_highlight(False);self._add_flight_folders([Path(url.toLocalFile()) for url in event.mimeData().urls()]);event.acceptProposedAction()
    def _set_application_drop_highlight(self,active):
        active=bool(active)
        if not hasattr(self,"drop_overlay") or self._application_drop_active==active:return
        self._application_drop_active=active
        if active:self.drop_overlay.setGeometry(self.app_shell.rect());self.drop_overlay.show();self.drop_overlay.raise_()
        else:self.drop_overlay.hide()
    def _renumber(self):
        for i,row in enumerate(self.flight_rows,1):row.set_default_number(i);row.remove_button.setVisible(len(self.flight_rows)>1);row.up_button.setEnabled(i>1 and self._job_kind!="run");row.down_button.setEnabled(i<len(self.flight_rows) and self._job_kind!="run")
    def _sync_aliases(self):
        self.source_edit=self.flight_rows[0].folder_edit; self.las_edit=self.flight_rows[0].las_edit; self.source_button=self.flight_rows[0].folder_button; self.las_button=self.flight_rows[0].las_button
    def _install_shortcuts(self):
        self._shortcuts=[]
        for keys,callback in (("Ctrl+Shift+A",self._add_flight),("Ctrl+R",self._inputs_changed),("Ctrl+Return",self._start_colorization),("Ctrl+,",self._show_advanced_settings),("Ctrl+Shift+,",self._show_application_settings)):
            shortcut=QShortcut(QKeySequence(keys),self);shortcut.activated.connect(callback);self._shortcuts.append(shortcut)
        self.add_flight_button.setAccessibleName("Add one flight");self.bulk_add_button.setAccessibleName("Import multiple flight folders");self.run_button.setAccessibleName("Start colorization");self.progress_bar.setAccessibleName("Overall processing progress");self.advanced_button.setAccessibleName("Open advanced processing settings");self.settings_button.setAccessibleName("Open application settings")
        self.setTabOrder(self.source_edit,self.las_edit);self.setTabOrder(self.las_edit,self.calibration_edit);self.setTabOrder(self.calibration_edit,self.mode_combo);self.setTabOrder(self.mode_combo,self.output_edit);self.setTabOrder(self.output_edit,self.run_button)
    def _navigate_to_check(self,item):
        text=f"{item.get('label','')} {item.get('detail','')}";target=None
        for index,row in enumerate(self.flight_rows,1):
            if re.search(rf"\bFlight\s+{index}\b",text,re.I) or row.name_edit.text().strip().casefold() in text.casefold():target=row;break
        lower=text.casefold()
        if "calibration" in lower:widget=self.calibration_edit
        elif "merged point" in lower:widget=self.merged_edit
        elif "cloudcompare" in lower:widget=self.cloudcompare_edit
        elif "transform" in lower and target:widget=target.transform_edit if target.transform_source.currentData()=="file" else target.matrix_text
        elif "point cloud" in lower and target:widget=target.las_edit
        else:widget=(target or self.flight_rows[0]).folder_edit
        if target:target.collapse_button.setChecked(True)
        self._set_processing_focus(False);self.scroll_area.ensureWidgetVisible(widget,30,80);widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
    @staticmethod
    def _output_stem(value):
        return re.sub(r'[^A-Za-z0-9._-]+','_',value).strip('._-')[:80]
    def _planned_outputs(self,selection=None):
        selection=selection or self._selection();value=self.output_edit.text().strip()
        if not value:return {}
        if selection.mode=="separate" and len(selection.flights)>1:
            folder=Path(value);result={};used=set()
            for index,flight in enumerate(selection.flights,1):
                custom=self._output_stem(flight.name)
                if custom.casefold()==f"flight {index}".casefold():custom=""
                stem=f"Flight {index:02d}"+(f" - {custom}" if custom else "");name=f"{stem} - Colorized.las"
                while name.casefold() in used:name=f"{stem} ({len(used)+1}) - Colorized.las"
                used.add(name.casefold());result[flight.name or f"Flight {index}"]=folder/name
            return result
        return {"Final output":Path(value)}
    def _update_processing_summaries(self,*_):
        if not hasattr(self,"settings_chips"):return
        advanced=self.advanced_settings;blur=next((label for label,value in AdvancedProcessingDialog.BLUR_LEVELS if value==advanced.minimum_sharpness),"Custom")
        window=("Full video" if not advanced.time_range_enabled else f"{AdvancedProcessingDialog._format_seconds(advanced.start_time_s)}–"+(AdvancedProcessingDialog._format_seconds(advanced.end_time_s) if advanced.end_time_s is not None else "end"))
        values=(f"{advanced.sample_frequency_hz:g} fps",f"Exclude outer {advanced.image_edge_exclusion_percent:g}%",f"{blur} blur",f"Fusion {'On' if advanced.multi_frame_fusion else 'Off'}",window)
        for label,text in zip(self.settings_chips,values):label.setText(text)
        selection=self._selection();planned=self._planned_outputs(selection)
        conflicts=[path.name for path in planned.values() if path.exists()]
        if selection.mode=="separate" and len(selection.flights)>1 and self.output_edit.text().strip() and (Path(self.output_edit.text().strip())/"multi_flight_report.json").exists():conflicts.append("multi_flight_report.json")
        if not planned:self.output_preview.setText("Choose an output location to preview generated files.")
        else:
            names=[path.name for path in planned.values()];shown=", ".join(names[:3])+(f" and {len(names)-3} more" if len(names)>3 else "");self.output_preview.setText(("Conflict: " if conflicts else "Will create: ")+shown+(f". Existing: {', '.join(conflicts[:3])}" if conflicts else ""));self.output_preview.setStyleSheet("color:#b34330;font-weight:600" if conflicts else "")
        report=self._last_inspection_result or {};reports=report.get("flights") or ([report] if report else []);points=[int(item.get("point_count") or 0) for item in reports];durations=[float(item.get("video_duration_s") or 0) for item in reports]
        frames=0
        for duration in durations:
            start=advanced.start_time_s if advanced.time_range_enabled else 0.;end=min(duration,advanced.end_time_s) if advanced.time_range_enabled and advanced.end_time_s is not None else duration;frames+=max(0,round((end-start)*advanced.sample_frequency_hz))
        strategy=("Single flight" if len(selection.flights)==1 else ("Parallel separate outputs" if selection.mode=="separate" else ("Parallel merged map/reduce" if not advanced.multi_frame_fusion and not selection.illumination_balancing else "Shared merged observation stream")))
        try:
            from .cuda_backend import cuda_checkpoint
            from .colorize import _available_memory_bytes
            from .runtime import plan_resources
            checkpoint=cuda_checkpoint();plan=plan_resources(max(points,default=0),available_memory_bytes=_available_memory_bytes(),cuda_available=checkpoint.available,cuda_devices=checkpoint.device_count if checkpoint.available else 0,flight_count=len(selection.flights),fusion=advanced.multi_frame_fusion,available_vram_bytes=getattr(checkpoint,"free_memory_bytes",None),preferences=self.resource_preferences);hardware=(f"{plan.cuda_devices} CUDA GPU(s)" if plan.cuda_available else f"CPU only ({plan.worker_threads} worker(s))");jobs=plan.concurrent_flights
        except Exception:hardware=f"CPU ({os.cpu_count() or 1} logical cores)";jobs=1
        disk=self._recommended_free_space_bytes(selection)/1024**3 if reports else 0
        mode="recommended resources" if self.resource_preferences.use_recommended else "custom resource limits"
        self.preflight_summary.setText(f"{sum(points):,} input points · approximately {frames:,} sampled frames · {disk:.1f} GiB recommended free space\n{strategy} · {hardware} · up to {jobs} concurrent flight job(s) · {mode}")
    def _set_processing_focus(self,active):
        if active:self.scroll_area.ensureWidgetVisible(self.processing_card,20,40)
    def _duplicate_name_rows(self):
        groups={}
        for row in self.flight_rows:
            normalized=" ".join(row.name_edit.text().split()).casefold()
            if normalized:groups.setdefault(normalized,[]).append(row)
        duplicates={row for rows in groups.values() if len(rows)>1 for row in rows}
        for row in self.flight_rows:
            duplicate=row in duplicates;row.name_edit.setProperty("duplicateName",duplicate);row.name_edit.style().unpolish(row.name_edit);row.name_edit.style().polish(row.name_edit)
            row.name_edit.setToolTip("Flight names must be unique." if duplicate else "Click to rename this flight (48 characters maximum).")
        return duplicates
    def _selection(self):
        return WorkflowSelection(flights=tuple(x.selection() for x in self.flight_rows),
            calibration_override=self.calibration_edit.text().strip() or None,
            mode=str(self.mode_combo.currentData()),
            maximum_color_distance_m=self.distance_spin.value() if self.distance_check.isChecked() else None,
            alignment_method=str(self.alignment_combo.currentData()),merged_source=self.merged_edit.text().strip() or None,
            cloudcompare_executable=self.cloudcompare_edit.text().strip() or None,
            illumination_balancing=self.color_balance_check.isChecked(),advanced=self.advanced_settings,resources=self.resource_preferences)
    def _inspection_selection(self):
        return replace(self._selection(),illumination_balancing=False,advanced=AdvancedProcessingSettings(),resources=ResourcePreferences())

    def _show_application_settings(self):
        dialog=ResourceSettingsDialog(self.resource_preferences,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.resource_preferences=dialog.values();self._save_settings();self._update_processing_summaries()
    def _show_advanced_settings(self):
        durations=[]
        if self._last_inspection_result:
            reports=self._last_inspection_result.get("flights") or [self._last_inspection_result]
            durations=[float(item["video_duration_s"]) for item in reports if item.get("video_duration_s")]
        available_duration=min(durations) if durations else None
        dialog=AdvancedProcessingDialog(self.advanced_settings,self,presets=self.advanced_presets,available_duration_s=available_duration)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.advanced_settings=dialog.values();self.advanced_presets=dialog.presets();self._save_advanced_presets();self._update_advanced_summary();self._update_processing_summaries()
    def _update_advanced_summary(self):
        if not hasattr(self,"advanced_button"):return
        blur=next((label for label,value in AdvancedProcessingDialog.BLUR_LEVELS if value==self.advanced_settings.minimum_sharpness),"Custom")
        window=("all video" if not self.advanced_settings.time_range_enabled else
                f"{AdvancedProcessingDialog._format_seconds(self.advanced_settings.start_time_s)}–"+
                (AdvancedProcessingDialog._format_seconds(self.advanced_settings.end_time_s) if self.advanced_settings.end_time_s is not None else "end"))
        fusion="Fusion on" if self.advanced_settings.multi_frame_fusion else "Fusion off"
        self.advanced_button.setToolTip(f"Current: {self.advanced_settings.sample_frequency_hz:g} fps · {self.advanced_settings.image_edge_exclusion_percent:g}% edges · {blur} blur rejection · {fusion} · {window}")
    def _restore_settings(self):
        calibration=str(self.settings.value("calibration","") or "")
        saved_theme=self.settings.value("dark_mode",False)
        self._dark_mode=(saved_theme is True or str(saved_theme).strip().lower() in ("1","true","yes","on"))
        try:self.resource_preferences=ResourcePreferences.from_dict(json.loads(str(self.settings.value("resource_preferences_json","{}") or "{}")))
        except (ValueError,TypeError,json.JSONDecodeError):self.resource_preferences=ResourcePreferences()
        self.advanced_presets={}
        try:raw=json.loads(str(self.settings.value("advanced_presets_json","{}") or "{}"))
        except (ValueError,TypeError,json.JSONDecodeError):raw={}
        if isinstance(raw,dict):
            for name,value in raw.items():
                name=" ".join(str(name).split())
                if not name or len(name)>48 or name.casefold() in ("default settings","custom settings"):continue
                try:self.advanced_presets[name]=AdvancedProcessingSettings.from_dict(value)
                except (ValueError,TypeError,OverflowError):continue
        for key in ("flights","source","output","mode","distance_enabled","distance_m"):
            self.settings.remove(key)
        self.calibration_edit.setText(calibration)
    def _save_advanced_presets(self):
        payload={name:value.to_dict() for name,value in self.advanced_presets.items()}
        self.settings.setValue("advanced_presets_json",json.dumps(payload,sort_keys=True,separators=(",",":")))
    def _save_settings(self):
        calibration=self.calibration_edit.text().strip()
        if calibration:self.settings.setValue("calibration",calibration)
        else:self.settings.remove("calibration")
        self.settings.setValue("dark_mode",self._dark_mode)
        self.settings.setValue("resource_preferences_json",json.dumps(self.resource_preferences.to_dict(),sort_keys=True,separators=(",",":")))
    def _calibration_changed(self,*_):
        self._save_settings();self._inputs_changed()
    def _apply_theme(self):
        self.setStyleSheet(_DARK_STYLE if self._dark_mode else _LIGHT_STYLE)
        if hasattr(self,"theme_button"):self.theme_button.setIcon(QIcon(str(_asset_path("theme-moon.svg" if self._dark_mode else "theme-sun.svg"))));self.theme_button.setText("Light mode" if self._dark_mode else "Dark mode")
    def _toggle_theme(self):self._dark_mode=self.theme_button.isChecked();self._apply_theme();self._save_settings()
    def _show_empty(self):self.checklist.set_rows([{"label":"Flight inputs","status":"warning","detail":"Select a folder for every flight."}])
    def _browse_calibration(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose RGB camera profile",self.calibration_edit.text() or self.source_edit.text(),"Camera profile (*.json)")
        if path:self.calibration_edit.setText(path)
    def _browse_output(self):
        if self.mode_combo.currentData()=="separate" and len(self.flight_rows)>1:path=QFileDialog.getExistingDirectory(self,"Choose output folder",self.output_edit.text())
        else:
            path,_=QFileDialog.getSaveFileName(self,"Save colorized point cloud",self.output_edit.text(),"LAS point cloud (*.las)",options=QFileDialog.Option.DontConfirmOverwrite)
            if path and not Path(path).suffix:path+=".las"
        if path:self.output_edit.setText(path)
    def _browse_merged(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose aligned merged point cloud",self.merged_edit.text(),"Point cloud (*.las *.laz)")
        if path:self.merged_edit.setText(path)
    def _browse_cloudcompare(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose CloudCompare executable",self.cloudcompare_edit.text(),"CloudCompare (CloudCompare.exe);;Executable (*.exe)")
        if path:self.cloudcompare_edit.setText(path)
    def _show_alignment_guide(self):
        QMessageBox.information(self,"CloudCompare alignment workflow",
            "1. Load every original flight LAS/LAZ into CloudCompare.\n"
            "2. Keep one cloud fixed. Roughly align and run ICP between clouds with strong overlap; use overlapping chains when a flight does not overlap the fixed cloud.\n"
            "3. Add redundant overlap links where possible and inspect loop closure. Do not accept a pair that improves locally but breaks another aligned overlap.\n"
            "4. Record each flight's final cumulative 4 x 4 source-to-merged transform. The direction must be original source → merged coordinates.\n"
            "5. Apply the final transforms, remove noise if desired, merge the aligned clouds, and save one LAS/LAZ.\n"
            "6. Select that merged cloud here and assign each final matrix to its matching flight. Leave a matrix blank only when that flight remained in the final coordinate frame.")
    def _mode_changed(self,*_):
        multiple=len(self.flight_rows)>1
        if not multiple and self.mode_combo.currentData()!="separate":self.mode_combo.setCurrentIndex(0);return
        self.mode_label.setVisible(multiple);self.mode_combo.setVisible(multiple);self.mode_help.setVisible(multiple)
        merge=self.mode_combo.currentData()=="merge"
        manual=self.alignment_combo.currentData()=="manual"
        self.alignment_panel.setVisible(multiple and merge);self.manual_panel.setVisible(manual);self.automatic_panel.setVisible(not manual)
        for row in self.flight_rows:row.set_transform_visible(multiple and merge and manual)
        if merge:
            self.mode_help.setText("Merged processing colorizes one final geometry with all flights. Point clouds should already be closely aligned in Inspector's coordinate system.");self.run_button.setText("Colorize merged point cloud")
            if manual:self.alignment_help.setText("In CloudCompare, build alignment links between strongly overlapping clouds, verify loops, apply each final cumulative transform, then merge and save one LAS/LAZ. Supply each source-to-merged 4 x 4 matrix below.")
            else:self.alignment_help.setText("The tool finds overlapping flight pairs, validates pairwise CloudCompare ICP, and globally optimizes the connected network with Flight 1 fixed. Disconnected or inconsistent networks stop for manual alignment.")
        elif multiple:self.mode_help.setText("Colorize each flight independently and save one LAS per flight; alignment is skipped.");self.run_button.setText("Colorize all flights separately")
        else:self.run_button.setText("Colorize point cloud")
        if not merge:
            for row in self.flight_rows:row.set_transform_visible(False)
        self._inputs_changed()
        self._update_actions()
    def _distance_changed(self,*_):self.distance_spin.setEnabled(self.distance_check.isChecked() and self._job_kind!="run");self._update_actions()
    def _inputs_changed(self,*_):
        self._revision+=1;self._ready=False;self._inspected_selection=None;self._last_inspection_result=None;self._inspection_pending=True;self.run_button.setEnabled(False);self.summary.setText("Waiting for the current selection to be checked.");self.result_panel.hide();self._update_processing_summaries();self._debounce.start()
    def _start_pending_inspection(self):
        if self._thread is not None:self._inspection_pending=True;return
        self._debounce.stop();self._inspection_pending=False;selection=self._inspection_selection()
        duplicates=self._duplicate_name_rows()
        if duplicates:self.checklist.set_rows([{"label":"Flight names","status":"missing","detail":"Give every flight a unique name."}]);self.summary.setText("Duplicate flight names must be renamed.");self._update_actions();return
        if any(not x.folder for x in selection.flights):self._show_empty();self.summary.setText("Choose a folder for every flight.");self._update_actions();return
        self.state_label.setText("Checking all flight data…");self._launch_worker("inspect",selection)
    def _launch_worker(self,kind,selection,output=""):
        self._job_kind=kind;self._job_selection=selection;self._job_revision=self._revision;self._cancellation=threading.Event();self._thread=QThread(self);self._worker=ServiceWorker(self.backend,kind,selection,self._cancellation,output);self._worker.moveToThread(self._thread);self._thread.started.connect(self._worker.run);self._worker.result.connect(self._on_result);self._worker.error.connect(self._on_error);self._worker.progress.connect(self._on_progress);self._worker.finished.connect(self._thread.quit);self._worker.finished.connect(self._worker.deleteLater);self._thread.finished.connect(self._thread_finished);self._thread.finished.connect(self._thread.deleteLater);self._update_actions();self._thread.start()
    def _on_result(self,result):
        if self._job_kind=="inspect":
            if self._job_revision!=self._revision or self._job_selection!=self._inspection_selection():return
            detected=result.get("cloudcompare_executable")
            self.cloudcompare_edit.setPlaceholderText(f"Automatically detected: {detected}" if detected else "Not detected; install CloudCompare or browse to CloudCompare.exe…")
            self.cloudcompare_edit.setToolTip(str(detected or "CloudCompare CLI is required only for automatic alignment."))
            self._ready=bool(result.get("ready"));self._last_inspection_result=result;self._inspected_selection=self._job_selection;self.checklist.set_rows(result.get("checklist",[]));missing=[x for x in result.get("dependencies",[]) if x.get("status")=="missing"];self.dependencies.setVisible(bool(missing));self.dependencies.set_rows(missing);self.tools_summary.setText("All required local tools are ready." if not missing else "Some local tools need attention.");self.summary.setText(str(result.get("summary","")));self.state_label.setText("Ready to colorize." if self._ready else "Resolve required items above.")
            reports=result.get("flights") or ([result] if len(self.flight_rows)==1 else [])
            for index,row in enumerate(self.flight_rows):row.set_readiness(reports[index] if index<len(reports) else None)
            self._update_processing_summaries()
        else:
            self._last_result=result;total=int(result.get("total_points",0));colored=int(result.get("colored_points",0));coverage=f" ({colored/total:.1%})" if total and colored<=total else "";self.state_label.setText("Colorization complete.");self.progress_bar.setValue(1000);self.progress_badge.setText("100%");self.flight_progress.finish_all();self._progress_fraction=1.0;self._update_elapsed();self._elapsed_timer.stop();self.eta_label.setText("Estimated time left: Complete");self.progress_detail.setText(f"{colored:,} colored output points{coverage}. Saved to {result.get('output',self.output_edit.text())}");strategy=result.get("processing_strategy");self.processing_plan_label.setText((f"Completed with {strategy.replace('_',' ')} · {result.get('concurrent_flight_jobs',1)} flight job(s) · {result.get('cuda_devices_detected',0)} CUDA device(s)." if strategy else "Processing completed with the automatically selected hardware strategy."));self._show_result_summary(result);self._set_processing_focus(False);QTimer.singleShot(0,lambda:self.scroll_area.ensureWidgetVisible(self.result_panel,20,80))
    def _show_result_summary(self,result):
        total=int(result.get("total_points") or 0);colored=int(result.get("colored_points") or 0);coverage=colored/total*100 if total else 0.;details=[]
        report_path=result.get("report");payload={}
        try:
            if report_path and Path(report_path).is_file():payload=json.loads(Path(report_path).read_text(encoding="utf-8"))
        except (OSError,ValueError,TypeError):payload={}
        summary=payload.get("summary") or {};elapsed=summary.get("elapsed_seconds") or payload.get("elapsed_seconds");frames=summary.get("frames") or {};warnings=summary.get("warnings_count")
        if not frames and payload.get("results"):
            used=rejected=received=warning_total=0
            for item in payload["results"]:
                try:child=json.loads(Path(item["report"]).read_text(encoding="utf-8"));child_summary=child.get("summary") or {};child_frames=child_summary.get("frames") or {};used+=int(child_frames.get("used") or 0);rejected+=int(child_frames.get("rejected") or 0);received+=int(child_frames.get("received") or 0);warning_total+=int(child_summary.get("warnings_count") or 0)
                except (OSError,ValueError,TypeError,KeyError):continue
            frames={"used":used,"rejected":rejected,"received":received};warnings=warning_total
        if elapsed is not None:details.append(f"Runtime {self._format_duration(float(elapsed))}")
        if frames:details.append(f"{int(frames.get('used') or 0):,} frames used · {int(frames.get('rejected') or 0):,} rejected")
        strategy=result.get("processing_strategy") or summary.get("processing_strategy");
        if strategy:details.append(strategy.replace("_"," "))
        if warnings is not None:details.append(f"{warnings} warning(s)")
        self.result_headline.setText(f"Complete · {colored:,} of {total:,} points colored ({coverage:.1f}%)");self.result_summary.setText("\n".join(details) or "Output and processing report are ready.");self.open_output_button.setEnabled(bool(result.get("output")));self.open_report_button.setEnabled(bool(report_path));self.result_panel.show()
    def _on_error(self,message,details):
        if self._job_kind=="inspect":self._ready=False;self.summary.setText(message);self.checklist.set_rows([{"label":"Flight inspection","status":"missing","detail":message}])
        elif self._cancellation.is_set():self.state_label.setText("Colorization cancelled.");self.progress_detail.setText("Processing stopped.");self.flight_progress.stop_active("Cancelled")
        else:self.state_label.setText("Processing could not finish.");self.progress_detail.setText(message);self.flight_progress.stop_active("Stopped — see processing details");self.log.appendPlainText(details)
        self._elapsed_timer.stop()
        if self._job_kind=="run":self._update_elapsed();self.eta_label.setText("Estimated time left: —");self._set_processing_focus(False)
    def _thread_finished(self):
        if self._thread:self._thread.wait()
        self._thread=self._worker=None;self._job_kind="";self._update_actions()
        if self._close_when_idle:self.close();return
        if self._inspection_pending:QTimer.singleShot(0,self._start_pending_inspection)
    def _on_progress(self,stage,fraction,message):
        fraction=max(0,min(1,fraction));self._progress_fraction=max(self._progress_fraction,fraction)
        color_stage=stage.rsplit(": ",1)[-1] in ("Checking visibility","Assigning RGB","Fusing RGB observations","Parallel flight colorization","Selecting final colors")
        if self._run_started_at is not None and color_stage and fraction<.995:
            now=time.monotonic()
            if not self._eta_samples or (now-self._eta_samples[-1][0]>=1 and fraction>self._eta_samples[-1][1]):
                self._eta_samples.append((now,fraction));self._eta_samples=self._eta_samples[-300:]
                first_time,first_fraction=self._eta_samples[0]
                span=now-first_time;advance=fraction-first_fraction
                if len(self._eta_samples)>=3 and span>=10 and advance>=.002:
                    rate=advance/span
                    self._eta_rate=rate if self._eta_rate is None else .8*self._eta_rate+.2*rate
                    self._eta_seconds=max(1,(1-fraction)/self._eta_rate);self._eta_as_of=now
        display_message=self._update_flight_progress(stage,message)
        self.progress_bar.setValue(max(self.progress_bar.value(),round(fraction*1000)));self.progress_badge.setText(f"{round(self.progress_bar.value()/10):d}%");self.state_label.setText(stage);self.progress_detail.setText(display_message);entry=f"{stage}: {display_message}"
        stage_name=stage.rsplit(": ",1)[-1]
        if stage_name in ("NVIDIA CUDA","Planning parallel colorization","Parallel flight colorization","Selecting final colors"):self.processing_plan_label.setText(message)
        if entry!=self._last_progress_message:self.log.appendPlainText(entry);self._last_progress_message=entry
        self._update_elapsed()
    def _update_flight_progress(self,stage,message):
        match=re.search(r"\s*·\s*flight\s+(\d+(?:\.\d+)?)%$",message)
        local=float(match.group(1))/100 if match else None
        display=message[:match.start()].rstrip() if match else message
        for name in sorted(self.flight_progress._rows,key=len,reverse=True):
            if stage==name or stage.startswith(name+": "):
                status=stage[len(name):].lstrip(": ") or display
                if display and display not in status:status=f"{status} — {display}"
                self.flight_progress.update_flight(name,local,status)
                break
        return display
    @staticmethod
    def _format_duration(seconds):
        total=max(0,int(round(seconds)));hours,remainder=divmod(total,3600);minutes,seconds=divmod(remainder,60)
        return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
    def _update_elapsed(self):
        if self._run_started_at is not None:
            elapsed=time.monotonic()-self._run_started_at;self.elapsed_label.setText(f"Elapsed: {self._format_duration(elapsed)}")
            if self._progress_fraction>=.995:self.eta_label.setText("Estimated time left: Complete")
            elif not self._eta_samples:self.eta_label.setText("Estimated time left: Waiting for colorization…")
            elif self._eta_seconds is None:self.eta_label.setText("Estimated time left: Calculating…")
            else:
                remaining=max(1,self._eta_seconds-(time.monotonic()-(self._eta_as_of or time.monotonic())))
                self.eta_label.setText(f"Estimated time left: {self._format_duration(remaining)}")
    def _output_valid(self):
        value=self.output_edit.text().strip()
        if not value:return False
        if self.mode_combo.currentData()=="separate" and len(self.flight_rows)>1:
            path=Path(value);return (path.exists() and path.is_dir()) or not path.suffix
        return Path(value).suffix.lower()==".las"
    def _output_conflicts(self,selection=None):
        selection=selection or self._selection();conflicts=[path for path in self._planned_outputs(selection).values() if path.exists()]
        if selection.mode=="separate" and len(selection.flights)>1 and self.output_edit.text().strip():
            report=Path(self.output_edit.text().strip())/"multi_flight_report.json"
            if report.exists():conflicts.append(report)
        return conflicts
    def _recommended_free_space_bytes(self,selection):
        gib=1024**3
        report=self._last_inspection_result or {};flights=report.get("flights") or [report]
        counts=[max(0,int(item.get("point_count") or 0)) for item in flights]
        if selection.mode=="merge":
            base=(30 if selection.alignment_method=="automatic" else 25)*gib
            merged_estimate=max(counts,default=0)
            candidates=0 if selection.advanced.multi_frame_fusion else merged_estimate*16*len(counts)
            return base+candidates+(sum(counts)*65 if selection.advanced.multi_frame_fusion else 0)
        # Separate results accumulate in the output folder while the largest flight also
        # needs its color arrays and atomic output copy. Keep a 5 GiB practical floor.
        estimate=sum(counts)*50+max(counts,default=0)*(20+(65 if selection.advanced.multi_frame_fusion else 0))+gib
        return max(5*gib,estimate)
    @staticmethod
    def _existing_output_parent(output):
        path=Path(output).resolve();candidate=path if path.is_dir() else path.parent
        while not candidate.exists() and candidate!=candidate.parent:candidate=candidate.parent
        return candidate
    def _show_low_disk_warning(self,free_bytes,recommended_bytes,location):
        gib=1024**3
        box=QMessageBox(self);box.setIcon(QMessageBox.Icon.Warning);box.setWindowTitle("Low disk space")
        box.setText(f"Only {free_bytes/gib:.1f} GiB is free on the output drive.")
        box.setInformativeText(
            f"This workflow recommends at least {recommended_bytes/gib:.0f} GiB free for temporary files and safe final output writing. "
            f"Temporary work will use the drive containing:\n{location}\n\n"
            "Processing may fail near the end if the drive runs out of space.")
        continue_button=box.addButton("Continue anyway",QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel",QMessageBox.ButtonRole.RejectRole);box.setDefaultButton(box.buttons()[-1]);box.exec()
        return box.clickedButton() is continue_button
    def _disk_space_allows_start(self,selection,output):
        location=self._existing_output_parent(output)
        try:free=shutil.disk_usage(location).free
        except OSError:return True
        recommended=self._recommended_free_space_bytes(selection)
        return free>=recommended or self._show_low_disk_warning(free,recommended,location)
    def _update_actions(self):
        if not hasattr(self,"run_button"):return
        running=self._job_kind=="run";idle=self._thread is None;names_valid=not self._duplicate_name_rows()
        for row in self.flight_rows:row.set_enabled(not running)
        for x in (self.add_flight_button,self.bulk_add_button,self.calibration_edit,self.calibration_button,self.mode_combo,self.alignment_combo,self.alignment_guide_button,self.merged_edit,self.merged_button,self.cloudcompare_edit,self.cloudcompare_button,self.distance_check,self.color_balance_check,self.advanced_button,self.settings_button,self.output_edit,self.output_button,self.refresh_button):x.setEnabled(not running)
        self._renumber()
        self.distance_spin.setEnabled(not running and self.distance_check.isChecked());self.cancel_button.setVisible(running);self.cancel_button.setEnabled(running and not self._cancellation.is_set());merge_valid=self.mode_combo.currentData()!="merge" or len(self.flight_rows)>1;can=idle and names_valid and self._ready and self._inspected_selection==self._inspection_selection() and self._output_valid() and merge_valid and not self._inspection_pending and not self._output_conflicts();self.run_button.setEnabled(can)
    def _start_colorization(self):
        if not self.run_button.isEnabled():return
        selection=self._selection();output=Path(self.output_edit.text().strip())
        if len(selection.flights)==1 and selection.flights[0].las_override and output.resolve()==Path(selection.flights[0].las_override).resolve():QMessageBox.warning(self,"Choose a different output","Output must differ from source.");return
        if not(selection.mode=="separate" and len(selection.flights)>1) and output.exists():QMessageBox.warning(self,"Choose a new output","That output already exists.");return
        if not self._disk_space_allows_start(selection,output):return
        names=[flight.name or f"Flight {index}" for index,flight in enumerate(selection.flights,1)] if len(selection.flights)>1 else [];flight_outputs=self._planned_outputs(selection) if selection.mode=="separate" and len(selection.flights)>1 else {}
        self._save_settings();self._last_result=None;self.result_panel.hide();self.log.clear();self.progress_bar.setValue(0);self.progress_badge.setText("0%");self.flight_progress.reset(names,flight_outputs);self.processing_plan_label.setText("Analyzing CPU, memory, storage, and CUDA resources…");self._progress_fraction=0.0;self._eta_seconds=None;self._eta_as_of=None;self._eta_samples=[];self._eta_rate=None;self.elapsed_label.setText("Elapsed: 00:00");self.eta_label.setText("Estimated time left: Waiting for colorization…");self._run_started_at=time.monotonic();self._elapsed_timer.start();self._set_processing_focus(True);self._launch_worker("run",selection,str(output))
    def _cancel(self):self._cancellation.set();self.state_label.setText("Stopping safely…");self._update_actions()
    def _open_output(self):
        if self._last_result and self._last_result.get("output"):
            path=Path(self._last_result["output"]).resolve();QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else path.parent)))
    def _open_report(self):
        if self._last_result and self._last_result.get("report"):QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._last_result["report"]).resolve())))
    def closeEvent(self,event:QCloseEvent):
        if self._thread is not None:self._close_when_idle=True;self._inspection_pending=False;self._cancellation.set();event.ignore();return
        event.accept()

def _show_fully_initialized(window,app):
    """Polish and lay out the widget tree before creating a visible surface."""
    window.ensurePolished()
    if window.layout():window.layout().activate()
    if window.centralWidget() and window.centralWidget().layout():window.centralWidget().layout().activate()
    app.processEvents();window.hide();window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,False);window.setWindowOpacity(1.0);window.setUpdatesEnabled(True);window.show()

class StartupSplash(QFrame):
    """Intentional startup surface shown while the full workspace is prepared."""
    def __init__(self,dark=False):
        super().__init__(None,Qt.WindowType.SplashScreen|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("startupSplash");self.setFixedSize(470,225)
        background="#172838" if dark else "#f4fafc";border="#3b7489" if dark else "#70b2c5";text="#f1f7fa" if dark else "#12364b";muted="#a8bfce" if dark else "#587184";track="#2b4252" if dark else "#d8e7ed"
        self.setStyleSheet(f"QFrame#startupSplash{{background:{background};border:2px solid {border};border-radius:14px}} QLabel{{border:none;background:transparent;color:{text}}} QLabel#splashMuted{{color:{muted}}} QProgressBar{{border:none;background:{track};border-radius:4px;min-height:8px;max-height:8px}} QProgressBar::chunk{{background:#27bed4;border-radius:4px}}")
        box=QVBoxLayout(self);box.setContentsMargins(28,24,28,24);box.setSpacing(9);top=QHBoxLayout();icon=_label("");icon.setPixmap(QIcon(str(_asset_path("elios_colorizer.svg"))).pixmap(46,46));top.addWidget(icon);heading=QVBoxLayout();title=_label("Elios Colorizer");title.setStyleSheet(f"color:{text};font-size:18pt;font-weight:750");heading.addWidget(title);subtitle=_label("Preparing your colorization workspace");subtitle.setObjectName("splashMuted");heading.addWidget(subtitle);top.addLayout(heading,1);box.addLayout(top);box.addStretch();self.status=_label("Starting local services…");self.status.setObjectName("splashMuted");box.addWidget(self.status);self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(2);self.progress.setTextVisible(False);box.addWidget(self.progress)
        self._ticks=0;self._progress_timer=QTimer(self);self._progress_timer.setInterval(110);self._progress_timer.timeout.connect(self._advance_progress);self._progress_timer.start()
        screen=QApplication.primaryScreen()
        if screen:
            area=screen.availableGeometry();self.move(area.center()-self.rect().center())
    def _advance_progress(self):
        self._ticks+=1;value=self.progress.value();increment=(2,4,1,3,2,5,1)[self._ticks%7]
        self.progress.setValue(min(93,value+increment))
        if value>=68:self.status.setText("Finalizing the workspace…")
        elif value>=34:self.status.setText("Checking local processing resources…")
        elif value>=14:self.status.setText("Loading the desktop interface…")
    def complete(self):
        self._progress_timer.stop();self.status.setText("Ready");self.progress.setValue(100)

def main():
    app=QApplication.instance() or QApplication(sys.argv);app.setApplicationName("Elios Colorizer");app.setWindowIcon(QIcon(str(_asset_path("elios_colorizer.svg"))));app.setFont(QFont("Segoe UI",10));app.setStyle("Fusion")
    saved_theme=QSettings("EliosColorizer","EliosColorizer").value("dark_mode",False);dark=(saved_theme is True or str(saved_theme).strip().lower() in ("1","true","yes","on"));app.setStyleSheet(_DARK_STYLE if dark else _LIGHT_STYLE)
    splash=StartupSplash(dark);started=time.monotonic();splash.show();splash.raise_();app.processEvents()
    startup={"window":None}
    def reveal_window():
        window=startup["window"]
        window.hide();window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,False);window.setWindowOpacity(1.0);window.setUpdatesEnabled(True);window.show();window.raise_();window.activateWindow();splash.close()
    def finish_startup():splash.complete();QTimer.singleShot(180,reveal_window)
    def build_workspace():
        # Construct the main interface only after the splash has its own painted
        # event-loop turn. Keep its native surface cloaked until every layout has
        # settled so Windows cannot briefly composite a partial main window.
        window=MainWindow(startup_hidden=True);startup["window"]=window;window.ensurePolished()
        if window.layout():window.layout().activate()
        if window.centralWidget() and window.centralWidget().layout():window.centralWidget().layout().activate()
        app.processEvents();remaining=max(0,3200-int((time.monotonic()-started)*1000));QTimer.singleShot(remaining,finish_startup)
    QTimer.singleShot(180,build_workspace);return app.exec()
if __name__=="__main__":raise SystemExit(main())
