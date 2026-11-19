"""Compact Hardwaretest theme matching GPU Taskmanager (Norbert Jander)."""

from hardwaretest.core.paths import resource_path

STYLE = """
QWidget { color:#e9f0f1; }
QMainWindow, QTabWidget::pane, QScrollArea { background:#1d2021; }
QTabWidget::pane { border:1px solid #364244; }
QTabBar::tab { background:#25292a; border:1px solid #364244; padding:5px 8px; }
QTabBar::tab:selected { background:#203f49; color:#20d9ef; border-bottom:2px solid #20d9ef; }
QPushButton, QToolButton { background:#2c3233; border:1px solid #405052; border-radius:4px; padding:3px 7px; }
QPushButton:hover, QToolButton:hover { background:#354244; border-color:#20d9ef; }
QPushButton:pressed, QPushButton:checked { background:#194b51; border-color:#20d9ef; }
QPushButton:disabled, QToolButton:disabled { color:#718086; border-color:#364244; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit { background:#2c3233; border:1px solid #405052; border-radius:3px; padding:2px; }
QTextEdit, QPlainTextEdit, QTextBrowser, QTreeView, QTableView, QListView { background:#25292a; alternate-background-color:#2c3233; border:1px solid #364244; selection-background-color:#245963; }
QHeaderView::section { background:#2c3233; color:#95a6ad; padding:4px; border:1px solid #364244; }
QGroupBox { border:1px solid #364244; border-radius:5px; margin-top:9px; padding-top:9px; }
QGroupBox::title { color:#95a6ad; subcontrol-origin:margin; left:8px; }
QProgressBar { background:#25292a; border:1px solid #364244; border-radius:3px; text-align:center; }
QProgressBar::chunk { background:#20d9ef; }
QMenuBar, QMenu { background:#25292a; }
QMenuBar::item:selected, QMenu::item:selected { background:#245963; }
QScrollBar:vertical { background:#202526; width:14px; }
QScrollBar:horizontal { background:#202526; height:14px; }
QScrollBar::handle { background:#43565a; border-radius:3px; min-width:20px; min-height:20px; }
QToolTip { background:#303a3b; color:#e9f0f1; border:1px solid #537174; padding:5px; }
"""

# Full-height adjacent controls are easier to hit in an iLO remote console
# than two tiny arrows stacked into half of a compact input field.
for _widget in ("QSpinBox", "QDoubleSpinBox", "QTimeEdit"):
    STYLE += f"""
{_widget} {{ min-height:28px; padding-right:64px; }}
{_widget}::up-button, {_widget}::down-button {{
    subcontrol-origin:border; subcontrol-position:top right;
    width:28px; height:28px; top:2px;
    background:#354244; border:1px solid #60787d; border-radius:3px;
}}
{_widget}::up-button {{ right:2px; }}
{_widget}::down-button {{ right:34px; }}
{_widget}::up-button:hover, {_widget}::down-button:hover {{
    background:#245963; border-color:#20d9ef;
}}
{_widget}::up-button:pressed, {_widget}::down-button:pressed {{ background:#194b51; }}
{_widget}::up-button:disabled, {_widget}::down-button:disabled {{
    background:#25292a; border-color:#364244;
}}
{_widget}::up-arrow {{ image:url("{resource_path('assets', 'spin-up.svg').as_posix()}"); width:14px; height:14px; }}
{_widget}::down-arrow {{ image:url("{resource_path('assets', 'spin-down.svg').as_posix()}"); width:14px; height:14px; }}
"""
