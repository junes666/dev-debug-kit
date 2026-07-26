#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开发调试 · Dev Debug Kit —— 离线一体化开发调试工具箱

集成：HTTP 调试 / JS 调试 / JSON 解析 / JSON 对比 / 编码加密 / 二维码。
纯 PySide6 桌面应用，装一次依赖后完全离线运行。
"""
from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QPushButton, QLabel, QButtonGroup, QScrollArea,
)

from app import theme, widgets
from app.resources import res

# (图标, 标题, 模块文件, 类名)
MODULES = [
    ("🌐", "HTTP 调试",  "app.modules.http_tool",     "HttpTool"),
    ("⚡", "JS 调试",    "app.modules.js_tool",       "JsTool"),
    ("🌲", "JSON 解析",  "app.modules.json_tool",     "JsonTool"),
    ("🔀", "JSON 对比",  "app.modules.jsondiff_tool", "JsonDiffTool"),
    ("🔐", "编码加密",   "app.modules.codec_tool",    "CodecTool"),
    ("▦", "二维码",     "app.modules.qrcode_tool",    "QrTool"),
    ("🌍", "翻译",      "app.modules.translate_tool", "TranslateTool"),
    ("🔤", "正则",      "app.modules.regex_tool",     "RegexTool"),
]


def _placeholder(title: str, err: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(40, 40, 40, 40)
    lb = QLabel(f"⚠ 模块「{title}」加载失败")
    lb.setStyleSheet("font-size:16px;font-weight:600;")
    tb = QLabel(err)
    tb.setProperty("role", "mono")
    tb.setWordWrap(True)
    tb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(lb)
    lay.addWidget(tb)
    lay.addStretch(1)
    return w


def _load_module(mod_path: str, cls_name: str, title: str) -> QWidget:
    try:
        import importlib
        mod = importlib.import_module(mod_path)
        cls = getattr(mod, cls_name)
        return cls()
    except Exception:
        return _placeholder(title, traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.mode = "dark"
        self.setWindowTitle("开发调试 · Dev Debug Kit")
        _icon = res("assets/icon.png")
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))
        self.resize(1280, 820)
        self.setMinimumSize(1040, 640)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        vl = QVBoxLayout(root)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # ---- 顶部导航栏 ----
        topbar = QWidget()
        topbar.setObjectName("TopBar")
        topbar.setFixedHeight(58)
        tl = QHBoxLayout(topbar)
        tl.setContentsMargins(18, 0, 16, 0)
        tl.setSpacing(6)

        brand = QLabel("开发调试")
        brand.setObjectName("Brand")
        bbadge = QLabel("离线")
        bbadge.setObjectName("BrandBadge")
        tl.addWidget(brand)
        tl.addWidget(bbadge)
        tl.addSpacing(22)

        self.stack = QStackedWidget()
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        for i, (icon, title, mod_path, cls_name) in enumerate(MODULES):
            btn = QPushButton(f"{icon}  {title}")
            btn.setProperty("nav", True)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self.stack.setCurrentIndex(idx))
            tl.addWidget(btn)
            self.nav_group.addButton(btn, i)
            page = _load_module(mod_path, cls_name, title)
            self.stack.addWidget(page)

        tl.addStretch(1)
        self.theme_btn = QPushButton("☀  浅色")
        self.theme_btn.setProperty("nav", True)
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(self.toggle_theme)
        tl.addWidget(self.theme_btn)

        vl.addWidget(topbar)
        vl.addWidget(self.stack, 1)

        self.nav_group.button(0).setChecked(True)
        self.stack.setCurrentIndex(0)

    def toggle_theme(self):
        self.mode = "light" if self.mode == "dark" else "dark"
        widgets.set_mode(self.mode)
        QApplication.instance().setStyleSheet(theme.qss(self.mode))
        self.theme_btn.setText("🌙  深色" if self.mode == "light" else "☀  浅色")
        # 通知模块刷新（如实现了 refresh_theme）
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            if hasattr(w, "refresh_theme"):
                try:
                    w.refresh_theme()
                except Exception:
                    pass


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("开发调试")
    _icon = res("assets/icon.png")
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))
    widgets.set_mode("dark")
    app.setStyleSheet(theme.qss("dark"))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
