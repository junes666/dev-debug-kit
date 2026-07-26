"""HTTP 调试模块。

用 Python 标准库 urllib.request 发请求（桌面应用，无浏览器跨域限制）。
所有网络请求放到 QThread 后台线程执行，通过信号回主线程刷新 UI，避免界面卡死。
支持：请求方法 / 请求头 / 请求体(text/JSON/form/multipart 含文件上传) / 查询参数 /
响应格式化预览 / 图片响应显示 / 历史记录回填 / 复制为 cURL / 导入 cURL。
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QSpinBox, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QStackedWidget, QLabel,
    QListWidget, QListWidgetItem, QScrollArea, QTextEdit, QDialog, QAbstractItemView,
    QFileDialog, QSizePolicy,
)

from app import widgets

METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]

COMMON_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer ",
    "User-Agent": "DevDebug/1.0",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cookie": "",
    "Referer": "",
    "Origin": "",
    "X-Requested-With": "XMLHttpRequest",
}

BODY_TYPES = ["none", "text", "JSON", "form-urlencoded", "multipart-form"]


# --------------------------------------------------------------------------- #
#  后台请求线程
# --------------------------------------------------------------------------- #
class _HttpWorker(QThread):
    done = Signal(dict)

    def __init__(self, req: dict, parent=None):
        super().__init__(parent)
        self._req = req

    def run(self):
        req = self._req
        out = {
            "ok": False, "status": 0, "reason": "", "headers": [],
            "body": b"", "content_type": "", "elapsed_ms": 0, "size": 0,
            "error": "", "final_url": req["url"],
        }
        try:
            r = urllib.request.Request(req["url"], data=req.get("data"), method=req["method"])
            for k, v in req["headers"]:
                if k:
                    r.add_header(k, v)
            ctx = ssl.create_default_context()
            if req.get("insecure"):
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            t0 = time.perf_counter()
            try:
                resp = urllib.request.urlopen(r, timeout=req["timeout"], context=ctx)
                raw = resp.read()
                out["status"] = getattr(resp, "status", resp.getcode())
                out["reason"] = getattr(resp, "reason", "")
                out["headers"] = list(resp.getheaders())
                out["content_type"] = resp.headers.get("Content-Type", "") or ""
                out["final_url"] = resp.geturl()
                out["body"] = raw
                out["ok"] = True
            except urllib.error.HTTPError as e:
                raw = b""
                try:
                    raw = e.read()
                except Exception:
                    pass
                out["status"] = e.code
                out["reason"] = e.reason if isinstance(e.reason, str) else str(e.reason)
                try:
                    out["headers"] = list(e.headers.items())
                    out["content_type"] = e.headers.get("Content-Type", "") or ""
                except Exception:
                    pass
                out["body"] = raw
                out["ok"] = True  # 有响应，只是非 2xx
            finally:
                out["elapsed_ms"] = (time.perf_counter() - t0) * 1000
            out["size"] = len(out["body"])
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, socket.timeout):
                out["error"] = f"请求超时（>{req['timeout']}s）"
            else:
                out["error"] = f"连接失败：{reason}"
        except socket.timeout:
            out["error"] = f"请求超时（>{req['timeout']}s）"
        except ValueError as e:
            out["error"] = f"地址不合法：{e}"
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {e}"
        self.done.emit(out)


# --------------------------------------------------------------------------- #
#  主模块
# --------------------------------------------------------------------------- #
class HttpTool(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _HttpWorker | None = None
        self._running = False
        self._resp: dict | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_request_bar())

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_history_panel())
        split.addWidget(self._build_request_panel())
        split.addWidget(self._build_response_panel())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 4)
        split.setSizes([210, 430, 470])
        root.addWidget(split, 1)

    # ------------------------------------------------------------------ #
    #  顶部请求栏
    # ------------------------------------------------------------------ #
    def _build_request_bar(self) -> QWidget:
        card = widgets.Card()
        self.method_combo = QComboBox()
        self.method_combo.addItems(METHODS)
        self.method_combo.setFixedWidth(110)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://httpbin.org/get")
        self.url_edit.setFont(widgets.mono_font(12))
        self.url_edit.setProperty("mono", True)
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.returnPressed.connect(self._send)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 120)
        self.timeout_spin.setValue(10)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setFixedWidth(84)
        self.timeout_spin.setToolTip("请求超时时间（秒）")

        self.send_btn = widgets.primary("发送", self._send)
        self.send_btn.setFixedWidth(96)

        card.add(widgets.row(
            self.method_combo, (self.url_edit, 1), self.timeout_spin, self.send_btn,
        ))
        return card

    # ------------------------------------------------------------------ #
    #  历史记录面板
    # ------------------------------------------------------------------ #
    def _build_history_panel(self) -> QWidget:
        card = widgets.Card("历史记录", "点击可回填重发")
        card.add_header_widget(widgets.chip("清空", self._clear_history, "清空历史记录"))
        self.history_list = QListWidget()
        self.history_list.setFont(widgets.mono_font(11))
        self.history_list.itemClicked.connect(self._restore_history)
        card.add(widgets.expanding(self.history_list))
        return card

    def _clear_history(self):
        self.history_list.clear()
        widgets.notify(self, "已清空历史记录", "info")

    def _push_history(self, method: str, url: str):
        snap = self._snapshot()
        it = QListWidgetItem(f"{method}  {url}")
        it.setData(Qt.UserRole, snap)
        it.setToolTip(f"{method} {url}")
        self.history_list.insertItem(0, it)
        while self.history_list.count() > 50:
            self.history_list.takeItem(self.history_list.count() - 1)

    def _restore_history(self, item: QListWidgetItem):
        snap = item.data(Qt.UserRole)
        if not isinstance(snap, dict):
            return
        try:
            self._load_snapshot(snap)
            widgets.notify(self, "已回填到请求表单", "success")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"回填失败：{e}", "error")

    # ------------------------------------------------------------------ #
    #  请求配置面板
    # ------------------------------------------------------------------ #
    def _build_request_panel(self) -> QWidget:
        card = widgets.Card("请求配置")
        card.add_header_widget(widgets.chip("原始报文", self._raw_dialog, "查看/导出原始 HTTP 请求报文，或粘贴报文解析回填"))
        card.add_header_widget(widgets.chip("复制为 cURL", self._copy_curl))
        card.add_header_widget(widgets.chip("导入 cURL", self._import_curl))

        tabs = QTabWidget()
        tabs.addTab(self._build_headers_tab(), "请求头")
        tabs.addTab(self._build_body_tab(), "请求体")
        tabs.addTab(self._build_params_tab(), "查询参数")
        card.add(widgets.expanding(tabs))
        return card

    # ---- 请求头 tab ---- #
    def _build_headers_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.headers_edit = widgets.CodeEditor(
            placeholder="每行一个请求头，格式  键: 值  例如：\n"
                        "Content-Type: application/json\n"
                        "Authorization: Bearer xxxxxx\n"
                        "（以 # 开头的行会被忽略）",
            wrap=True,
        )

        self.header_pick = QComboBox()
        self.header_pick.addItems(list(COMMON_HEADERS.keys()))
        self.header_pick.setFixedWidth(180)

        bar = widgets.row(
            widgets.label("常用头：", "hint"), self.header_pick,
            widgets.chip("插入", self._add_common_header),
            None,
            widgets.chip("清空", lambda: self.headers_edit.set_text("")),
        )
        lay.addWidget(bar)
        lay.addWidget(widgets.expanding(self.headers_edit), 1)
        return w

    def _add_common_header(self):
        name = self.header_pick.currentText()
        line = f"{name}: {COMMON_HEADERS.get(name, '')}"
        cur = self.headers_edit.text().rstrip("\n")
        self.headers_edit.set_text((cur + "\n" + line) if cur else line)

    # ---- 请求体 tab ---- #
    def _build_body_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.body_type = QComboBox()
        self.body_type.addItems(BODY_TYPES)
        self.body_type.currentIndexChanged.connect(self._on_body_type)
        lay.addWidget(widgets.row(widgets.label("类型：", "label"), self.body_type, None))

        self.body_stack = QStackedWidget()
        # 0 none
        none_lb = widgets.label("该请求不携带请求体。", "hint")
        none_lb.setAlignment(Qt.AlignCenter)
        self.body_stack.addWidget(none_lb)
        # 1 text / 2 JSON 共用编辑器（分别一个，便于内容独立）
        self.body_text = widgets.CodeEditor(placeholder="在此输入纯文本请求体…", wrap=True)
        self.body_stack.addWidget(self.body_text)
        self.body_json = widgets.CodeEditor(placeholder='{\n  "key": "value"\n}')
        self.body_stack.addWidget(self.body_json)
        # 3 form-urlencoded
        self.form_table = self._kv_table(["键", "值"])
        self.body_stack.addWidget(self._table_page(self.form_table, kind="form"))
        # 4 multipart
        self.body_stack.addWidget(self._build_multipart_page())

        lay.addWidget(self.body_stack, 1)
        self._on_body_type()
        return w

    def _table_page(self, table: QTableWidget, kind: str) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)
        bar = widgets.row(
            widgets.chip("＋ 添加行", lambda: self._add_kv_row(table)),
            widgets.chip("－ 删除选中", lambda: self._del_kv_row(table)),
            None,
        )
        pl.addWidget(bar)
        pl.addWidget(widgets.expanding(table), 1)
        return page

    def _build_multipart_page(self) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(8)

        self.mp_table = QTableWidget(0, 3)
        self.mp_table.setHorizontalHeaderLabels(["键", "类型", "值 / 文件"])
        self.mp_table.verticalHeader().setVisible(False)
        self.mp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.mp_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.mp_table.setColumnWidth(0, 150)
        self.mp_table.setColumnWidth(1, 84)

        self.mp_preview = QLabel("选择文件后在此预览图片")
        self.mp_preview.setProperty("role", "hint")
        self.mp_preview.setAlignment(Qt.AlignCenter)
        self.mp_preview.setMinimumHeight(110)
        self.mp_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        bar = widgets.row(
            widgets.chip("＋ 文本字段", lambda: self._mp_add_row(kind="文本")),
            widgets.chip("＋ 文件字段", lambda: self._mp_add_row(kind="文件")),
            widgets.chip("－ 删除选中", lambda: self._del_kv_row(self.mp_table)),
            None,
        )
        pl.addWidget(bar)
        pl.addWidget(widgets.expanding(self.mp_table), 1)
        pl.addWidget(self.mp_preview)
        return page

    def _on_body_type(self):
        idx = self.body_type.currentIndex()
        self.body_stack.setCurrentIndex(idx)

    # ---- 查询参数 tab ---- #
    def _build_params_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.params_table = self._kv_table(["键", "值"])
        lay.addWidget(widgets.row(
            widgets.chip("＋ 添加行", lambda: self._add_kv_row(self.params_table)),
            widgets.chip("－ 删除选中", lambda: self._del_kv_row(self.params_table)),
            None,
            widgets.label("发送时自动拼接到 URL", "hint"),
        ))
        lay.addWidget(widgets.expanding(self.params_table), 1)
        return w

    # ------------------------------------------------------------------ #
    #  响应面板
    # ------------------------------------------------------------------ #
    def _build_response_panel(self) -> QWidget:
        card = widgets.Card("响应")
        self.status_badge = widgets.badge("待发送", "muted")
        self.status_reason = widgets.label("", "hint")
        self.time_lb = widgets.label("", "mono")
        self.size_lb = widgets.label("", "mono")
        card.header.insertWidget(1, self.status_badge)
        card.header.insertWidget(2, self.status_reason)
        card.add_header_widget(self.time_lb)
        card.add_header_widget(self.size_lb)

        tabs = QTabWidget()
        tabs.addTab(self._build_resp_body_tab(), "响应体")
        tabs.addTab(self._build_resp_headers_tab(), "响应头")
        card.add(widgets.expanding(tabs))
        return card

    def _build_resp_body_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.view_mode = QComboBox()
        self.view_mode.addItems(["格式化", "原始文本", "原始报文", "预览"])
        self.view_mode.currentIndexChanged.connect(self._render_body_view)
        lay.addWidget(widgets.row(
            widgets.label("视图：", "label"), self.view_mode, None,
            widgets.chip("复制", self._copy_response),
        ))

        self.resp_stack = QStackedWidget()
        # 0 文本编辑器（格式化/原始共用）
        self.resp_editor = widgets.CodeEditor(placeholder="发送请求后在此显示响应…", wrap=True)
        self.resp_editor.setReadOnly(True)
        self.resp_stack.addWidget(self.resp_editor)
        # 1 预览（图片 / 富文本）
        prev = QStackedWidget()
        self.resp_image = QLabel("非图片响应，无图片预览")
        self.resp_image.setAlignment(Qt.AlignCenter)
        self.resp_image.setProperty("role", "hint")
        img_scroll = QScrollArea()
        img_scroll.setWidgetResizable(True)
        img_scroll.setWidget(self.resp_image)
        prev.addWidget(img_scroll)
        self.resp_html = QTextEdit()
        self.resp_html.setReadOnly(True)
        prev.addWidget(self.resp_html)
        self.preview_stack = prev
        self.resp_stack.addWidget(prev)

        lay.addWidget(self.resp_stack, 1)
        return w

    def _build_resp_headers_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.resp_headers_table = QTableWidget(0, 2)
        self.resp_headers_table.setHorizontalHeaderLabels(["响应头", "值"])
        self.resp_headers_table.verticalHeader().setVisible(False)
        self.resp_headers_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.resp_headers_table.setWordWrap(True)
        hh = self.resp_headers_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.resp_headers_table.setColumnWidth(0, 200)
        lay.addWidget(widgets.expanding(self.resp_headers_table), 1)
        return w

    # ------------------------------------------------------------------ #
    #  通用 键/值 表格
    # ------------------------------------------------------------------ #
    def _kv_table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        t.setColumnWidth(0, 170)
        return t

    def _add_kv_row(self, table: QTableWidget, k: str = "", v: str = ""):
        r = table.rowCount()
        table.insertRow(r)
        table.setItem(r, 0, QTableWidgetItem(k))
        table.setItem(r, 1, QTableWidgetItem(v))
        return r

    def _del_kv_row(self, table: QTableWidget):
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True)
        if not rows and table.rowCount():
            rows = [table.rowCount() - 1]
        for r in rows:
            table.removeRow(r)

    def _read_kv(self, table: QTableWidget) -> list[tuple[str, str]]:
        out = []
        for r in range(table.rowCount()):
            ki = table.item(r, 0)
            vi = table.item(r, 1)
            k = ki.text().strip() if ki else ""
            v = vi.text() if vi else ""
            if k:
                out.append((k, v))
        return out

    def _set_kv(self, table: QTableWidget, rows: list):
        table.setRowCount(0)
        for k, v in rows:
            self._add_kv_row(table, k, v)

    # ------------------------------------------------------------------ #
    #  请求头文本框：文本 <-> [(键, 值)]
    # ------------------------------------------------------------------ #
    def _read_headers_text(self) -> list[tuple[str, str]]:
        out = []
        for line in self.headers_edit.text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip()
            if k:
                out.append((k, v.strip()))
        return out

    def _set_headers_text(self, rows: list):
        lines = [f"{k}: {v}" for k, v in rows if str(k).strip()]
        self.headers_edit.set_text("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  multipart 行管理
    # ------------------------------------------------------------------ #
    def _mp_add_row(self, kind: str = "文本", key: str = "", value: str = "", path: str = ""):
        r = self.mp_table.rowCount()
        self.mp_table.insertRow(r)
        self.mp_table.setItem(r, 0, QTableWidgetItem(key))
        combo = QComboBox()
        combo.addItems(["文本", "文件"])
        combo.setCurrentText(kind if kind in ("文本", "文件") else "文本")
        combo.currentTextChanged.connect(lambda _t, c=combo: self._mp_type_changed(c))
        self.mp_table.setCellWidget(r, 1, combo)
        self._mp_set_value_cell(r, combo.currentText(), value=value, path=path)
        return r

    def _widget_row(self, table: QTableWidget, col: int, widget) -> int:
        for r in range(table.rowCount()):
            if table.cellWidget(r, col) is widget:
                return r
        return -1

    def _mp_type_changed(self, combo: QComboBox):
        r = self._widget_row(self.mp_table, 1, combo)
        if r >= 0:
            self._mp_set_value_cell(r, combo.currentText())

    def _mp_set_value_cell(self, row: int, kind: str, value: str = "", path: str = ""):
        if kind == "文件":
            self.mp_table.takeItem(row, 2)
            btn = widgets.ghost(os.path.basename(path) if path else "选择文件…")
            btn.setProperty("filepath", path)
            btn.clicked.connect(lambda _=False, b=btn: self._mp_pick_file(b))
            self.mp_table.setCellWidget(row, 2, btn)
        else:
            self.mp_table.removeCellWidget(row, 2)
            self.mp_table.setItem(row, 2, QTableWidgetItem(value))

    def _mp_pick_file(self, btn):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要上传的文件",
            filter="图片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;所有文件 (*)")
        if not path:
            return
        btn.setProperty("filepath", path)
        btn.setText(os.path.basename(path))
        self._mp_preview_image(path)

    def _mp_preview_image(self, path: str):
        try:
            pix = QPixmap(path)
            if not pix.isNull():
                self.mp_preview.setPixmap(
                    pix.scaled(220, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.mp_preview.setToolTip(path)
                return
        except Exception:
            pass
        self.mp_preview.setPixmap(QPixmap())
        self.mp_preview.setText(os.path.basename(path))

    def _mp_read_rows(self) -> list[dict]:
        rows = []
        for r in range(self.mp_table.rowCount()):
            ki = self.mp_table.item(r, 0)
            key = ki.text().strip() if ki else ""
            if not key:
                continue
            combo = self.mp_table.cellWidget(r, 1)
            kind = combo.currentText() if combo else "文本"
            if kind == "文件":
                btn = self.mp_table.cellWidget(r, 2)
                path = btn.property("filepath") if btn else ""
                rows.append({"key": key, "kind": "file", "path": path or ""})
            else:
                vi = self.mp_table.item(r, 2)
                rows.append({"key": key, "kind": "text", "value": vi.text() if vi else ""})
        return rows

    # ------------------------------------------------------------------ #
    #  组装并发送请求
    # ------------------------------------------------------------------ #
    def _normalize_url(self, url: str) -> str:
        url = (url or "").strip()
        if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", url):
            url = "http://" + url
        return url

    def _apply_params(self, url: str) -> str:
        params = self._read_kv(self.params_table)
        if not params:
            return url
        sp = urlsplit(url)
        q = parse_qsl(sp.query, keep_blank_values=True) + params
        return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q), sp.fragment))

    def _build_body(self):
        """返回 (data_bytes 或 None, 自动 content-type 或 None)。"""
        t = self.body_type.currentText()
        if t == "none":
            return None, None
        if t == "text":
            txt = self.body_text.text()
            return (txt.encode("utf-8") if txt else None), None
        if t == "JSON":
            txt = self.body_json.text().strip()
            if not txt:
                return None, "application/json"
            try:
                json.loads(txt)
            except Exception:
                widgets.notify(self, "JSON 请求体格式有误，仍按原样发送", "warn")
            return txt.encode("utf-8"), "application/json; charset=utf-8"
        if t == "form-urlencoded":
            rows = self._read_kv(self.form_table)
            return urlencode(rows).encode("utf-8"), "application/x-www-form-urlencoded"
        if t == "multipart-form":
            data, ct = self._build_multipart(self._mp_read_rows())
            return data, ct
        return None, None

    def _build_multipart(self, rows: list[dict]):
        boundary = "----DevDebug" + uuid.uuid4().hex
        buf = bytearray()
        b = boundary.encode()
        for row in rows:
            key = row["key"]
            buf += b"--" + b + b"\r\n"
            if row["kind"] == "file" and row.get("path"):
                path = row["path"]
                fname = os.path.basename(path)
                ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                with open(path, "rb") as f:
                    content = f.read()
                buf += f'Content-Disposition: form-data; name="{key}"; filename="{fname}"\r\n'.encode("utf-8")
                buf += f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
                buf += content + b"\r\n"
            else:
                val = row.get("value", "")
                buf += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8")
                buf += val.encode("utf-8") + b"\r\n"
        buf += b"--" + b + b"--\r\n"
        return bytes(buf), "multipart/form-data; boundary=" + boundary

    def _assemble_request(self) -> dict | None:
        url = self._normalize_url(self.url_edit.text())
        if not url:
            widgets.notify(self, "请输入请求地址", "error")
            return None
        method = self.method_combo.currentText()
        url = self._apply_params(url)
        headers = self._read_headers_text()
        data, auto_ct = self._build_body()
        if auto_ct and not any(k.lower() == "content-type" for k, _ in headers):
            headers.append(("Content-Type", auto_ct))
        return {
            "method": method, "url": url, "headers": headers,
            "data": data, "timeout": self.timeout_spin.value(), "insecure": True,
        }

    def _send(self):
        if self._running:
            return
        try:
            req = self._assemble_request()
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"组装请求失败：{e}", "error")
            return
        if req is None:
            return
        self._running = True
        self.send_btn.setEnabled(False)
        self.send_btn.setText("发送中…")
        self._set_status("请求中", "muted", "", "", "")
        self._push_history(req["method"], req["url"])

        self._worker = _HttpWorker(req, self)
        self._worker.done.connect(self._on_response)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    # ------------------------------------------------------------------ #
    #  响应处理
    # ------------------------------------------------------------------ #
    def _on_response(self, res: dict):
        self._running = False
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")

        if res.get("error"):
            self._set_status("错误", "err", res["error"], "", "")
            self._resp = None
            self.resp_editor.set_text(res["error"])
            self.resp_headers_table.setRowCount(0)
            widgets.notify(self, res["error"], "error")
            return

        code = res["status"]
        kind = self._code_kind(code)
        self._set_status(
            str(code), kind, res.get("reason", ""),
            f"{res['elapsed_ms']:.0f} ms", self._fmt_size(res["size"]))

        ct = res.get("content_type", "")
        raw = res.get("body", b"")
        is_image = ct.lower().startswith("image/")
        text = "" if is_image else self._decode(raw, ct)
        pretty, json_ok = self._prettify(text, ct)
        self._resp = {
            "raw": raw, "text": text, "content_type": ct,
            "is_image": is_image, "pretty": pretty, "json_ok": json_ok,
            "status": code, "reason": res.get("reason", ""), "headers": res.get("headers", []),
        }

        # 响应头表
        self._fill_headers(res.get("headers", []))

        # 默认视图：图片 -> 预览；JSON -> 格式化；否则原始
        self.view_mode.blockSignals(True)
        if is_image:
            self.view_mode.setCurrentIndex(2)
        elif json_ok:
            self.view_mode.setCurrentIndex(0)
        else:
            self.view_mode.setCurrentIndex(1)
        self.view_mode.blockSignals(False)
        self._render_body_view()

    def _fill_headers(self, headers: list):
        t = self.resp_headers_table
        t.setRowCount(0)
        for k, v in headers:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(str(k)))
            t.setItem(r, 1, QTableWidgetItem(str(v)))

    def _render_body_view(self):
        r = self._resp
        mode = self.view_mode.currentText()
        if not r:
            self.resp_stack.setCurrentIndex(0)
            return
        if mode == "预览":
            self.resp_stack.setCurrentIndex(1)
            if r["is_image"]:
                pix = QPixmap()
                pix.loadFromData(r["raw"])
                if not pix.isNull():
                    self.resp_image.setPixmap(pix)
                else:
                    self.resp_image.setText("图片解码失败")
                self.preview_stack.setCurrentIndex(0)
            else:
                ct = r["content_type"].lower()
                if "html" in ct:
                    self.resp_html.setHtml(r["text"])
                else:
                    self.resp_html.setPlainText(r["pretty"] if r["json_ok"] else r["text"])
                self.preview_stack.setCurrentIndex(1)
            return

        self.resp_stack.setCurrentIndex(0)
        if mode == "原始报文":
            self.resp_editor.set_text(self._raw_response_text(r))
        elif r["is_image"]:
            self.resp_editor.set_text(f"[图片响应 {r['content_type']}，{len(r['raw'])} 字节]\n请切换到「预览」查看。")
        elif mode == "格式化":
            self.resp_editor.set_text(r["pretty"])
        else:
            self.resp_editor.set_text(r["text"])

    @staticmethod
    def _raw_response_text(r: dict) -> str:
        lines = [f"HTTP/1.1 {r.get('status', '')} {r.get('reason', '')}".rstrip()]
        for k, v in r.get("headers", []):
            lines.append(f"{k}: {v}")
        head = "\r\n".join(lines)
        if r.get("is_image"):
            body = f"[图片响应 {r.get('content_type', '')}，{len(r.get('raw', b''))} 字节]"
        else:
            body = r.get("text", "")
        return head + "\r\n\r\n" + body

    def _copy_response(self):
        r = self._resp
        if not r:
            widgets.notify(self, "暂无响应可复制", "warn")
            return
        if r["is_image"]:
            widgets.notify(self, "图片响应无法复制文本", "warn")
            return
        mode = self.view_mode.currentText()
        txt = r["pretty"] if mode == "格式化" else r["text"]
        widgets.copy_text(self, txt)

    # ------------------------------------------------------------------ #
    #  状态行
    # ------------------------------------------------------------------ #
    def _set_status(self, code: str, kind: str, reason: str, tm: str, size: str):
        self.status_badge.setText(code)
        self.status_badge.setProperty("badge", kind)
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)
        self.status_reason.setText(reason or "")
        self.time_lb.setText(("⏱ " + tm) if tm else "")
        self.size_lb.setText(("↧ " + size) if size else "")

    @staticmethod
    def _code_kind(code: int) -> str:
        if 200 <= code < 300:
            return "ok"
        if 300 <= code < 400:
            return "muted"
        if 400 <= code < 500:
            return "warn"
        if code >= 500:
            return "err"
        return "muted"

    @staticmethod
    def _fmt_size(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n / 1024 / 1024:.2f} MB"

    @staticmethod
    def _decode(raw: bytes, content_type: str) -> str:
        charset = None
        if content_type:
            m = re.search(r"charset=([\w\-]+)", content_type, re.I)
            if m:
                charset = m.group(1)
        for enc in (charset, "utf-8", "gb18030", "latin-1"):
            if not enc:
                continue
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _prettify(text: str, content_type: str):
        looks_json = "json" in (content_type or "").lower()
        s = (text or "").lstrip()
        if not (looks_json or s[:1] in "{["):
            return text, False
        try:
            data = json.loads(text)
            return json.dumps(data, ensure_ascii=False, indent=2), True
        except Exception:
            return text, False

    # ------------------------------------------------------------------ #
    #  原始 HTTP 报文  生成 / 解析
    # ------------------------------------------------------------------ #
    def _raw_request_text(self) -> str:
        """把当前表单渲染成原始 HTTP 请求报文文本。"""
        req = self._assemble_request()
        if req is None:
            return ""
        method, url = req["method"], req["url"]
        sp = urlsplit(url)
        headers = list(req["headers"])
        lower = {k.lower() for k, _ in headers}
        lines = [f"{method} {url} HTTP/1.1"]
        if "host" not in lower and sp.netloc:
            lines.append(f"Host: {sp.netloc}")
        for k, v in headers:
            lines.append(f"{k}: {v}")
        data = req.get("data")
        if data and "content-length" not in lower:
            lines.append(f"Content-Length: {len(data)}")
        body = ""
        if data:
            try:
                body = data.decode("utf-8")
            except Exception:
                body = f"<二进制数据 {len(data)} 字节，原始报文无法文本化>"
        return "\r\n".join(lines) + "\r\n\r\n" + body

    def _raw_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("原始 HTTP 请求报文")
        dlg.resize(720, 540)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(widgets.label(
            "下方为当前请求的原始报文。可直接编辑/粘贴其它报文，点「解析并回填」导入到表单：", "label"))
        editor = widgets.CodeEditor(
            placeholder="POST https://example.com/api HTTP/1.1\n"
                        "Content-Type: application/json\n\n"
                        '{"key":"value"}',
            wrap=True)
        try:
            editor.set_text(self._raw_request_text())
        except Exception:
            pass
        v.addWidget(editor, 1)
        v.addWidget(widgets.row(
            widgets.chip("复制", lambda: widgets.copy_text(self, editor.text(), "已复制原始报文")),
            None,
            widgets.ghost("解析并回填", lambda: (self._apply_raw(editor.text()), dlg.accept())),
            widgets.primary("关闭", dlg.accept),
        ))
        dlg.exec()

    def _apply_raw(self, text: str):
        try:
            self._parse_raw_request(text)
            widgets.notify(self, "原始报文已解析回填", "success")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"报文解析失败：{e}", "error")

    def _parse_raw_request(self, text: str):
        text = (text or "").replace("\r\n", "\n").strip("\n")
        if not text.strip():
            raise ValueError("内容为空")
        # 以第一个空行分割 头部 / 正文
        if "\n\n" in text:
            head, body = text.split("\n\n", 1)
        else:
            head, body = text, ""
        head_lines = [ln for ln in head.split("\n") if ln.strip()]
        if not head_lines:
            raise ValueError("缺少请求行")
        # 请求行： METHOD  URL/PATH  HTTP/x.x
        parts = head_lines[0].split()
        if len(parts) < 2:
            raise ValueError("请求行格式不正确")
        method = parts[0].upper()
        target = parts[1]
        headers: list[tuple[str, str]] = []
        host = ""
        for ln in head_lines[1:]:
            if ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            k, v = k.strip(), v.strip()
            if k.lower() == "host":
                host = v
            headers.append((k, v))
        # 组装完整 URL
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", target):
            url = target
        elif host:
            scheme = "https" if (":443" in host or not host.endswith(":80")) else "http"
            url = f"{scheme}://{host}{target if target.startswith('/') else '/' + target}"
        else:
            url = self._normalize_url(target)

        sp = urlsplit(url)
        self.method_combo.setCurrentText(method if method in METHODS else "GET")
        self.url_edit.setText(urlunsplit((sp.scheme, sp.netloc, sp.path, "", sp.fragment)))
        self._set_kv(self.params_table, parse_qsl(sp.query, keep_blank_values=True))
        # 请求头（去掉自动管理的 Host / Content-Length）
        show_headers = [(k, v) for k, v in headers if k.lower() not in ("host", "content-length")]
        self._set_headers_text(show_headers)

        # 正文
        ctype = next((v for k, v in headers if k.lower() == "content-type"), "").lower()
        body = body.strip("\n")
        if not body:
            self.body_type.setCurrentText("none")
        elif "json" in ctype or body.lstrip()[:1] in "{[":
            self.body_type.setCurrentText("JSON")
            self.body_json.set_text(body)
        elif "x-www-form-urlencoded" in ctype:
            self.body_type.setCurrentText("form-urlencoded")
            self._set_kv(self.form_table, parse_qsl(body, keep_blank_values=True))
        else:
            self.body_type.setCurrentText("text")
            self.body_text.set_text(body)

    # ------------------------------------------------------------------ #
    #  cURL 生成 / 解析
    # ------------------------------------------------------------------ #
    def _copy_curl(self):
        try:
            req = self._assemble_request()
            if req is None:
                return
            parts = ["curl"]
            if req["method"] != "GET":
                parts += ["-X", req["method"]]
            parts.append(shlex.quote(req["url"]))
            for k, v in req["headers"]:
                parts += ["-H", shlex.quote(f"{k}: {v}")]
            bt = self.body_type.currentText()
            if bt == "multipart-form":
                for row in self._mp_read_rows():
                    if row["kind"] == "file" and row.get("path"):
                        parts += ["-F", shlex.quote(f"{row['key']}=@{row['path']}")]
                    else:
                        parts += ["-F", shlex.quote(f"{row['key']}={row.get('value', '')}")]
            elif req.get("data"):
                try:
                    body = req["data"].decode("utf-8")
                except Exception:
                    body = "<二进制数据>"
                parts += ["--data-raw", shlex.quote(body)]
            widgets.copy_text(self, "  ".join(parts), "已复制 cURL")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"生成 cURL 失败：{e}", "error")

    def _import_curl(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("导入 cURL 命令")
        dlg.resize(560, 300)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(widgets.label("粘贴 cURL 命令，解析后回填到请求表单：", "label"))
        editor = widgets.CodeEditor(placeholder="curl 'https://httpbin.org/post' -X POST -H 'Accept: application/json' --data-raw '...'", wrap=True)
        v.addWidget(editor, 1)
        v.addWidget(widgets.row(
            None,
            widgets.ghost("取消", dlg.reject),
            widgets.primary("解析", dlg.accept),
        ))
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self._parse_curl(editor.text())
            widgets.notify(self, "cURL 解析完成", "success")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"cURL 解析失败：{e}", "error")

    def _parse_curl(self, text: str):
        text = (text or "").strip()
        if not text:
            raise ValueError("内容为空")
        # 去掉行尾续行反斜杠
        text = re.sub(r"\\\s*\n", " ", text)
        tokens = shlex.split(text)
        if tokens and tokens[0] == "curl":
            tokens = tokens[1:]

        method = None
        url = None
        headers: list[tuple[str, str]] = []
        data_parts: list[str] = []
        form_rows: list[dict] = []
        i = 0
        while i < len(tokens):
            tk = tokens[i]
            if tk in ("-X", "--request"):
                i += 1
                method = tokens[i].upper()
            elif tk in ("-H", "--header"):
                i += 1
                h = tokens[i]
                if ":" in h:
                    k, val = h.split(":", 1)
                    headers.append((k.strip(), val.strip()))
            elif tk in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii"):
                i += 1
                data_parts.append(tokens[i])
            elif tk in ("-F", "--form"):
                i += 1
                f = tokens[i]
                if "=" in f:
                    k, val = f.split("=", 1)
                    if val.startswith("@"):
                        form_rows.append({"key": k, "kind": "file", "path": val[1:]})
                    else:
                        form_rows.append({"key": k, "kind": "text", "value": val})
            elif tk in ("--url",):
                i += 1
                url = tokens[i]
            elif tk in ("-u", "--user", "-A", "--user-agent", "-e", "--referer",
                        "-b", "--cookie", "--connect-timeout", "-m", "--max-time"):
                # 处理带值但此处不直接映射的选项（-A/-e/-b 转成 header）
                i += 1
                val = tokens[i]
                if tk in ("-A", "--user-agent"):
                    headers.append(("User-Agent", val))
                elif tk in ("-e", "--referer"):
                    headers.append(("Referer", val))
                elif tk in ("-b", "--cookie"):
                    headers.append(("Cookie", val))
            elif tk.startswith("-"):
                pass  # 忽略无值开关，如 -L --compressed -k
            elif url is None:
                url = tk
            i += 1

        if url:
            sp = urlsplit(self._normalize_url(url))
            self.url_edit.setText(urlunsplit((sp.scheme, sp.netloc, sp.path, "", sp.fragment)))
            self._set_kv(self.params_table, parse_qsl(sp.query, keep_blank_values=True))
        self._set_headers_text(headers)

        if form_rows:
            method = method or "POST"
            self.body_type.setCurrentText("multipart-form")
            self.mp_table.setRowCount(0)
            for row in form_rows:
                if row["kind"] == "file":
                    self._mp_add_row(kind="文件", key=row["key"], path=row["path"])
                else:
                    self._mp_add_row(kind="文本", key=row["key"], value=row["value"])
        elif data_parts:
            method = method or "POST"
            body = "&".join(data_parts)
            self.body_type.setCurrentText("JSON" if body.lstrip()[:1] in "{[" else "text")
            (self.body_json if body.lstrip()[:1] in "{[" else self.body_text).set_text(body)
        else:
            self.body_type.setCurrentText("none")

        self.method_combo.setCurrentText(method or "GET")

    # ------------------------------------------------------------------ #
    #  历史快照 / 回填
    # ------------------------------------------------------------------ #
    def _snapshot(self) -> dict:
        return {
            "method": self.method_combo.currentText(),
            "url": self.url_edit.text(),
            "timeout": self.timeout_spin.value(),
            "headers": self._read_headers_text(),
            "params": self._read_kv(self.params_table),
            "body_type": self.body_type.currentText(),
            "body_text": self.body_text.text(),
            "body_json": self.body_json.text(),
            "form": self._read_kv(self.form_table),
            "multipart": self._mp_read_rows(),
        }

    def _load_snapshot(self, s: dict):
        self.method_combo.setCurrentText(s.get("method", "GET"))
        self.url_edit.setText(s.get("url", ""))
        self.timeout_spin.setValue(int(s.get("timeout", 10)))
        self._set_headers_text(s.get("headers", []))
        self._set_kv(self.params_table, s.get("params", []))
        self.body_text.set_text(s.get("body_text", ""))
        self.body_json.set_text(s.get("body_json", ""))
        self._set_kv(self.form_table, s.get("form", []))
        self.mp_table.setRowCount(0)
        for row in s.get("multipart", []):
            if row.get("kind") == "file":
                self._mp_add_row(kind="文件", key=row.get("key", ""), path=row.get("path", ""))
            else:
                self._mp_add_row(kind="文本", key=row.get("key", ""), value=row.get("value", ""))
        self.body_type.setCurrentText(s.get("body_type", "none"))

    # ------------------------------------------------------------------ #
    def refresh_theme(self):
        """主题切换后重刷状态徽标配色。"""
        try:
            self.status_badge.style().unpolish(self.status_badge)
            self.status_badge.style().polish(self.status_badge)
        except Exception:
            pass
