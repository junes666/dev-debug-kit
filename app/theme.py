"""设计系统：暗/亮双主题的调色板与 QSS 样式表。

Qt 的 QSS 不支持变量，这里用调色板字典 + 字符串格式化统一生成样式，
保证所有模块视觉一致。模块只需给控件设置 objectName / 动态属性 variant 即可命中样式。
"""
from __future__ import annotations

from .resources import res

# ---- 调色板 ----------------------------------------------------------------
DARK = {
    "bg0": "#0e1320",   # 窗口最底层
    "bg1": "#161c2b",   # 卡片/面板
    "bg2": "#1e2637",   # 输入框/下沉区
    "bg3": "#273248",   # hover
    "border": "#2a3346",
    "border2": "#38445c",
    "fg": "#e7ecf7",
    "fg_dim": "#a4aec4",
    "fg_muted": "#6b768e",
    "accent": "#5b8cff",
    "accent_hi": "#79a2ff",
    "accent_lo": "#3f6fe6",
    "accent_soft": "#5b8cff26",
    "ok": "#37d39b",
    "warn": "#f7b23b",
    "err": "#ff6b6b",
    "info": "#5b8cff",
    "sel": "#5b8cff40",
}

LIGHT = {
    "bg0": "#eef1f7",
    "bg1": "#ffffff",
    "bg2": "#f4f6fb",
    "bg3": "#e7ecf6",
    "border": "#dbe1ec",
    "border2": "#c7d0e0",
    "fg": "#1a2233",
    "fg_dim": "#4d576f",
    "fg_muted": "#8a93a8",
    "accent": "#3b6fe0",
    "accent_hi": "#5585ea",
    "accent_lo": "#2f5fc8",
    "accent_soft": "#3b6fe018",
    "ok": "#12a866",
    "warn": "#c67c10",
    "err": "#d94141",
    "info": "#3b6fe0",
    "sel": "#3b6fe033",
}

THEMES = {"dark": DARK, "light": LIGHT}

MONO = "'JetBrains Mono','Cascadia Code','Consolas','Menlo','DejaVu Sans Mono',monospace"
UI = "'Segoe UI','PingFang SC','Microsoft YaHei','Noto Sans CJK SC','Helvetica Neue',sans-serif"


