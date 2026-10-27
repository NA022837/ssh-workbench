"""Explicit app-wide dark palette; independent of the operating system's appearance."""
from PySide6.QtGui import QColor, QPalette

BACKGROUND = '#23282c'
PANEL = '#293036'
FIELD = '#202529'
TEXT = '#dedede'
MUTED = '#969da5'
ACCENT = '#56a8ef'
TERMINAL_BACKGROUND = '#1c1c1c'
TERMINAL_TEXT = '#d8d8d8'

STYLESHEET = '''
QWidget { background-color:#23282c; color:#dedede; font-size:12px; }
QMainWindow, QDialog, QMessageBox { background-color:#23282c; }
QLabel { background:transparent; color:#bfc3c8; }
QLabel#appTitle { color:#f0f0f0; font-size:16px; font-weight:700; padding:4px; }
QLabel#sessionTarget { color:#eeeeee; font-size:12px; font-weight:700; }
QLabel#environmentBadge { background:#294155; color:#a9d4f6; border-radius:4px; padding:2px 6px; font-size:10px; }
QLabel#environmentBadge[production="true"] { background:#4c3035; color:#ffc1b8; }
QPushButton[role="crumb"] { padding:1px 5px; border-radius:4px; font-size:11px; }
QToolButton#sessionTool:checked { background:#294a65; border-color:#587b99; }
QLabel#sessionStatus, QLabel#hintText { color:#969da5; }
QLabel#serverTime { color:#9acbeb; font-size:11px; }
QLabel#welcomeText { color:#969da5; font-size:17px; }
QPushButton { background:#2b2b2b; color:#dedede; padding:5px 10px; border:1px solid #3a414a; border-radius:7px; }
QPushButton:hover { background:#313b46; border-color:#647180; }
QPushButton:checked { background:#223e56; border-color:#56a8ef; color:#e6f3ff; }
QPushButton[role="tool"] { background:transparent; border:1px solid transparent; padding:2px; border-radius:6px; font-size:15px; }
QPushButton[role="tool"]:hover { background:#2b3947; border-color:#425b73; }
QToolButton#tabClose { background:transparent; border:0; border-radius:5px; color:#9ca9b8; font-size:17px; }
QToolButton#tabClose:hover { background:#3f4650; color:white; }
QPushButton:pressed { background:#232323; }
QPushButton:focus { border:1px solid #56a8ef; }
QPushButton:disabled { background:#222222; color:#7e858e; border-color:#363636; }
QPushButton[role="primary"] { background:#56a8ef; color:#ffffff; border-color:#56a8ef; font-weight:700; }
QPushButton[role="primary"]:hover { background:#70baff; border-color:#70baff; }
QPushButton[role="primary"]:disabled { background:#294155; color:#a0b5c8; border-color:#3c5368; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background:#202529; color:#dedede; border:1px solid #3b3b3b; border-radius:5px; padding:5px; selection-background-color:#24415d; selection-color:#ffffff; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color:#56a8ef; }
QLineEdit:disabled, QComboBox:disabled { background:#293036; color:#858d96; border-color:#363636; }
QComboBox QAbstractItemView { background:#293036; color:#dedede; selection-background-color:#24415d; selection-color:#ffffff; }
QListWidget, QTreeWidget, QTableWidget { background:#202529; alternate-background-color:#252b30; color:#dedede; border:1px solid #323232; border-radius:3px; gridline-color:#282828; selection-background-color:#24415d; selection-color:#ffffff; }
QListWidget::item, QTreeWidget::item { padding:6px 5px; border-radius:5px; margin:1px; }
QListWidget::item:selected, QTreeWidget::item:selected { background:#24415d; color:#ffffff; }
QListWidget::item:hover, QTreeWidget::item:hover { background:#282e34; }
QHeaderView, QTableCornerButton::section { background:#293036; color:#65aff0; }
QHeaderView::section { background:#293036; color:#65aff0; padding:6px; border:0; border-right:1px solid #303030; border-bottom:1px solid #3b3b3b; font-weight:600; }
QTableWidget::item { padding:4px; }
QLabel#panelTitle { color:#969da5; font-size:11px; font-weight:700; padding:2px 0; }
QWidget#filesPanel { background:#202529; }
QWidget#connectionsPanel { background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #293136,stop:1 #202c34); }
QWidget#fileActions { background:#293238; border:1px solid #3a454f; border-radius:7px; }
QFrame#metricCard { background:#28333d; border:1px solid #3b4c5c; border-radius:10px; }
QLabel#metricValue { color:#e7f3ff; font-size:22px; font-weight:700; }
QProgressBar { background:#1c252d; border:0; border-radius:2px; }
QProgressBar::chunk { background:#56a8ef; border-radius:2px; }
QToolButton { background:transparent; border:1px solid transparent; border-radius:6px; padding:3px; }
QToolButton:hover { background:#3a4d5f; border-color:#587592; }
QToolButton:pressed { background:#24415d; }
QToolButton:focus { border-color:#66b9ff; }
QToolButton:disabled { background:transparent; border-color:transparent; }
QTabWidget::pane { border:0; border-top:1px solid #303b47; background:#23282c; }
QTabBar::tab { background:#20252c; color:#9ca9b8; padding:4px 9px; border:1px solid transparent; margin:2px 3px 2px 0; border-radius:6px; min-width:60px; }
QTabBar::tab:selected { background:#263c51; color:#eff7ff; border:1px solid #486e94; }
QTabBar::tab:hover { background:#2b3540; color:#ffffff; }
QSplitter::handle { background:#303030; width:1px; }
QSplitter::handle:hover { background:#56a8ef; }
QStatusBar { background:#141414; color:#969da5; border-top:1px solid #333333; }
QStatusBar::item { border:0; }
QScrollBar:vertical { background:#181818; width:12px; margin:0; }
QScrollBar:horizontal { background:#181818; height:12px; margin:0; }
QScrollBar::handle:vertical { background:#494949; min-height:25px; border-radius:5px; }
QScrollBar::handle:horizontal { background:#494949; min-width:25px; border-radius:5px; }
QScrollBar::handle:hover { background:#656565; }
QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
QScrollBar::add-page, QScrollBar::sub-page { background:none; }
QMenu { background:#293036; color:#dedede; border:1px solid #494949; padding:5px; }
QMenu::item { padding:7px 20px; }
QMenu::item:selected { background:#24415d; color:#ffffff; }
QMenu::item:disabled { color:#7e858e; }
QToolTip { background:#292929; color:#eeeeee; border:1px solid #5e6c79; padding:5px; }
'''


