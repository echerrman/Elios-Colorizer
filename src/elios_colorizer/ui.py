"""Desktop interface for single- and multi-flight colorization."""
from __future__ import annotations
import re, sys, threading, time, traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QMenu, QScrollArea,
    QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget)

_LIGHT_STYLE = """
QMainWindow,QWidget#page{background:#f3f5f7;color:#1e293b} QWidget{font-family:'Segoe UI';font-size:10pt}
QFrame#card{background:#fff;border:1px solid #dce3eb;border-radius:10px} QFrame#flightRow{background:#f8fafc;border:1px solid #e1e8ef;border-radius:7px}
QLabel{color:#26354a;background:transparent} QLabel#title{font-size:23pt;font-weight:700;color:#142438} QLabel#sectionTitle{font-size:12pt;font-weight:650;color:#152d45} QLabel#muted{color:#617184}
QLineEdit,QComboBox,QDoubleSpinBox{background:#fff;border:1px solid #cbd5e1;border-radius:5px;padding:8px;color:#192c42} QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus{border-color:#317da6}
QPushButton{background:#fff;color:#25465f;border:1px solid #cbd5e1;border-radius:5px;padding:8px 13px;font-weight:600} QPushButton:hover{background:#edf5fa} QPushButton:disabled{color:#8a99a9;background:#eff2f5}
QPushButton#primary{background:#126d8b;color:#fff;border-color:#126d8b} QPushButton#primary:disabled{background:#b6c9d1}
QProgressBar{border:none;background:#e8eef3;border-radius:4px;min-height:8px} QProgressBar::chunk{background:#1784a3}
QPlainTextEdit{background:#f8fafc;color:#44586e;border:1px solid #dce3eb;border-radius:5px;font-family:Consolas;font-size:9pt} QScrollArea{background:transparent;border:none}
"""
_DARK_STYLE = """
QMainWindow,QWidget#page{background:#17212b;color:#e6edf3} QWidget{font-family:'Segoe UI';font-size:10pt}
QFrame#card{background:#202c38;border:1px solid #344454;border-radius:10px} QFrame#flightRow{background:#1b2732;border:1px solid #344454;border-radius:7px}
QLabel{color:#d9e2ec;background:transparent} QLabel#title{font-size:23pt;font-weight:700;color:#f4f8fb} QLabel#sectionTitle{font-size:12pt;font-weight:650;color:#dcecf5} QLabel#muted{color:#aab9c7}
QLineEdit,QComboBox,QDoubleSpinBox{background:#16212b;border:1px solid #4a5d70;border-radius:5px;padding:8px;color:#edf4f8}
QPushButton{background:#263745;color:#e3edf4;border:1px solid #4a5d70;border-radius:5px;padding:8px 13px;font-weight:600} QPushButton:hover{background:#304758} QPushButton:disabled{color:#7b8b9a;background:#25313c}
QPushButton#primary{background:#1784a3;color:#fff;border-color:#1784a3} QPushButton#primary:disabled{background:#3b5b68}
QProgressBar{border:none;background:#30404e;border-radius:4px;min-height:8px} QProgressBar::chunk{background:#42b6d3}
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

@dataclass(frozen=True)
class FlightSelection:
    folder:str; las_override:str|None=None
    def to_dict(self): return {"folder":self.folder,"las_override":self.las_override}
@dataclass(frozen=True)
class WorkflowSelection:
    flights:tuple[FlightSelection,...]; calibration_override:str|None=None
    mode:str="separate"; maximum_color_distance_m:float|None=None
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
                if len(flights)==1: result=self.backend.inspect_source(**flights[0],calibration_override=self.selection.calibration_override)
                else: result=self.backend.inspect_sources(flights,calibration_override=self.selection.calibration_override)
            elif len(flights)==1 and self.selection.mode=="separate":
                kw=dict(**flights[0],output=self.output,calibration_override=self.selection.calibration_override,progress=self.progress.emit,cancelled=self.cancellation.is_set)
                if self.selection.maximum_color_distance_m is not None: kw["maximum_color_distance_m"]=self.selection.maximum_color_distance_m
                result=self.backend.run_colorization(**kw)
            else:
                result=self.backend.run_workflow(flights,self.output,calibration_override=self.selection.calibration_override,mode=self.selection.mode,
                    maximum_color_distance_m=self.selection.maximum_color_distance_m,progress=self.progress.emit,cancelled=self.cancellation.is_set)
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
            grid.addWidget(state,0,0,2,1); grid.addWidget(name,0,1); grid.addWidget(detail,1,1); self.box.addWidget(panel)
        self.box.addStretch(1)

class FlightRow(QFrame):
    changed=Signal(); remove_requested=Signal(object)
    def __init__(self,number):
        super().__init__(objectName="flightRow"); grid=QGridLayout(self); grid.setContentsMargins(12,10,12,10); grid.setColumnStretch(1,1)
        self.heading=_label(f"Flight {number}"); self.heading.setStyleSheet("font-weight:700"); self.remove_button=QPushButton("Remove")
        grid.addWidget(self.heading,0,0); grid.addWidget(self.remove_button,0,2)
        self.folder_edit,self.folder_button=self._path("Select native Inspector flight folder…")
        self.las_edit,self.las_button=self._path("Choose matching LAS / LAZ…")
        grid.addWidget(_label("Flight folder"),1,0); grid.addWidget(self.folder_edit,1,1); grid.addWidget(self.folder_button,1,2)
        grid.addWidget(_label("Point cloud"),2,0); grid.addWidget(self.las_edit,2,1); grid.addWidget(self.las_button,2,2)
        self.folder_edit.textChanged.connect(self.changed); self.las_edit.textChanged.connect(self.changed)
        self.remove_button.clicked.connect(lambda:self.remove_requested.emit(self)); self.folder_button.clicked.connect(self._browse_folder); self.las_button.clicked.connect(self._browse_las)
    @staticmethod
    def _path(placeholder):
        edit=QLineEdit(); edit.setPlaceholderText(placeholder); edit.setClearButtonEnabled(True); edit.setMinimumWidth(0); button=QPushButton("Browse…"); return edit,button
    def _browse_folder(self):
        path=QFileDialog.getExistingDirectory(self,"Choose an Inspector flight folder",self.folder_edit.text())
        if path:self.folder_edit.setText(path)
    def _browse_las(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose source point cloud",self.las_edit.text() or self.folder_edit.text(),"LAS point clouds (*.las *.laz)")
        if path:self.las_edit.setText(path)
    def selection(self):return FlightSelection(self.folder_edit.text().strip(),self.las_edit.text().strip() or None)
    def set_enabled(self,value):
        for x in (self.folder_edit,self.folder_button,self.las_edit,self.las_button,self.remove_button):x.setEnabled(value)

class MainWindow(QMainWindow):
    def __init__(self,backend=None,restore_settings=True):
        super().__init__()
        if backend is None:
            from . import service as backend
        self.backend=backend; self.settings=QSettings("EliosColorizer","EliosColorizer"); self.flight_rows=[]
        self._thread=self._worker=None; self._job_kind=""; self._job_selection=None; self._job_revision=self._revision=0
        self._inspection_pending=False; self._inspected_selection=None; self._ready=False; self._cancellation=threading.Event(); self._close_when_idle=False
        self._last_result=None; self._last_progress_message=""; self._run_started_at=None; self._dark_mode=False
        self._elapsed_timer=QTimer(self); self._elapsed_timer.setInterval(500); self._elapsed_timer.timeout.connect(self._update_elapsed)
        self.setWindowTitle("Elios Colorizer"); self.resize(1080,920); self.setMinimumSize(820,680); self._build(); self._apply_theme()
        self._debounce=QTimer(self); self._debounce.setSingleShot(True); self._debounce.setInterval(350); self._debounce.timeout.connect(self._start_pending_inspection)
        self.calibration_edit.textChanged.connect(self._calibration_changed); self.output_edit.textChanged.connect(self._update_actions)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed); self.distance_check.toggled.connect(self._distance_changed)
        if restore_settings:self._restore_settings()
        self._sync_aliases(); self._show_empty(); self._mode_changed()
        if any(x.folder_edit.text().strip() for x in self.flight_rows):self._inputs_changed()
    def _build(self):
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page=QWidget(objectName="page"); root=QVBoxLayout(page); root.setContentsMargins(28,24,28,23); root.setSpacing(16)
        top=QHBoxLayout(); title=_label("Elios Colorizer"); title.setObjectName("title"); top.addWidget(title); top.addStretch(); self.theme_button=QPushButton(); self.theme_button.setCheckable(True); self.theme_button.setChecked(self._dark_mode); self.theme_button.clicked.connect(self._toggle_theme); top.addWidget(self.theme_button); root.addLayout(top)
        root.addWidget(_label("Bring one or more flights' RGB imagery onto their recorded point clouds.",True))
        card,box=_card("1  Choose flights"); box.addWidget(_label("Add one row per flight. One camera profile is shared by all flights.",True)); self.flights_layout=QVBoxLayout(); box.addLayout(self.flights_layout)
        self.add_flight_button=QPushButton("+ Add flight"); self.add_flight_button.clicked.connect(self._add_flight); box.addWidget(self.add_flight_button,alignment=Qt.AlignmentFlag.AlignLeft)
        self.calibration_edit,self.calibration_button=self._file_row(box,"Camera profile","Choose shared RGB calibration…",self._browse_calibration); root.addWidget(card); self._add_flight(trigger=False)
        card,box=_card("2  Check all flight data"); self.checklist=Checklist(); box.addWidget(self.checklist); self.summary=_label("Choose flight folders.",True); box.addWidget(self.summary)
        row=QHBoxLayout(); self.refresh_button=QPushButton("Refresh checks"); self.refresh_button.clicked.connect(self._inputs_changed); row.addWidget(self.refresh_button); row.addStretch(); self.tools_summary=_label("Local libraries are checked automatically.",True); row.addWidget(self.tools_summary); box.addLayout(row); self.dependencies=Checklist(); self.dependencies.hide(); box.addWidget(self.dependencies); root.addWidget(card)
        card,box=_card("3  Choose processing and output"); row=QHBoxLayout(); row.addWidget(_label("Result")); self.mode_combo=QComboBox(); self.mode_combo.addItem("Separate colorized LAS files","separate"); self.mode_combo.addItem("Align and merge into one cloud","merge"); row.addWidget(self.mode_combo,1); box.addLayout(row)
        self.mode_help=_label("",True); box.addWidget(self.mode_help); row=QHBoxLayout(); self.distance_check=QCheckBox("Limit colorization distance"); self.distance_spin=QDoubleSpinBox(); self.distance_spin.setRange(.2,40); self.distance_spin.setValue(8); self.distance_spin.setSuffix(" m"); row.addWidget(self.distance_check); row.addWidget(self.distance_spin); row.addStretch(); box.addLayout(row)
        box.addWidget(_label("When enabled, distant background points stay available for a closer flight to color in merged mode.",True)); self.output_edit,self.output_button=self._file_row(box,"Output","Choose output…",self._browse_output)
        row=QHBoxLayout(); self.state_label=_label("Select a flight to begin."); row.addWidget(self.state_label,1); self.cancel_button=QPushButton("Cancel"); self.cancel_button.clicked.connect(self._cancel); self.cancel_button.hide(); row.addWidget(self.cancel_button); self.run_button=QPushButton("Colorize point cloud",objectName="primary"); self.run_button.clicked.connect(self._start_colorization); row.addWidget(self.run_button); box.addLayout(row)
        self.progress_bar=QProgressBar(); self.progress_bar.setRange(0,1000); box.addWidget(self.progress_bar); self.elapsed_label=_label("Elapsed: 00:00",True); self.progress_detail=_label("All work runs locally on this computer.",True); box.addWidget(self.elapsed_label); box.addWidget(self.progress_detail)
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
        for i,row in enumerate(self.flight_rows,1):row.heading.setText(f"Flight {i}");row.remove_button.setVisible(len(self.flight_rows)>1)
    def _sync_aliases(self):
        self.source_edit=self.flight_rows[0].folder_edit; self.las_edit=self.flight_rows[0].las_edit; self.source_button=self.flight_rows[0].folder_button; self.las_button=self.flight_rows[0].las_button
    def _selection(self):return WorkflowSelection(tuple(x.selection() for x in self.flight_rows),self.calibration_edit.text().strip() or None,str(self.mode_combo.currentData()),self.distance_spin.value() if self.distance_check.isChecked() else None)
    def _inspection_selection(self):
        x=self._selection(); return WorkflowSelection(x.flights,x.calibration_override)
    def _restore_settings(self):
        calibration=str(self.settings.value("calibration","") or "")
        for key in ("flights","source","output","mode","distance_enabled","distance_m","dark_mode"):
            self.settings.remove(key)
        self.calibration_edit.setText(calibration)
    def _save_settings(self):
        calibration=self.calibration_edit.text().strip()
        if calibration:self.settings.setValue("calibration",calibration)
        else:self.settings.remove("calibration")
    def _calibration_changed(self,*_):
        self._save_settings();self._inputs_changed()
    def _apply_theme(self):
        self.setStyleSheet(_DARK_STYLE if self._dark_mode else _LIGHT_STYLE)
        if hasattr(self,"theme_button"):self.theme_button.setIcon(QIcon(str(_asset_path("theme-moon.svg" if self._dark_mode else "theme-sun.svg"))));self.theme_button.setText("Light mode" if self._dark_mode else "Dark mode")
    def _toggle_theme(self):self._dark_mode=self.theme_button.isChecked();self._apply_theme()
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
    def _mode_changed(self,*_):
        merge=self.mode_combo.currentData()=="merge"; multiple=len(self.flight_rows)>1
        if merge:self.mode_help.setText("Works best when Inspector point clouds are already well aligned in the same coordinate system. A correction is used only when held-out overlap improves; otherwise original coordinates are retained.");self.run_button.setText("Colorize and merge flights")
        elif multiple:self.mode_help.setText("Colorize each flight independently and save one LAS per flight; alignment is skipped.");self.run_button.setText("Colorize all flights separately")
        else:self.mode_help.setText("The original single-flight colorization workflow.");self.run_button.setText("Colorize point cloud")
        self._update_actions()
    def _distance_changed(self,*_):self.distance_spin.setEnabled(self.distance_check.isChecked() and self._job_kind!="run");self._update_actions()
    def _inputs_changed(self,*_):
        self._revision+=1;self._ready=False;self._inspected_selection=None;self._inspection_pending=True;self.run_button.setEnabled(False);self.summary.setText("Waiting for the current selection to be checked.");self._debounce.start()
    def _start_pending_inspection(self):
        if self._thread is not None:self._inspection_pending=True;return
        self._debounce.stop();self._inspection_pending=False;selection=self._inspection_selection()
        if any(not x.folder for x in selection.flights):self._show_empty();self.summary.setText("Choose a folder for every flight.");self._update_actions();return
        self.state_label.setText("Checking all flight data…");self._launch_worker("inspect",selection)
    def _launch_worker(self,kind,selection,output=""):
        self._job_kind=kind;self._job_selection=selection;self._job_revision=self._revision;self._cancellation=threading.Event();self._thread=QThread(self);self._worker=ServiceWorker(self.backend,kind,selection,self._cancellation,output);self._worker.moveToThread(self._thread);self._thread.started.connect(self._worker.run);self._worker.result.connect(self._on_result);self._worker.error.connect(self._on_error);self._worker.progress.connect(self._on_progress);self._worker.finished.connect(self._thread.quit);self._worker.finished.connect(self._worker.deleteLater);self._thread.finished.connect(self._thread_finished);self._thread.finished.connect(self._thread.deleteLater);self._update_actions();self._thread.start()
    def _on_result(self,result):
        if self._job_kind=="inspect":
            if self._job_revision!=self._revision or self._job_selection!=self._inspection_selection():return
            self._ready=bool(result.get("ready"));self._inspected_selection=self._job_selection;self.checklist.set_rows(result.get("checklist",[]));missing=[x for x in result.get("dependencies",[]) if x.get("status")=="missing"];self.dependencies.setVisible(bool(missing));self.dependencies.set_rows(missing);self.tools_summary.setText("All required local tools are ready." if not missing else "Some local tools need attention.");self.summary.setText(str(result.get("summary","")));self.state_label.setText("Ready to colorize." if self._ready else "Resolve required items above.")
        else:
            self._last_result=result;total=int(result.get("total_points",0));colored=int(result.get("colored_points",0));coverage=f" ({colored/total:.1%})" if total and colored<=total else "";self.state_label.setText("Colorization complete.");self.progress_bar.setValue(1000);self._elapsed_timer.stop();self.progress_detail.setText(f"{colored:,} colored output points{coverage}. Saved to {result.get('output',self.output_edit.text())}");self.open_output_button.setVisible(bool(result.get("output")));self.open_report_button.setVisible(bool(result.get("report")))
    def _on_error(self,message,details):
        if self._job_kind=="inspect":self._ready=False;self.summary.setText(message);self.checklist.set_rows([{"label":"Flight inspection","status":"missing","detail":message}])
        elif self._cancellation.is_set():self.state_label.setText("Colorization cancelled.");self.progress_detail.setText("Processing stopped.")
        else:self.state_label.setText("Processing could not finish.");self.progress_detail.setText(message);self.log.appendPlainText(details)
        self._elapsed_timer.stop()
    def _thread_finished(self):
        if self._thread:self._thread.wait()
        self._thread=self._worker=None;self._job_kind="";self._update_actions()
        if self._close_when_idle:self.close();return
        if self._inspection_pending:QTimer.singleShot(0,self._start_pending_inspection)
    def _on_progress(self,stage,fraction,message):
        self.progress_bar.setValue(round(max(0,min(1,fraction))*1000));self.state_label.setText(stage);self.progress_detail.setText(message);entry=f"{stage}: {message}"
        if entry!=self._last_progress_message:self.log.appendPlainText(entry);self._last_progress_message=entry
    def _update_elapsed(self):
        if self._run_started_at is not None:
            elapsed=int(time.monotonic()-self._run_started_at);minutes,seconds=divmod(elapsed,60);self.elapsed_label.setText(f"Elapsed: {minutes:02d}:{seconds:02d}")
    def _output_valid(self):
        value=self.output_edit.text().strip()
        if not value:return False
        if self.mode_combo.currentData()=="separate" and len(self.flight_rows)>1:
            path=Path(value);return (path.exists() and path.is_dir()) or not path.suffix
        return Path(value).suffix.lower()==".las"
    def _update_actions(self):
        if not hasattr(self,"run_button"):return
        running=self._job_kind=="run";idle=self._thread is None
        for row in self.flight_rows:row.set_enabled(not running)
        for x in (self.add_flight_button,self.calibration_edit,self.calibration_button,self.mode_combo,self.distance_check,self.output_edit,self.output_button,self.refresh_button):x.setEnabled(not running)
        self.distance_spin.setEnabled(not running and self.distance_check.isChecked());self.cancel_button.setVisible(running);self.cancel_button.setEnabled(running and not self._cancellation.is_set());merge_valid=self.mode_combo.currentData()!="merge" or len(self.flight_rows)>1;can=idle and self._ready and self._inspected_selection==self._inspection_selection() and self._output_valid() and merge_valid and not self._inspection_pending;self.run_button.setEnabled(can)
    def _start_colorization(self):
        if not self.run_button.isEnabled():return
        selection=self._selection();output=Path(self.output_edit.text().strip())
        if len(selection.flights)==1 and selection.flights[0].las_override and output.resolve()==Path(selection.flights[0].las_override).resolve():QMessageBox.warning(self,"Choose a different output","Output must differ from source.");return
        if not(selection.mode=="separate" and len(selection.flights)>1) and output.exists():QMessageBox.warning(self,"Choose a new output","That output already exists.");return
        self._save_settings();self._last_result=None;self.log.clear();self.progress_bar.setValue(0);self._run_started_at=time.monotonic();self._elapsed_timer.start();self._launch_worker("run",selection,str(output))
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
