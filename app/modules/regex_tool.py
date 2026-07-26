"""正则表达式测试 + 常用正则库（离线，基于 Python re）。"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QCheckBox, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from app import widgets

# 常用正则库：(名称, 正则)
COMMON = [
    ("手机号（中国大陆）", r"1[3-9]\d{9}"),
    ("邮箱", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ("URL 网址", r"https?://[^\s/$.?#][^\s]*"),
    ("IPv4 地址", r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"),
    ("IPv6 地址", r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"),
    ("IP:端口", r"\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}"),
    ("身份证（18 位）", r"[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]"),
    ("日期 YYYY-MM-DD", r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"),
    ("时间 HH:MM:SS", r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"),
    ("中文字符", r"[一-龥]+"),
    ("整数", r"-?\d+"),
    ("浮点数", r"-?\d+\.\d+"),
    ("QQ 号", r"[1-9]\d{4,10}"),
    ("邮政编码", r"[1-9]\d{5}"),
    ("十六进制颜色", r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b"),
    ("MAC 地址", r"(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}"),
    ("域名", r"[a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z0-9][-a-zA-Z0-9]*)+"),
    ("用户名（4-16 位字母数字下划线）", r"^\w{4,16}$"),
    ("强密码（8+ 含大小写数字）", r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$"),
    ("车牌号", r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-Z][A-HJ-NP-Z0-9]{4,5}[A-Z0-9挂学警港澳]"),
    ("HTML 标签", r"</?[a-zA-Z][^>]*>"),
    ("空白行", r"^\s*$"),
    ("银行卡号", r"\d{16,19}"),
    ("@提及", r"@\w+"),
]

SAMPLE = """联系电话 13800138000，客服 400-800-1234。
邮箱 support@example.com、admin@dev-debug.cn。
官网 https://github.com/dev-debug-kit 与 http://example.org/path?q=1。
服务器 IP 192.168.1.100:8080，网关 10.0.0.1。
日期 2026-07-27 时间 12:30:45。
颜色 #1E90FF、#fff。身份证 11010119900307734X。
中文测试：开发调试工具箱。MAC 00:1A:2B:3C:4D:5E。"""


class RegexTool(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---- 正则输入行 ----
        self.pat_edit = QLineEdit()
        self.pat_edit.setPlaceholderText(r"输入正则表达式，如  \d{4}-\d{2}-\d{2}")
        self.pat_edit.setFont(widgets.mono_font(12))
        self.pat_edit.textChanged.connect(self._schedule)

        self.cb_i = QCheckBox("忽略大小写")
        self.cb_m = QCheckBox("多行")
        self.cb_s = QCheckBox("点匹配换行")
        for cb in (self.cb_i, self.cb_m, self.cb_s):
            cb.stateChanged.connect(self._schedule)

        self.preset = QComboBox()
        self.preset.addItem("常用正则…")
        for name, pat in COMMON:
            self.preset.addItem(name, pat)
        self.preset.currentIndexChanged.connect(self._pick_preset)

        top = widgets.Card("正则表达式")
        top.add(widgets.row(widgets.label("正则：", "label"), (self.pat_edit, 1)))
        top.add(widgets.row(
            self.cb_i, self.cb_m, self.cb_s, None,
            widgets.label("模板：", "hint"), self.preset,
        ))
        root.addWidget(top)

        # ---- 替换行 ----
        self.repl_edit = QLineEdit()
        self.repl_edit.setPlaceholderText(r"替换为（支持 \1 \g<name> 反向引用）")
        self.repl_edit.setFont(widgets.mono_font(12))
        self.repl_out = QLineEdit()
        self.repl_out.setReadOnly(True)
        self.repl_out.setFont(widgets.mono_font(12))
        top.add(widgets.row(
            widgets.label("替换：", "label"), (self.repl_edit, 1),
            widgets.primary("替换", self._do_replace),
            widgets.chip("复制", lambda: widgets.copy_text(self, self.repl_out.text())),
        ))
        top.add(widgets.row(widgets.label("结果：", "label"), (self.repl_out, 1)))

        # ---- 文本 / 结果 ----
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        c_text = widgets.Card("测试文本")
        self.text_edit = widgets.CodeEditor(placeholder="在此粘贴要匹配的文本…", wrap=True)
        self.text_edit.set_text(SAMPLE)
        self.text_edit.textChanged.connect(self._schedule)
        c_text.add(widgets.expanding(self.text_edit))
        split.addWidget(c_text)

        c_res = widgets.Card("匹配结果")
        self.status = widgets.badge("匹配 0 处", "muted")
        c_res.add_header_widget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "匹配内容", "位置", "分组"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 48)
        self.table.setColumnWidth(2, 90)
        c_res.add(widgets.expanding(self.table))
        split.addWidget(c_res)
        split.setSizes([260, 300])
        root.addWidget(split, 1)

        # 防抖定时器
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._match)
        self._match()

    # ------------------------------------------------------------------ #
    def _flags(self) -> int:
        f = 0
        if self.cb_i.isChecked():
            f |= re.IGNORECASE
        if self.cb_m.isChecked():
            f |= re.MULTILINE
        if self.cb_s.isChecked():
            f |= re.DOTALL
        return f

    def _compile(self):
        pat = self.pat_edit.text()
        if not pat:
            return None
        return re.compile(pat, self._flags())

    def _pick_preset(self, idx: int):
        pat = self.preset.itemData(idx)
        if pat:
            self.pat_edit.setText(pat)

    def _schedule(self):
        self._timer.start()

    def _match(self):
        self.table.setRowCount(0)
        pat = self.pat_edit.text()
        if not pat:
            self._set_status("匹配 0 处", "muted")
            return
        try:
            rx = re.compile(pat, self._flags())
        except re.error as e:
            self._set_status(f"正则错误：{e}", "err")
            return
        text = self.text_edit.text()
        n = 0
        for m in rx.finditer(text):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(str(r + 1)))
            self.table.setItem(r, 1, QTableWidgetItem(m.group(0)))
            self.table.setItem(r, 2, QTableWidgetItem(f"{m.start()}-{m.end()}"))
            groups = m.groups()
            gtext = "  ".join(f"[{i+1}]{g}" for i, g in enumerate(groups) if g is not None) if groups else ""
            self.table.setItem(r, 3, QTableWidgetItem(gtext))
            n += 1
            if n >= 5000:
                break
        self._set_status(f"匹配 {n} 处", "ok" if n else "warn")

    def _do_replace(self):
        pat = self.pat_edit.text()
        if not pat:
            widgets.notify(self, "请先输入正则", "warn")
            return
        try:
            rx = re.compile(pat, self._flags())
            self.repl_out.setText(rx.sub(self.repl_edit.text(), self.text_edit.text()))
        except re.error as e:
            widgets.notify(self, f"正则错误：{e}", "error")

    def _set_status(self, text: str, kind: str):
        self.status.setText(text)
        self.status.setProperty("badge", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