def build_qss(p: dict) -> str:
    plus_url = res("assets/tree_plus.png").as_posix()
    minus_url = res("assets/tree_minus.png").as_posix()
    return f"""
* {{
    font-family: {UI};
    font-size: 13px;
    color: {p['fg']};
    outline: none;
}}
QWidget {{ background: transparent; }}
QMainWindow, #Root {{ background: {p['bg0']}; }}

/* ---- 顶部导航栏 ---- */
#TopBar {{ background: {p['bg1']}; border-bottom: 1px solid {p['border']}; }}
#Brand {{ font-size: 16px; font-weight: 700; color: {p['fg']}; }}
#BrandBadge {{ color: {p['accent_hi']}; background: transparent;
    padding: 2px 4px; font-size: 10.5px; font-weight: 600; }}
QPushButton[nav="true"] {{
    text-align: center; padding: 8px 15px; border: none; border-radius: 9px;
    color: {p['fg_dim']}; background: transparent; font-size: 13.5px; font-weight: 500;
}}
QPushButton[nav="true"]:hover {{ background: {p['bg3']}; color: {p['fg']}; }}
QPushButton[nav="true"]:checked {{ background: {p['accent_soft']}; color: {p['accent_hi']}; font-weight: 600; }}

/* ---- 卡片 ---- */
#Card {{ background: {p['bg1']}; border: 1px solid {p['border']}; border-radius: 12px; }}
#CardTitle {{ font-size: 13px; font-weight: 600; color: {p['fg']}; }}
#CardHint  {{ color: {p['fg_muted']}; font-size: 11.5px; }}
#Divider {{ background: {p['border']}; max-height: 1px; min-height: 1px; border: none; }}
#VDivider {{ background: {p['border']}; max-width: 1px; min-width: 1px; border: none; }}

/* ---- 文本标签 ---- */
QLabel {{ color: {p['fg']}; background: transparent; }}
QLabel[role="label"] {{ color: {p['fg_dim']}; font-size: 12px; font-weight: 500; }}
QLabel[role="hint"]  {{ color: {p['fg_muted']}; font-size: 11.5px; }}
QLabel[role="mono"]  {{ font-family: {MONO}; color: {p['fg_dim']}; }}

/* ---- 输入控件 ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QAbstractSpinBox {{
    background: {p['bg2']}; border: 1px solid {p['border']}; border-radius: 8px;
    padding: 7px 10px; color: {p['fg']}; selection-background-color: {p['sel']};
}}
QPlainTextEdit, QTextEdit {{ font-family: {MONO}; font-size: 12.5px; line-height: 1.5; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {p['accent']};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{ color: {p['fg_muted']}; }}
QLineEdit[mono="true"] {{ font-family: {MONO}; }}

QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p['fg_muted']}; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {p['bg2']}; border: 1px solid {p['border2']};
    border-radius: 8px; selection-background-color: {p['accent_soft']}; selection-color: {p['accent_hi']};
    padding: 4px; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

/* ---- 按钮 ---- */
QPushButton {{
    background: {p['bg2']}; border: 1px solid {p['border2']}; border-radius: 8px;
    padding: 7px 14px; color: {p['fg']}; font-weight: 500;
}}
QPushButton:hover {{ background: {p['bg3']}; border-color: {p['border2']}; }}
QPushButton:pressed {{ background: {p['bg1']}; }}
QPushButton:disabled {{ color: {p['fg_muted']}; background: {p['bg1']}; }}

QPushButton[variant="primary"] {{ background: {p['accent']}; border: 1px solid {p['accent']}; color: #ffffff; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {p['accent_hi']}; border-color: {p['accent_hi']}; }}
QPushButton[variant="primary"]:pressed {{ background: {p['accent_lo']}; }}

QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid {p['border2']}; color: {p['fg_dim']}; }}
QPushButton[variant="ghost"]:hover {{ background: {p['bg2']}; color: {p['fg']}; }}

QPushButton[variant="danger"] {{ background: transparent; border: 1px solid {p['err']}; color: {p['err']}; }}
QPushButton[variant="danger"]:hover {{ background: {p['err']}; color: #ffffff; }}

QPushButton[variant="chip"] {{ background: {p['bg2']}; border: 1px solid {p['border']}; border-radius: 14px;
    padding: 4px 12px; color: {p['fg_dim']}; font-size: 12px; }}
QPushButton[variant="chip"]:hover {{ color: {p['fg']}; border-color: {p['border2']}; }}

/* ---- 选项卡（模块内的子标签） ---- */
QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 10px; top: -1px; background: {p['bg1']}; }}
QTabBar::tab {{ background: transparent; color: {p['fg_muted']}; padding: 7px 16px; margin-right: 2px;
    border: none; border-radius: 8px; font-weight: 500; }}
QTabBar::tab:hover {{ color: {p['fg']}; background: {p['bg2']}; }}
QTabBar::tab:selected {{ color: {p['accent_hi']}; background: {p['accent_soft']}; font-weight: 600; }}

/* ---- 树 / 表格 ---- */
QTreeWidget, QTreeView, QTableWidget, QTableView, QListWidget {{
    background: {p['bg2']}; border: 1px solid {p['border']}; border-radius: 8px;
    alternate-background-color: {p['bg1']}; outline: none;
}}
QTreeWidget::item, QTableWidget::item, QListWidget::item {{ padding: 3px 4px; border: none; }}
QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{
    background: {p['accent_soft']}; color: {p['fg']};
}}
QTreeWidget::item:hover, QListWidget::item:hover {{ background: {p['bg3']}; }}
QHeaderView::section {{ background: {p['bg1']}; color: {p['fg_dim']}; padding: 6px 8px;
    border: none; border-bottom: 1px solid {p['border']}; border-right: 1px solid {p['border']}; font-weight: 600; }}
QTreeWidget::branch {{ background: transparent; }}
QTreeView::branch:has-children:closed, QTreeWidget::branch:has-children:closed {{
    image: url("{plus_url}"); }}
QTreeView::branch:has-children:open, QTreeWidget::branch:has-children:open {{
    image: url("{minus_url}"); }}

/* ---- 复选/单选 ---- */
QCheckBox, QRadioButton {{ color: {p['fg_dim']}; spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {p['border2']};
    background: {p['bg2']}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['border2']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['fg_muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['border2']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {p['fg_muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- 分隔条 / Splitter ---- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}

/* ---- 工具提示 ---- */
QToolTip {{ background: {p['bg2']}; color: {p['fg']}; border: 1px solid {p['border2']};
    border-radius: 6px; padding: 5px 8px; }}

/* ---- Toast ---- */
#Toast {{ border-radius: 10px; padding: 10px 16px; color: #ffffff; font-weight: 600; }}

/* ---- 徽标/状态 ---- */
QLabel[badge="ok"]   {{ color: {p['ok']};   background: {p['ok']}22;   border-radius: 6px; padding: 2px 9px; font-weight: 600; }}
QLabel[badge="warn"] {{ color: {p['warn']}; background: {p['warn']}22; border-radius: 6px; padding: 2px 9px; font-weight: 600; }}
QLabel[badge="err"]  {{ color: {p['err']};  background: {p['err']}22;  border-radius: 6px; padding: 2px 9px; font-weight: 600; }}
QLabel[badge="muted"]{{ color: {p['fg_muted']}; background: {p['bg2']}; border-radius: 6px; padding: 2px 9px; }}
"""


def qss(mode: str = "dark") -> str:
    return build_qss(THEMES.get(mode, DARK))


def palette(mode: str = "dark") -> dict:
    return THEMES.get(mode, DARK)
