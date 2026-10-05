"""Desktop interface for single- and multi-flight colorization."""
from __future__ import annotations
import re, shutil, sys, threading, time, traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QAbstractSpinBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QMenu, QScrollArea,
    QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget)

_LIGHT_STYLE = """
QMainWindow,QWidget#page{background:#f3f5f7;color:#1e293b} QWidget{font-family:'Segoe UI';font-size:10pt}
QFrame#card{background:#fff;border:1px solid #dce3eb;border-radius:10px} QFrame#flightRow{background:#f8fafc;border:1px solid #e1e8ef;border-radius:7px}
QFrame#processingCard{background:#f7fbfd;border:2px solid #8ec6d8;border-radius:10px} QFrame#alignmentPanel{background:#eaf5f8;border:1px solid #83b8c9;border-radius:7px}
QLabel{color:#26354a;background:transparent} QLabel#title{font-size:23pt;font-weight:700;color:#142438} QLabel#sectionTitle{font-size:12pt;font-weight:700;color:#126d8b} QLabel#muted{color:#617184} QLabel#callout{color:#1d526a;background:#dff0f5;border-radius:5px;padding:7px}
QLineEdit,QComboBox,QDoubleSpinBox{background:#fff;border:1px solid #cbd5e1;border-radius:5px;padding:8px;color:#192c42} QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus{border-color:#317da6}
QLineEdit#flightTitleEdit{background:transparent;border:1px solid transparent;border-radius:3px;padding:3px 5px;color:#126d8b;font-size:12pt;font-weight:750} QLineEdit#flightTitleEdit:hover{background:#eef6f9;border-color:#b9d7e2} QLineEdit#flightTitleEdit:focus{background:#fff;border-color:#317da6}
QLineEdit#flightTitleEdit[duplicateName="true"]{background:#fff2ef;border-color:#b34330;color:#8e2e1f}
QCheckBox#distanceToggle{font-weight:700;background:#f1f7fa;border:1px solid #a8c5d3;border-radius:5px;padding:8px 11px;spacing:8px} QCheckBox#distanceToggle:checked{background:#dceff6;border-color:#317da6}
QPushButton{background:#fff;color:#25465f;border:1px solid #cbd5e1;border-radius:5px;padding:8px 13px;font-weight:600} QPushButton:hover{background:#edf5fa} QPushButton:disabled{color:#8a99a9;background:#eff2f5} QPushButton#accentButton{background:#e1f1f6;border-color:#5a9cb4;color:#145b76}
QPushButton#primary{background:#126d8b;color:#fff;border-color:#126d8b} QPushButton#primary:disabled{background:#b6c9d1}
QPushButton#removeFlightButton{background:#fff0ed;border-color:#d58a7d;color:#993d31} QPushButton#removeFlightButton:hover{background:#f9ddd7;border-color:#bd5c4c} QPushButton#addFlightButton{background:#e7f5eb;border-color:#72ad84;color:#256b3e} QPushButton#addFlightButton:hover{background:#d7eedf;border-color:#55966a}
QCheckBox#comingSoonToggle{color:#8794a2;background:#eef1f4;border:1px dashed #b7c0c9;border-radius:5px;padding:8px 11px} QLabel#comingSoonBadge{color:#657483;background:#dfe5ea;border-radius:8px;padding:3px 8px;font-size:8pt;font-weight:700}
QProgressBar{border:none;background:#e8eef3;border-radius:4px;min-height:8px} QProgressBar::chunk{background:#1784a3}
QLabel#percentBadge{background:#126d8b;color:#fff;border-radius:12px;padding:5px 10px;font-size:10pt;font-weight:700}
QPlainTextEdit{background:#f8fafc;color:#44586e;border:1px solid #dce3eb;border-radius:5px;font-family:Consolas;font-size:9pt} QScrollArea{background:transparent;border:none}
"""
_DARK_STYLE = """
QMainWindow,QWidget#page{background:#17212b;color:#e6edf3} QWidget{font-family:'Segoe UI';font-size:10pt}
QFrame#card{background:#202c38;border:1px solid #344454;border-radius:10px} QFrame#flightRow{background:#1b2732;border:1px solid #344454;border-radius:7px}
QFrame#processingCard{background:#1b2d38;border:2px solid #347f98;border-radius:10px} QFrame#alignmentPanel{background:#193643;border:1px solid #3b8198;border-radius:7px}
QLabel{color:#d9e2ec;background:transparent} QLabel#title{font-size:23pt;font-weight:700;color:#f4f8fb} QLabel#sectionTitle{font-size:12pt;font-weight:700;color:#64c5df} QLabel#muted{color:#aab9c7} QLabel#callout{color:#d8f1f7;background:#234652;border-radius:5px;padding:7px}
QLineEdit,QComboBox,QDoubleSpinBox{background:#16212b;border:1px solid #4a5d70;border-radius:5px;padding:8px;color:#edf4f8}
QLineEdit#flightTitleEdit{background:transparent;border:1px solid transparent;border-radius:3px;padding:3px 5px;color:#65c7df;font-size:12pt;font-weight:750} QLineEdit#flightTitleEdit:hover{background:#233541;border-color:#426779} QLineEdit#flightTitleEdit:focus{background:#16212b;border-color:#42b6d3}
QLineEdit#flightTitleEdit[duplicateName="true"]{background:#3b2526;border-color:#d66b57;color:#ffd7cf}
QCheckBox#distanceToggle{font-weight:700;background:#263745;border:1px solid #567084;border-radius:5px;padding:8px 11px;spacing:8px} QCheckBox#distanceToggle:checked{background:#214b5c;border-color:#42b6d3}
QPushButton{background:#263745;color:#e3edf4;border:1px solid #4a5d70;border-radius:5px;padding:8px 13px;font-weight:600} QPushButton:hover{background:#304758} QPushButton:disabled{color:#7b8b9a;background:#25313c} QPushButton#accentButton{background:#214b5c;border-color:#42a9c3;color:#e5f7fb}
QPushButton#primary{background:#1784a3;color:#fff;border-color:#1784a3} QPushButton#primary:disabled{background:#3b5b68}
QPushButton#removeFlightButton{background:#422b2d;border-color:#995b55;color:#ffc7be} QPushButton#removeFlightButton:hover{background:#523235;border-color:#c67568} QPushButton#addFlightButton{background:#213a2c;border-color:#4f8a66;color:#c8f0d5} QPushButton#addFlightButton:hover{background:#294936;border-color:#68a87e}
QCheckBox#comingSoonToggle{color:#81909e;background:#222f3a;border:1px dashed #4b5d6d;border-radius:5px;padding:8px 11px} QLabel#comingSoonBadge{color:#afbdc9;background:#34434f;border-radius:8px;padding:3px 8px;font-size:8pt;font-weight:700}
QProgressBar{border:none;background:#30404e;border-radius:4px;min-height:8px} QProgressBar::chunk{background:#42b6d3}
QLabel#percentBadge{background:#1784a3;color:#fff;border-radius:12px;padding:5px 10px;font-size:10pt;font-weight:700}
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

@dataclass(frozen=True)
class AdvancedProcessingSettings:
    sample_frequency_hz:float=1.0
    image_edge_exclusion_percent:float=2.0
    minimum_sharpness:float=2.0

class AdvancedProcessingDialog(QDialog):
    BLUR_LEVELS=(("Off",0.0),("Normal",2.0),("Strong",8.0))
    def __init__(self,settings:AdvancedProcessingSettings,parent=None):
        super().__init__(parent);self.setWindowTitle("Advanced Processing Settings");self.setModal(True);self.setMinimumWidth(570)
        root=QVBoxLayout(self);root.addWidget(_label("These settings apply to this processing session. Higher sampling can substantially increase processing time.",True))
        grid=QGridLayout();grid.setColumnStretch(1,1);root.addLayout(grid)
        self.sample_frequency=QDoubleSpinBox();self.sample_frequency.setObjectName("sampleFrequencySpin");self.sample_frequency.setRange(.25,30.0);self.sample_frequency.setDecimals(2);self.sample_frequency.setSingleStep(.25);self.sample_frequency.setSuffix(" frames/sec");self.sample_frequency.setValue(settings.sample_frequency_hz)
        self.edge_exclusion=QDoubleSpinBox();self.edge_exclusion.setObjectName("edgeExclusionSpin");self.edge_exclusion.setRange(0,15);self.edge_exclusion.setDecimals(1);self.edge_exclusion.setSingleStep(1);self.edge_exclusion.setSuffix(" % per edge");self.edge_exclusion.setValue(settings.image_edge_exclusion_percent)
        self.blur_rejection=NoWheelComboBox();self.blur_rejection.setObjectName("blurRejectionCombo")
        for label,value in self.BLUR_LEVELS:self.blur_rejection.addItem(label,value)
        self.blur_rejection.setCurrentIndex(next((i for i,(_,value) in enumerate(self.BLUR_LEVELS) if value==settings.minimum_sharpness),1))
        self._add_row(grid,0,"Frame sampling frequency",self._arrow_control(self.sample_frequency,"sampleFrequency"),"How often RGB frames are considered. Range: 0.25–30; default: 1 frame/sec.",lambda:self.sample_frequency.setValue(1.0))
        self._add_row(grid,2,"Image edge exclusion",self._arrow_control(self.edge_exclusion,"edgeExclusion"),"Ignore projected pixels inside this border on all four edges. Range: 0–15%; default: 2%.",lambda:self.edge_exclusion.setValue(2.0))
        self._add_row(grid,4,"Blur rejection",self.blur_rejection,"Strong rejects more soft frames; Off accepts every frame. Default: Normal.",lambda:self.blur_rejection.setCurrentIndex(1))
        controls=QHBoxLayout();reset_all=QPushButton("Reset all defaults");reset_all.setObjectName("resetAllAdvancedButton");reset_all.clicked.connect(self.reset_defaults);controls.addWidget(reset_all);controls.addStretch()
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);controls.addWidget(buttons);root.addLayout(controls)
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
        self.sample_frequency.setValue(1.0);self.edge_exclusion.setValue(2.0);self.blur_rejection.setCurrentIndex(1)
    def values(self):
        return AdvancedProcessingSettings(self.sample_frequency.value(),self.edge_exclusion.value(),float(self.blur_rejection.currentData()))

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
            flights=[x.to_dict() for x in self.selection.flights]
            if self.kind=="inspect":
                if len(flights)==1: result=self.backend.inspect_source(folder=flights[0]["folder"],las_override=flights[0]["las_override"],calibration_override=self.selection.calibration_override)
                else: result=self.backend.inspect_sources(flights,calibration_override=self.selection.calibration_override,mode=self.selection.mode,alignment_method=self.selection.alignment_method,merged_source=self.selection.merged_source,cloudcompare_executable=self.selection.cloudcompare_executable)
            elif len(flights)==1 and self.selection.mode=="separate":
                kw=dict(folder=flights[0]["folder"],las_override=flights[0]["las_override"],output=self.output,calibration_override=self.selection.calibration_override,progress=self.progress.emit,cancelled=self.cancellation.is_set)
                if self.selection.maximum_color_distance_m is not None: kw["maximum_color_distance_m"]=self.selection.maximum_color_distance_m
                if self.selection.illumination_balancing: kw["illumination_balancing"]=True
                kw.update(sample_interval_s=1/self.selection.advanced.sample_frequency_hz,
                          image_border_fraction=self.selection.advanced.image_edge_exclusion_percent/100,
                          minimum_sharpness=self.selection.advanced.minimum_sharpness)
                result=self.backend.run_colorization(**kw)
            else:
                result=self.backend.run_workflow(flights,self.output,calibration_override=self.selection.calibration_override,mode=self.selection.mode,
                    alignment_method=self.selection.alignment_method,merged_source=self.selection.merged_source,cloudcompare_executable=self.selection.cloudcompare_executable,
                    illumination_balancing=self.selection.illumination_balancing,maximum_color_distance_m=self.selection.maximum_color_distance_m,
                    sample_interval_s=1/self.selection.advanced.sample_frequency_hz,
                    image_border_fraction=self.selection.advanced.image_edge_exclusion_percent/100,
                    minimum_sharpness=self.selection.advanced.minimum_sharpness,
                    progress=self.progress.emit,cancelled=self.cancellation.is_set)
            self.result.emit(result)
        except Exception as exc:self.error.emit(str(exc) or type(exc).__name__,traceback.format_exc())
        finally:self.finished.emit()

class Checklist(QWidget):
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
    changed=Signal(); remove_requested=Signal(object)
    def __init__(self,number):
        super().__init__(objectName="flightRow"); grid=QGridLayout(self); grid.setContentsMargins(12,10,12,10); grid.setColumnStretch(1,1)
        self._number=number; self._uses_default_name=True; self._setting_default_name=False
        self.name_edit=QLineEdit(f"Flight {number}"); self.name_edit.setObjectName("flightTitleEdit"); self.name_edit.setMaxLength(48); self.name_edit.setToolTip("Click to rename this flight (48 characters maximum)."); self.name_edit.setMinimumWidth(120); self.name_edit.setMaximumWidth(420)
        self.heading=self.name_edit # Compatibility alias for callers that previously read the title label.
        self.remove_button=QPushButton("✕  Remove"); self.remove_button.setObjectName("removeFlightButton")
        grid.addWidget(self.name_edit,0,0,1,2); grid.addWidget(self.remove_button,0,2)
        self.folder_edit,self.folder_button=self._path("Select native Inspector flight folder…")
        self.las_edit,self.las_button=self._path("Select matching Inspector export folder…")
        self.transform_source=NoWheelComboBox(); self.transform_source.addItem("Upload matrix file","file"); self.transform_source.addItem("Enter matrix values","values")
        self.transform_edit,self.transform_button=self._path("Optional 4 x 4 matrix file (blank = identity)…")
        self.transform_label=_label("Alignment matrix")
        grid.addWidget(_label("Native flight folder"),1,0); grid.addWidget(self.folder_edit,1,1); grid.addWidget(self.folder_button,1,2)
        grid.addWidget(_label("Inspector export folder"),2,0); grid.addWidget(self.las_edit,2,1); grid.addWidget(self.las_button,2,2)
        transform_row=QWidget(); transform_box=QHBoxLayout(transform_row); transform_box.setContentsMargins(0,0,0,0); transform_box.setSpacing(7); transform_box.addWidget(self.transform_source); transform_box.addWidget(self.transform_edit,1)
        grid.addWidget(self.transform_label,3,0); grid.addWidget(transform_row,3,1); grid.addWidget(self.transform_button,3,2)
        self.matrix_text=QPlainTextEdit(); self.matrix_text.setPlaceholderText("Four rows with four values each"); self.matrix_text.setPlainText("1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1"); self.matrix_text.setMaximumHeight(88); self.matrix_text.setStyleSheet("font-family:Consolas;font-size:9pt"); grid.addWidget(self.matrix_text,4,1,1,2)
        self.folder_edit.textChanged.connect(self.changed); self.las_edit.textChanged.connect(self.changed); self.name_edit.textChanged.connect(self._name_changed); self.name_edit.editingFinished.connect(self._finish_name_edit); self.transform_edit.textChanged.connect(self.changed); self.matrix_text.textChanged.connect(self.changed)
        self.remove_button.clicked.connect(lambda:self.remove_requested.emit(self)); self.folder_button.clicked.connect(self._browse_folder); self.las_button.clicked.connect(self._browse_las); self.transform_button.clicked.connect(self._browse_transform)
        self.transform_source.currentIndexChanged.connect(self._transform_source_changed)
        self.set_transform_visible(False)
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
    def _path(placeholder):
        edit=QLineEdit(); edit.setPlaceholderText(placeholder); edit.setClearButtonEnabled(True); edit.setMinimumWidth(0); button=QPushButton("Browse…"); return edit,button
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
        for x in (self.name_edit,self.folder_edit,self.folder_button,self.las_edit,self.las_button,self.transform_source,self.transform_edit,self.transform_button,self.matrix_text,self.remove_button):x.setEnabled(value)

class MainWindow(QMainWindow):
    def __init__(self,backend=None,restore_settings=True):
        super().__init__()
        if backend is None:
            from . import service as backend
        self.backend=backend; self.settings=QSettings("EliosColorizer","EliosColorizer"); self.flight_rows=[]
        self._thread=self._worker=None; self._job_kind=""; self._job_selection=None; self._job_revision=self._revision=0
        self._inspection_pending=False; self._inspected_selection=None; self._ready=False; self._cancellation=threading.Event(); self._close_when_idle=False
        self._last_result=None; self._last_inspection_result=None; self._last_progress_message=""; self._run_started_at=None; self._progress_fraction=0.0; self._eta_seconds=None; self._eta_as_of=None; self._eta_samples=[]; self._eta_rate=None; self._dark_mode=False
        self.advanced_settings=AdvancedProcessingSettings()
        self._elapsed_timer=QTimer(self); self._elapsed_timer.setInterval(1000); self._elapsed_timer.timeout.connect(self._update_elapsed)
        self.setWindowTitle("Elios Colorizer"); self.resize(1080,920); self.setMinimumSize(820,680); self._build()
        self._debounce=QTimer(self); self._debounce.setSingleShot(True); self._debounce.setInterval(350); self._debounce.timeout.connect(self._start_pending_inspection)
        self.calibration_edit.textChanged.connect(self._calibration_changed); self.output_edit.textChanged.connect(self._update_actions)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed); self.alignment_combo.currentIndexChanged.connect(self._mode_changed); self.distance_check.toggled.connect(self._distance_changed)
        self.merged_edit.textChanged.connect(self._inputs_changed); self.cloudcompare_edit.textChanged.connect(self._inputs_changed)
        if restore_settings:self._restore_settings()
        self.theme_button.setChecked(self._dark_mode);self._apply_theme()
        self._sync_aliases(); self._show_empty(); self._mode_changed()
        if any(x.folder_edit.text().strip() for x in self.flight_rows):self._inputs_changed()
    def _build(self):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page=QWidget(objectName="page"); root=QVBoxLayout(page); root.setContentsMargins(28,24,28,23); root.setSpacing(16)
        top=QHBoxLayout(); title=_label("Elios Colorizer"); title.setObjectName("title"); top.addWidget(title); top.addStretch(); self.theme_button=QPushButton(); self.theme_button.setCheckable(True); self.theme_button.setChecked(self._dark_mode); self.theme_button.clicked.connect(self._toggle_theme); top.addWidget(self.theme_button); root.addLayout(top)
        root.addWidget(_label("Bring one or more flights' RGB imagery onto their recorded point clouds.",True))
        card,box=_card("1  Choose flights"); box.addWidget(_label("For each flight, select its native Inspector folder and matching export folder. The export must contain the LAS/LAZ and *-trajectory.csv. One camera profile is shared by all flights.",True)); self.flights_layout=QVBoxLayout(); box.addLayout(self.flights_layout)
        self.add_flight_button=QPushButton("＋  Add flight"); self.add_flight_button.setObjectName("addFlightButton"); self.add_flight_button.clicked.connect(self._add_flight); box.addWidget(self.add_flight_button,alignment=Qt.AlignmentFlag.AlignLeft)
        self.calibration_edit,self.calibration_button=self._file_row(box,"Camera profile","Choose shared RGB calibration…",self._browse_calibration); root.addWidget(card); self._add_flight(trigger=False)
        card,box=_card("2  Check all flight data"); self.checklist=Checklist(); box.addWidget(self.checklist); self.summary=_label("Choose flight folders.",True); box.addWidget(self.summary)
        row=QHBoxLayout(); self.refresh_button=QPushButton("↻  Refresh checks"); self.refresh_button.clicked.connect(self._inputs_changed); row.addWidget(self.refresh_button); row.addStretch(); self.tools_summary=_label("Local libraries are checked automatically.",True); row.addWidget(self.tools_summary); box.addLayout(row); self.dependencies=Checklist(); self.dependencies.hide(); box.addWidget(self.dependencies); root.addWidget(card)
        card,box=_card("3  Choose processing and output"); card.setObjectName("processingCard");section_title=box.itemAt(0).widget();box.removeWidget(section_title);section_header=QHBoxLayout();section_header.addWidget(section_title);section_header.addStretch();self.advanced_button=QPushButton("Advanced processing settings…");self.advanced_button.setObjectName("accentButton");self.advanced_button.clicked.connect(self._show_advanced_settings);section_header.addWidget(self.advanced_button);box.insertLayout(0,section_header);self._update_advanced_summary(); row=QHBoxLayout(); self.mode_label=_label("Result"); row.addWidget(self.mode_label); self.mode_combo=NoWheelComboBox(); self.mode_combo.addItem("Separate colorized LAS files","separate"); self.mode_combo.addItem("Align and merge into one cloud","merge"); row.addWidget(self.mode_combo,1); box.addLayout(row)
        self.mode_help=_label("",True); box.addWidget(self.mode_help)
        self.alignment_panel=QFrame(objectName="alignmentPanel"); align_box=QVBoxLayout(self.alignment_panel); align_box.setContentsMargins(12,10,12,10); align_box.setSpacing(8)
        row=QHBoxLayout(); row.addWidget(_label("Alignment")); self.alignment_combo=NoWheelComboBox(); self.alignment_combo.addItem("Use a CloudCompare-aligned merged cloud (Recommended)","manual"); self.alignment_combo.addItem("Align automatically with CloudCompare","automatic"); row.addWidget(self.alignment_combo,1); align_box.addLayout(row)
        self.alignment_help=_label(""); self.alignment_help.setObjectName("callout"); align_box.addWidget(self.alignment_help)
        self.alignment_guide_button=QPushButton("CloudCompare instructions…",objectName="accentButton"); self.alignment_guide_button.clicked.connect(self._show_alignment_guide); align_box.addWidget(self.alignment_guide_button,alignment=Qt.AlignmentFlag.AlignLeft)
        self.manual_panel=QWidget(); manual_box=QVBoxLayout(self.manual_panel); manual_box.setContentsMargins(0,0,0,0); self.merged_edit,self.merged_button=self._file_row(manual_box,"Merged cloud","Choose the aligned, merged LAS/LAZ…",self._browse_merged); align_box.addWidget(self.manual_panel)
        self.automatic_panel=QWidget(); automatic_box=QVBoxLayout(self.automatic_panel); automatic_box.setContentsMargins(0,0,0,0); self.cloudcompare_edit,self.cloudcompare_button=self._file_row(automatic_box,"CloudCompare","Detected automatically, or browse to CloudCompare.exe…",self._browse_cloudcompare); align_box.addWidget(self.automatic_panel)
        box.addWidget(self.alignment_panel)
        row=QHBoxLayout(); self.distance_check=QCheckBox("Limit colorization distance"); self.distance_check.setObjectName("distanceToggle"); self.distance_spin=QDoubleSpinBox(); self.distance_spin.setRange(.2,40); self.distance_spin.setValue(8); self.distance_spin.setSuffix(" m"); row.addWidget(self.distance_check); row.addWidget(self.distance_spin); row.addStretch(); box.addLayout(row)
        row=QHBoxLayout(); self.color_balance_check=QCheckBox("Illumination Balancing"); self.color_balance_check.setObjectName("distanceToggle"); self.color_balance_check.setToolTip("Use overlapping views to conservatively correct center-to-edge lighting. Leaves RGB unchanged when evidence is insufficient."); row.addWidget(self.color_balance_check); row.addStretch(); box.addLayout(row)
        self.output_edit,self.output_button=self._file_row(box,"Output","Choose output…",self._browse_output)
        row=QHBoxLayout(); self.state_label=_label("Select a flight to begin."); row.addWidget(self.state_label,1); self.cancel_button=QPushButton("Cancel"); self.cancel_button.clicked.connect(self._cancel); self.cancel_button.hide(); row.addWidget(self.cancel_button); self.run_button=QPushButton("Colorize point cloud",objectName="primary"); self.run_button.clicked.connect(self._start_colorization); row.addWidget(self.run_button); box.addLayout(row)
        progress_row=QHBoxLayout(); self.progress_bar=QProgressBar(); self.progress_bar.setRange(0,1000); self.progress_bar.setTextVisible(False); progress_row.addWidget(self.progress_bar,1); self.progress_badge=_label("0%"); self.progress_badge.setObjectName("percentBadge"); self.progress_badge.setAlignment(Qt.AlignmentFlag.AlignCenter); self.progress_badge.setMinimumWidth(58); progress_row.addWidget(self.progress_badge); box.addLayout(progress_row)
        timing_row=QHBoxLayout(); self.elapsed_label=_label("Elapsed: 00:00",True); timing_row.addWidget(self.elapsed_label); timing_row.addStretch(); self.eta_label=_label("Estimated time left: —",True); self.eta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); timing_row.addWidget(self.eta_label); box.addLayout(timing_row)
        self.progress_detail=_label("",True); box.addWidget(self.progress_detail)
        row=QHBoxLayout(); self.open_output_button=QPushButton("Open output folder"); self.open_report_button=QPushButton("Open processing report"); self.open_output_button.clicked.connect(self._open_output); self.open_report_button.clicked.connect(self._open_report); self.open_output_button.hide(); self.open_report_button.hide(); row.addWidget(self.open_output_button); row.addWidget(self.open_report_button); row.addStretch(); self.details_button=QPushButton("Processing details"); self.details_button.setCheckable(True); row.addWidget(self.details_button); box.addLayout(row)
        self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(1500); self.log.setMaximumHeight(150); self.log.hide(); self.details_button.toggled.connect(self.log.setVisible); box.addWidget(self.log); root.addWidget(card); root.addStretch(); scroll.setWidget(page); self.setCentralWidget(scroll)
    @staticmethod
    def _file_row(box,title,placeholder,callback):
        row=QHBoxLayout(); label=_label(title); label.setMinimumWidth(95); row.addWidget(label); edit=QLineEdit(); edit.setPlaceholderText(placeholder); edit.setClearButtonEnabled(True); edit.setMinimumWidth(0); row.addWidget(edit,1); button=QPushButton("Browse…"); button.clicked.connect(callback); row.addWidget(button); box.addLayout(row); return edit,button
    def _add_flight(self,checked=False,trigger=True):
        row=FlightRow(len(self.flight_rows)+1); row.changed.connect(self._inputs_changed); row.remove_requested.connect(self._remove_flight); self.flight_rows.append(row); self.flights_layout.addWidget(row); self._renumber(); self._sync_aliases()
        if trigger:self._inputs_changed();self._mode_changed()
        return row
    def _remove_flight(self,row):
        if len(self.flight_rows)==1:return
        self.flight_rows.remove(row); row.deleteLater(); self._renumber(); self._sync_aliases()
        if len(self.flight_rows)==1 and self.mode_combo.currentData()=="merge":self.mode_combo.setCurrentIndex(0)
        self._inputs_changed(); self._mode_changed()
    def _renumber(self):
        for i,row in enumerate(self.flight_rows,1):row.set_default_number(i);row.remove_button.setVisible(len(self.flight_rows)>1)
    def _sync_aliases(self):
        self.source_edit=self.flight_rows[0].folder_edit; self.las_edit=self.flight_rows[0].las_edit; self.source_button=self.flight_rows[0].folder_button; self.las_button=self.flight_rows[0].las_button
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
            illumination_balancing=self.color_balance_check.isChecked(),advanced=self.advanced_settings)
    def _inspection_selection(self):
        return replace(self._selection(),illumination_balancing=False,advanced=AdvancedProcessingSettings())
    def _show_advanced_settings(self):
        dialog=AdvancedProcessingDialog(self.advanced_settings,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.advanced_settings=dialog.values();self._update_advanced_summary()
    def _update_advanced_summary(self):
        if not hasattr(self,"advanced_button"):return
        blur=next((label for label,value in AdvancedProcessingDialog.BLUR_LEVELS if value==self.advanced_settings.minimum_sharpness),"Custom")
        self.advanced_button.setToolTip(f"Current: {self.advanced_settings.sample_frequency_hz:g} fps · {self.advanced_settings.image_edge_exclusion_percent:g}% edges · {blur} blur rejection")
    def _restore_settings(self):
        calibration=str(self.settings.value("calibration","") or "")
        saved_theme=self.settings.value("dark_mode",False)
        self._dark_mode=(saved_theme is True or str(saved_theme).strip().lower() in ("1","true","yes","on"))
        for key in ("flights","source","output","mode","distance_enabled","distance_m"):
            self.settings.remove(key)
        self.calibration_edit.setText(calibration)
    def _save_settings(self):
        calibration=self.calibration_edit.text().strip()
        if calibration:self.settings.setValue("calibration",calibration)
        else:self.settings.remove("calibration")
        self.settings.setValue("dark_mode",self._dark_mode)
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
        self._revision+=1;self._ready=False;self._inspected_selection=None;self._last_inspection_result=None;self._inspection_pending=True;self.run_button.setEnabled(False);self.summary.setText("Waiting for the current selection to be checked.");self._debounce.start()
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
        else:
            self._last_result=result;total=int(result.get("total_points",0));colored=int(result.get("colored_points",0));coverage=f" ({colored/total:.1%})" if total and colored<=total else "";self.state_label.setText("Colorization complete.");self.progress_bar.setValue(1000);self.progress_badge.setText("100%");self._progress_fraction=1.0;self._update_elapsed();self._elapsed_timer.stop();self.eta_label.setText("Estimated time left: Complete");self.progress_detail.setText(f"{colored:,} colored output points{coverage}. Saved to {result.get('output',self.output_edit.text())}");self.open_output_button.setVisible(bool(result.get("output")));self.open_report_button.setVisible(bool(result.get("report")))
    def _on_error(self,message,details):
        if self._job_kind=="inspect":self._ready=False;self.summary.setText(message);self.checklist.set_rows([{"label":"Flight inspection","status":"missing","detail":message}])
        elif self._cancellation.is_set():self.state_label.setText("Colorization cancelled.");self.progress_detail.setText("Processing stopped.")
        else:self.state_label.setText("Processing could not finish.");self.progress_detail.setText(message);self.log.appendPlainText(details)
        self._elapsed_timer.stop()
        if self._job_kind=="run":self._update_elapsed();self.eta_label.setText("Estimated time left: —")
    def _thread_finished(self):
        if self._thread:self._thread.wait()
        self._thread=self._worker=None;self._job_kind="";self._update_actions()
        if self._close_when_idle:self.close();return
        if self._inspection_pending:QTimer.singleShot(0,self._start_pending_inspection)
    def _on_progress(self,stage,fraction,message):
        fraction=max(0,min(1,fraction));self._progress_fraction=fraction
        color_stage=stage.rsplit(": ",1)[-1] in ("Checking visibility","Assigning RGB")
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
        self.progress_bar.setValue(round(fraction*1000));self.progress_badge.setText(f"{round(fraction*100):d}%");self.state_label.setText(stage);self.progress_detail.setText(message);entry=f"{stage}: {message}"
        if entry!=self._last_progress_message:self.log.appendPlainText(entry);self._last_progress_message=entry
        self._update_elapsed()
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
    def _recommended_free_space_bytes(self,selection):
        gib=1024**3
        if selection.mode=="merge":return (30 if selection.alignment_method=="automatic" else 25)*gib
        report=self._last_inspection_result or {};flights=report.get("flights") or [report]
        counts=[max(0,int(item.get("point_count") or 0)) for item in flights]
        # Separate results accumulate in the output folder while the largest flight also
        # needs its color arrays and atomic output copy. Keep a 5 GiB practical floor.
        estimate=sum(counts)*50+max(counts,default=0)*20+gib
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
        for x in (self.add_flight_button,self.calibration_edit,self.calibration_button,self.mode_combo,self.alignment_combo,self.alignment_guide_button,self.merged_edit,self.merged_button,self.cloudcompare_edit,self.cloudcompare_button,self.distance_check,self.color_balance_check,self.advanced_button,self.output_edit,self.output_button,self.refresh_button):x.setEnabled(not running)
        self.distance_spin.setEnabled(not running and self.distance_check.isChecked());self.cancel_button.setVisible(running);self.cancel_button.setEnabled(running and not self._cancellation.is_set());merge_valid=self.mode_combo.currentData()!="merge" or len(self.flight_rows)>1;can=idle and names_valid and self._ready and self._inspected_selection==self._inspection_selection() and self._output_valid() and merge_valid and not self._inspection_pending;self.run_button.setEnabled(can)
    def _start_colorization(self):
        if not self.run_button.isEnabled():return
        selection=self._selection();output=Path(self.output_edit.text().strip())
        if len(selection.flights)==1 and selection.flights[0].las_override and output.resolve()==Path(selection.flights[0].las_override).resolve():QMessageBox.warning(self,"Choose a different output","Output must differ from source.");return
        if not(selection.mode=="separate" and len(selection.flights)>1) and output.exists():QMessageBox.warning(self,"Choose a new output","That output already exists.");return
        if not self._disk_space_allows_start(selection,output):return
        self._save_settings();self._last_result=None;self.log.clear();self.progress_bar.setValue(0);self.progress_badge.setText("0%");self._progress_fraction=0.0;self._eta_seconds=None;self._eta_as_of=None;self._eta_samples=[];self._eta_rate=None;self.elapsed_label.setText("Elapsed: 00:00");self.eta_label.setText("Estimated time left: Waiting for colorization…");self._run_started_at=time.monotonic();self._elapsed_timer.start();self._launch_worker("run",selection,str(output))
    def _cancel(self):self._cancellation.set();self.state_label.setText("Stopping safely…");self._update_actions()
    def _open_output(self):
        if self._last_result and self._last_result.get("output"):
            path=Path(self._last_result["output"]).resolve();QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else path.parent)))
    def _open_report(self):
        if self._last_result and self._last_result.get("report"):QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._last_result["report"]).resolve())))
    def closeEvent(self,event:QCloseEvent):
        if self._thread is not None:self._close_when_idle=True;self._inspection_pending=False;self._cancellation.set();event.ignore();return
        event.accept()

def main():
    app=QApplication.instance() or QApplication(sys.argv);app.setApplicationName("Elios Colorizer");app.setWindowIcon(QIcon(str(_asset_path("elios_colorizer.svg"))));app.setFont(QFont("Segoe UI",10));app.setStyle("Fusion");window=MainWindow();window.show();return app.exec()
if __name__=="__main__":raise SystemExit(main())
