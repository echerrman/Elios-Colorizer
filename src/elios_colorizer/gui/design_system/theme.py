"""Elios 3.x design tokens compiled into a Qt Widgets stylesheet."""


def modular_style(dark: bool, checkmark_path: str = "") -> str:
    if dark:
        c = {
            "shell": "#111922", "rail": "#141f29", "panel": "#1b2732",
            "field": "#111c25", "line": "#344653", "line_strong": "#4a6070",
            "text": "#edf4f7", "muted": "#9cabb5", "hover": "#263946",
            "active": "#183f4d", "accent": "#35b6d2", "accent_hover": "#55c8df",
            "accent_text": "#071f27", "soft": "#172c36", "danger": "#ef8272",
            "danger_soft": "#452b2d", "success": "#64d99a", "success_soft": "#18382b",
            "warning": "#f2c66d", "warning_soft": "#40351e", "focus": "#6fd9ed",
        }
    else:
        c = {
            "shell": "#e7ecef", "rail": "#edf1f3", "panel": "#f5f7f8",
            "field": "#eef2f4", "line": "#c7d1d7", "line_strong": "#8fa3af",
            "text": "#172c3d", "muted": "#596d7a", "hover": "#e2eaed",
            "active": "#d7e9ed", "accent": "#087895", "accent_hover": "#096985",
            "accent_text": "#ffffff", "soft": "#e9eff1", "danger": "#a94437",
            "danger_soft": "#f6e5e1", "success": "#217a4b", "success_soft": "#dfeee5",
            "warning": "#7d5b09", "warning_soft": "#f2ead1", "focus": "#168dac",
        }
    checkmark = checkmark_path.replace("\\", "/")
    return f"""
    QWidget#modernShell {{ background:{c['shell']}; color:{c['text']}; font-family:'Segoe UI Variable','Segoe UI'; font-size:10pt; }}
    QWidget#workflowPage {{ background:{c['shell']}; color:{c['text']}; }}
    QWidget#modernShell QToolTip {{ background:{c['panel']}; color:{c['text']}; border:1px solid {c['line_strong']}; padding:6px; }}
    QFrame#modernHeader {{ background:{c['panel']}; border:none; border-bottom:1px solid {c['line']}; }}
    QFrame#toolRail {{ background:{c['rail']}; border:none; border-right:1px solid {c['line']}; }}
    QFrame#summaryRail {{ background:{c['panel']}; border:none; border-left:1px solid {c['line']}; }}
    QLabel#productTitle {{ color:{c['text']}; font-size:15pt; font-weight:700; }}
    QLabel#eyebrow {{ color:{c['accent']}; font-size:8pt; font-weight:700; }}
    QLabel#railHeading {{ color:{c['muted']}; font-size:8pt; font-weight:700; }}
    QLabel#pageHeading {{ color:{c['text']}; font-size:19pt; font-weight:700; }}
    QLabel#pageDescription {{ color:{c['muted']}; }}
    QLabel#summaryValue {{ color:{c['text']}; font-weight:600; }}
    QLabel#reviewBlock {{ background:{c['soft']}; color:{c['text']}; border:1px solid {c['line']}; border-radius:9px; padding:12px; }}
    QLabel#reviewBlock[missingOutput='true'] {{ background:{c['danger_soft']}; color:{c['danger']}; border-color:{c['danger']}; }}
    QLabel#emptyTitle {{ color:{c['text']}; font-size:14pt; font-weight:700; }}
    QLabel#emptyIcon {{ color:{c['accent']}; font-size:26pt; font-weight:700; }}
    QLabel#statusBadge {{ border-radius:10px; padding:4px 9px; font-size:8pt; font-weight:700; }}
    QLabel#statusBadge[tone='neutral'] {{ color:{c['muted']}; background:{c['soft']}; border:1px solid {c['line']}; }}
    QLabel#statusBadge[tone='info'] {{ color:{c['accent']}; background:{c['active']}; border:1px solid {c['accent']}; }}
    QLabel#statusBadge[tone='success'] {{ color:{c['success']}; background:{c['success_soft']}; border:1px solid {c['success']}; }}
    QLabel#statusBadge[tone='warning'] {{ color:{c['warning']}; background:{c['warning_soft']}; border:1px solid {c['warning']}; }}
    QLabel#statusBadge[tone='danger'] {{ color:{c['danger']}; background:{c['danger_soft']}; border:1px solid {c['danger']}; }}
    QFrame#summaryBlock {{ background:{c['soft']}; border:1px solid {c['line']}; border-radius:9px; }}
    QFrame#workflowHeader {{ background:{c['panel']}; border:none; border-bottom:1px solid {c['line']}; }}
    QFrame#reviewCard, QFrame#emptyState, QFrame#recentRunCard, QFrame#runPanel {{ background:{c['panel']}; border:1px solid {c['line']}; border-radius:12px; }}
    QFrame#validationBanner {{ background:{c['soft']}; border:1px solid {c['line']}; border-radius:10px; }}
    QFrame#validationBanner[state='ready'] {{ background:{c['success_soft']}; border-color:{c['success']}; }}
    QFrame#validationBanner[state='error'] {{ background:{c['danger_soft']}; border-color:{c['danger']}; }}
    QLabel#validationStateIcon {{ color:{c['accent']}; font-size:22pt; font-weight:700; min-width:42px; }}
    QFrame#validationLoadingOverlay {{ background:rgba(17,25,34,220); border:none; }}
    QLabel#validationLoadingText {{ color:#edf4f7; background:transparent; font-size:12pt; font-weight:650; }}
    QDialog#advancedProcessingPanel {{ background:transparent; border:none; }}

    QWidget#modernShell QPushButton {{ background:{c['panel']}; color:{c['text']}; border:1px solid {c['line_strong']}; border-radius:7px; padding:8px 13px; font-weight:600; min-height:18px; }}
    QWidget#modernShell QPushButton:hover {{ background:{c['hover']}; border-color:{c['accent']}; }}
    QWidget#modernShell QPushButton:pressed {{ background:{c['active']}; }}
    QWidget#modernShell QPushButton:disabled {{ color:{c['muted']}; background:{c['soft']}; border-color:{c['line']}; }}
    QWidget#modernShell QPushButton#primary {{ color:{c['accent_text']}; background:{c['accent']}; border-color:{c['accent']}; padding:9px 17px; }}
    QWidget#modernShell QPushButton#primary:hover {{ background:{c['accent_hover']}; border-color:{c['accent_hover']}; }}
    QWidget#modernShell QPushButton#primary:disabled {{ color:{c['muted']}; background:{c['soft']}; border-color:{c['line']}; }}
    QWidget#modernShell QPushButton#accentButton {{ background:{c['soft']}; color:{c['accent']}; border-color:{c['accent']}; }}
    QWidget#modernShell QPushButton#removeFlightButton {{ color:{c['danger']}; background:{c['danger_soft']}; border-color:{c['danger']}; }}
    QWidget#modernShell QPushButton#addFlightButton {{ color:{c['success']}; background:{c['success_soft']}; border-color:{c['success']}; }}
    QWidget#modernShell QPushButton#flightMoveButton {{ padding:0; min-width:30px; max-width:32px; min-height:30px; max-height:32px; font-size:13pt; font-weight:700; }}
    QWidget#modernShell QPushButton#toolNav, QWidget#modernShell QPushButton#workflowNav {{ text-align:left; border-color:transparent; background:transparent; padding:10px 12px; border-radius:8px; }}
    QWidget#modernShell QPushButton#toolNav:hover, QWidget#modernShell QPushButton#workflowNav:hover {{ background:{c['hover']}; }}
    QWidget#modernShell QPushButton#toolNav:checked, QWidget#modernShell QPushButton#workflowNav:checked {{ background:{c['active']}; border-color:{c['accent']}; font-weight:700; }}
    QWidget#modernShell QPushButton#workflowNav {{ text-align:center; padding:8px 10px; min-height:20px; }}
    QWidget#modernShell QPushButton#plannedTool {{ text-align:left; color:{c['muted']}; padding:11px 12px; border:1px dashed {c['line_strong']}; background:transparent; border-radius:8px; }}

    QWidget#modernShell QLineEdit, QWidget#modernShell QComboBox, QWidget#modernShell QDoubleSpinBox,
    QWidget#modernShell QSpinBox, QWidget#modernShell QTimeEdit {{ background:{c['field']}; color:{c['text']}; border:1px solid {c['line_strong']}; border-radius:7px; padding:8px 10px; selection-background-color:{c['accent']}; }}
    QWidget#modernShell QLineEdit:hover, QWidget#modernShell QComboBox:hover, QWidget#modernShell QDoubleSpinBox:hover, QWidget#modernShell QSpinBox:hover {{ border-color:{c['accent']}; }}
    QWidget#modernShell QLineEdit:focus, QWidget#modernShell QComboBox:focus, QWidget#modernShell QDoubleSpinBox:focus,
    QWidget#modernShell QSpinBox:focus, QWidget#modernShell QPlainTextEdit:focus {{ border:2px solid {c['focus']}; }}
    QWidget#modernShell QPlainTextEdit {{ background:{c['field']}; color:{c['text']}; border:1px solid {c['line']}; border-radius:8px; padding:8px; }}
    QWidget#modernShell QComboBox::drop-down {{ border:none; width:26px; }}

    QWidget#modernShell QFrame#card, QWidget#modernShell QFrame#flightsCard,
    QWidget#modernShell QFrame#cameraProfileCard, QWidget#modernShell QFrame#validationCard, QWidget#modernShell QFrame#processingCard {{ background:{c['panel']}; border:1px solid {c['line']}; border-radius:12px; }}
    QWidget#modernShell QFrame#flightRow {{ background:{c['soft']}; border:1px solid {c['line']}; border-radius:9px; }}
    QWidget#modernShell QLabel#sectionTitle {{ color:{c['text']}; background:transparent; border:none; border-left:3px solid {c['accent']}; border-radius:0; padding:2px 0 2px 10px; font-size:14pt; font-weight:700; }}
    QWidget#modernShell QLabel#callout {{ color:{c['text']}; background:{c['soft']}; border:1px solid {c['line']}; border-radius:8px; padding:9px; }}
    QWidget#modernShell QLabel#settingsChip {{ color:{c['accent']}; background:{c['active']}; border:1px solid {c['line']}; border-radius:10px; padding:4px 8px; }}
    QWidget#modernShell QCheckBox {{ color:{c['text']}; spacing:8px; }}
    QWidget#modernShell QCheckBox::indicator {{ width:17px; height:17px; border:1px solid {c['line_strong']}; border-radius:5px; background:{c['field']}; }}
    QWidget#modernShell QCheckBox::indicator:hover {{ border-color:{c['accent']}; }}
    QWidget#modernShell QCheckBox::indicator:checked {{ background:{c['accent']}; border-color:{c['accent']}; }}
    QWidget#modernShell QCheckBox::indicator:checked {{ image:url("{checkmark}"); }}
    QWidget#modernShell QCheckBox#distanceToggle {{ color:{c['text']}; background:{c['soft']}; border:1px solid {c['line']}; border-radius:8px; padding:8px 11px; }}
    QWidget#modernShell QCheckBox#distanceToggle:checked {{ background:{c['active']}; border-color:{c['accent']}; }}
    QWidget#modernShell QProgressBar {{ background:{c['soft']}; border:none; border-radius:4px; min-height:8px; max-height:8px; }}
    QWidget#modernShell QProgressBar::chunk {{ background:{c['accent']}; border-radius:4px; }}
    QWidget#modernShell QFrame#stickyBar, QWidget#modernShell QFrame#runPanel {{ background:{c['panel']}; border:1px solid {c['line']}; border-radius:12px; }}
    QWidget#modernShell QScrollArea {{ background:transparent; border:none; }}
    QWidget#modernShell QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
    QWidget#modernShell QScrollBar::handle:vertical {{ background:{c['line_strong']}; min-height:32px; border-radius:4px; }}
    QWidget#modernShell QScrollBar::add-line:vertical, QWidget#modernShell QScrollBar::sub-line:vertical {{ height:0; }}

    QDialog {{ background:{c['shell']}; color:{c['text']}; }}
    QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox {{ color:{c['text']}; }}
    QDialog QPushButton {{ background:{c['panel']}; color:{c['text']}; border:1px solid {c['line_strong']}; border-radius:7px; padding:8px 13px; font-weight:600; }}
    QDialog QPushButton:hover {{ background:{c['hover']}; border-color:{c['accent']}; }}
    QDialog QPushButton:disabled {{ color:{c['muted']}; background:{c['soft']}; border-color:{c['line']}; }}
    QDialog QLineEdit, QDialog QComboBox, QDialog QDoubleSpinBox, QDialog QSpinBox, QDialog QTimeEdit {{ background:{c['field']}; color:{c['text']}; border:1px solid {c['line_strong']}; border-radius:7px; padding:7px 9px; selection-background-color:{c['accent']}; }}
    QDialog QPlainTextEdit, QDialog QTableWidget, QDialog QTreeWidget {{ background:{c['field']}; color:{c['text']}; border:1px solid {c['line']}; border-radius:8px; selection-background-color:{c['active']}; }}
    QDialog QGroupBox {{ background:{c['panel']}; border:1px solid {c['line']}; border-radius:9px; margin-top:11px; padding:12px 8px 8px 8px; font-weight:600; }}
    QDialog QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 5px; background:{c['shell']}; }}
    QDialog QTabWidget::pane {{ background:{c['panel']}; border:1px solid {c['line']}; border-radius:8px; }}
    QDialog QTabBar::tab {{ color:{c['muted']}; background:{c['soft']}; border:1px solid {c['line']}; padding:8px 13px; }}
    QDialog QTabBar::tab:selected {{ color:{c['text']}; background:{c['active']}; border-color:{c['accent']}; }}
    QDialog QSlider::groove:horizontal {{ height:5px; background:{c['line']}; border-radius:2px; }}
    QDialog QSlider::sub-page:horizontal {{ background:{c['accent']}; border-radius:2px; }}
    QDialog QSlider::handle:horizontal {{ width:16px; margin:-6px 0; background:{c['panel']}; border:2px solid {c['accent']}; border-radius:8px; }}
    QDialog QSlider::handle:horizontal:hover {{ background:{c['accent']}; }}
    QDialog QLabel#exportMatchStatus[matchState='complete'] {{ color:{c['success']}; background:{c['success_soft']}; border:1px solid {c['success']}; border-radius:7px; padding:7px; }}
    QDialog QLabel#exportMatchStatus[matchState='partial'] {{ color:{c['warning']}; background:{c['warning_soft']}; border:1px solid {c['warning']}; border-radius:7px; padding:7px; }}
    QDialog QFrame#exportFlightRow {{ background:{c['panel']}; border:1px solid {c['line']}; border-radius:7px; }}
    QDialog QLabel#exportFlightName {{ color:{c['text']}; font-weight:700; }}
    QDialog QLabel#exportFlightStatus {{ border-radius:11px; font-size:12pt; font-weight:800; }}
    QDialog QLabel#exportFlightStatus[matchState='complete'] {{ color:{c['success']}; background:{c['success_soft']}; border:1px solid {c['success']}; }}
    QDialog QLabel#exportFlightStatus[matchState='missing'] {{ color:{c['danger']}; background:{c['danger_soft']}; border:1px solid {c['danger']}; }}
    QDialog QLabel#exportMatchedPath {{ color:{c['success']}; font-weight:600; }}
    QDialog QHeaderView::section {{ background:{c['soft']}; color:{c['text']}; border:none; border-right:1px solid {c['line']}; border-bottom:1px solid {c['line']}; padding:7px; font-weight:600; }}
    QMenu {{ background:{c['panel']}; color:{c['text']}; border:1px solid {c['line']}; padding:5px; }}
    QMenu::item {{ padding:7px 22px 7px 10px; border-radius:5px; }}
    QMenu::item:selected {{ background:{c['active']}; color:{c['text']}; }}
    """