def apply_theme(app):
    app.setStyle('Fusion')
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window:BACKGROUND, QPalette.ColorRole.WindowText:TEXT,
        QPalette.ColorRole.Base:FIELD, QPalette.ColorRole.AlternateBase:PANEL,
        QPalette.ColorRole.Text:TEXT, QPalette.ColorRole.Button:'#2b2b2b',
        QPalette.ColorRole.ButtonText:TEXT, QPalette.ColorRole.Highlight:'#24415d',
        QPalette.ColorRole.HighlightedText:'#ffffff', QPalette.ColorRole.ToolTipBase:'#292929',
        QPalette.ColorRole.ToolTipText:TEXT, QPalette.ColorRole.PlaceholderText:'#858d96',
        QPalette.ColorRole.Link:ACCENT, QPalette.ColorRole.LinkVisited:'#a6c8ff',
        QPalette.ColorRole.Light:'#555555', QPalette.ColorRole.Midlight:'#333333',
        QPalette.ColorRole.Mid:'#303030', QPalette.ColorRole.Dark:'#1c1c1c',
        QPalette.ColorRole.Shadow:'#101010', QPalette.ColorRole.BrightText:'#ffffff',
    }
    for role,color in colors.items():
        palette.setColor(role,QColor(color))
    for role in (QPalette.ColorRole.Text,QPalette.ColorRole.WindowText,QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled,role,QColor('#7e858e'))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
