"""JSON 解析模块。

左侧输入 / 编辑 JSON，支持格式化、压缩、校验、转义 / 反转义、复制、粘贴、清空；
右侧把合法 JSON 渲染成可展开的三列树（键 / 值 / 类型），点击节点显示其
JSONPath 路径与原始值。若节点的值是图片（data:image base64 或以图片后缀结尾的
http(s) 链接），则在信息区显示预览——网络图片放到后台 QThread 里下载，避免卡死。
"""
from __future__ import annotations

import base64
import io
import json
import re
import urllib.parse
import urllib.request

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget, QLineEdit,
    QHeaderView, QAbstractItemView, QLabel, QScrollArea,
)

from app import widgets, jsonkit


_IMG_URL_RE = re.compile(
    r"(?i)^https?://\S+\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?\S*)?$"
)


def _sample_avatar() -> str:
    """生成一个离线可见的图片 data URI（用 segno 画二维码），失败则退回 1x1 PNG。"""
    try:
        import segno  # 项目已内置
        buf = io.BytesIO()
        segno.make("开发调试 · JSON").save(buf, kind="png", scale=3, border=2)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1"
                "HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _sample_text() -> str:
    data = {
        "name": "开发调试",
        "version": 3,
        "enabled": True,
        "ratio": 0.618,
        "tags": ["json", "tree", "预览"],
        "avatar": _sample_avatar(),
        "logo_url": "https://example.com/assets/logo.png",
        "meta": {
            "author": "Claude",
            "count": 42,
            "empty": None,
            "nested": {"x": 1, "y": [1, 2, 3]},
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
#  后台图片下载线程（桌面端无跨域限制，用 urllib 直接抓字节）
# --------------------------------------------------------------------------- #
class _ImageWorker(QThread):
    done = Signal(int, bytes, str)  # token, data, error

    def __init__(self, token: int, url: str, parent=None):
        super().__init__(parent)
        self._token = token
        self._url = url

    def run(self):
        try:
            req = urllib.request.Request(
                self._url, headers={"User-Agent": "Mozilla/5.0 (offline-tool)"})
            with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
                data = resp.read(8 * 1024 * 1024)  # 最多 8MB，防止超大文件
            self.done.emit(self._token, data, "")
        except Exception as e:  # noqa: BLE001
            self.done.emit(self._token, b"", str(e) or "下载失败")


# --------------------------------------------------------------------------- #
#  主模块
# --------------------------------------------------------------------------- #
class JsonTool(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        self._img_worker: _ImageWorker | None = None
        self._img_token = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        main_split = QSplitter(Qt.Horizontal)
        main_split.setChildrenCollapsible(False)
        main_split.addWidget(self._build_left())
        main_split.addWidget(self._build_right())
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([540, 540])
        root.addWidget(main_split, 1)

        # 输入防抖：改动后自动重建树
        from PySide6.QtCore import QTimer
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(lambda: self._rebuild_tree())
        self.editor.textChanged.connect(self._debounce.start)

        # 初始示例：同步构建（不走防抖，保证首帧即有内容）
        self.editor.blockSignals(True)
        self.editor.set_text(_sample_text())
        self.editor.blockSignals(False)
        self._rebuild_tree(select_image=True)

    # ------------------------------------------------------------------ #
    #  左侧：输入 + 工具条 + 状态
    # ------------------------------------------------------------------ #
    def _build_left(self) -> QWidget:
        card = widgets.Card("JSON 输入", "编辑 / 粘贴 JSON，下方工具处理，右侧实时成树")

        self.editor = widgets.CodeEditor(placeholder="在此粘贴或编写 JSON…")
        card.add(widgets.expanding(self.editor))

        toolbar = widgets.row(
            widgets.primary("格式化", self._on_format, "解析后按 2 空格缩进美化"),
            widgets.chip("压缩", self._on_minify, "解析后压缩成单行"),
            widgets.chip("校验", self._on_validate, "校验是否合法并提示行列信息"),
            widgets.chip("转义", self._on_escape, "把当前文本整体作为字符串做 JSON 转义"),
            widgets.chip("反转义", self._on_unescape, "把转义后的字符串还原"),
            None,
            widgets.ghost("复制", self._on_copy, "复制输入框内容"),
            widgets.ghost("粘贴", self._on_paste, "把剪贴板内容填入输入框"),
            widgets.danger("清空", self._on_clear, "清空输入与结果树"),
        )
        card.add(toolbar)

        self.status_badge = widgets.badge("待校验", "muted")
        self.status_label = widgets.label("等待输入…", "hint")
        card.add(widgets.row(self.status_badge, (self.status_label, 1)))
        return card

    # ------------------------------------------------------------------ #
    #  右侧：结果树 + 信息区（可上下拖拽）
    # ------------------------------------------------------------------ #
    def _build_right(self) -> QWidget:
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        # ---- 结果树 ---- #
        tree_card = widgets.Card("结果树", "键 / 值 / 类型 · 点击节点查看路径与值")
        tree_card.add_header_widget(widgets.chip("展开全部", self._expand_all))
        tree_card.add_header_widget(widgets.chip("折叠全部", self._collapse_all))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["键", "值", "类型"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(2, 80)
        self.tree.currentItemChanged.connect(self._on_current)
        self.tree.itemClicked.connect(lambda it, _c: self._on_node(it))
        tree_card.add(widgets.expanding(self.tree))
        split.addWidget(tree_card)

        # ---- 信息区 ---- #
        info_card = widgets.Card("节点信息", "选中节点的 JSONPath 路径、原始值与图片预览")

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setFont(widgets.mono_font(12))
        self.path_edit.setPlaceholderText("$")
        info_card.add(widgets.row(
            widgets.label("路径", "label"),
            (self.path_edit, 1),
            widgets.chip("复制路径", self._copy_path),
        ))

        self.type_label = widgets.label("—", "mono")
        info_card.add(widgets.row(
            widgets.label("值", "label"),
            (self.type_label, 1),
            widgets.chip("复制值", self._copy_value),
        ))

        self.value_view = widgets.CodeEditor(placeholder="点击左侧树节点查看其值…", wrap=True)
        self.value_view.setReadOnly(True)
        self.value_view.setFixedHeight(120)
        info_card.add(self.value_view)

        # 图片预览（默认隐藏）
        self.img_box = QWidget()
        img_lay = QVBoxLayout(self.img_box)
        img_lay.setContentsMargins(0, 4, 0, 0)
        img_lay.setSpacing(6)
        img_lay.addWidget(widgets.label("图片预览", "label"))
        self.img_label = QLabel("—")
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setMinimumHeight(120)
        p = widgets.pal()
        self.img_label.setStyleSheet(
            f"QLabel{{background:{p['bg1']};border:1px solid {p['border']};"
            f"border-radius:8px;color:{p['fg_muted']};padding:8px;}}")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.img_label)
        img_lay.addWidget(scroll, 1)
        self.img_box.hide()
        info_card.add(widgets.expanding(self.img_box))

        split.addWidget(info_card)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([420, 300])
        return split

    # ------------------------------------------------------------------ #
    #  状态提示
    # ------------------------------------------------------------------ #
    def _set_status(self, kind: str, text: str):
        label = {"ok": "合法", "err": "非法", "muted": "待校验"}.get(kind, "—")
        self.status_badge.setText(label)
        self.status_badge.setProperty("badge", kind)
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)
        self.status_label.setText(text)

    # ------------------------------------------------------------------ #
    #  构建结果树
    # ------------------------------------------------------------------ #
    def _rebuild_tree(self, notify_ok: bool = False, select_image: bool = False):
        text = self.editor.text()
        if not text.strip():
            self.tree.clear()
            self.data = None
            self._set_status("muted", "等待输入…")
            if notify_ok:
                widgets.notify(self, "内容为空", "warn")
            return None
        try:
            data, err = jsonkit.parse(text)
        except Exception as e:  # noqa: BLE001
            data, err = None, str(e)
        if err:
            self._set_status("err", err)
            if notify_ok:
                widgets.notify(self, f"解析失败：{err}", "error")
            return None
        self.data = data
        try:
            jsonkit.populate_tree(self.tree, data)
        except Exception as e:  # noqa: BLE001
            self._set_status("err", f"渲染失败：{e}")
            widgets.notify(self, f"渲染失败：{e}", "error")
            return None
        self._set_status("ok", "JSON 合法")
        if notify_ok:
            widgets.notify(self, "校验通过，JSON 合法", "success")
        if select_image:
            self._select_first_image()
        return data

    def _expand_all(self):
        self.tree.expandAll()

    def _collapse_all(self):
        self.tree.collapseAll()
        root = self.tree.topLevelItem(0)
        if root:
            root.setExpanded(True)

    # ------------------------------------------------------------------ #
    #  工具条动作
    # ------------------------------------------------------------------ #
    def _on_format(self):
        data, err = self._safe_parse()
        if err:
            return
        try:
            self.editor.set_text(jsonkit.pretty(data, 2))
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"格式化失败：{e}", "error")
            return
        self._rebuild_tree()
        widgets.notify(self, "已格式化", "success")

    def _on_minify(self):
        data, err = self._safe_parse()
        if err:
            return
        try:
            self.editor.set_text(jsonkit.minify(data))
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"压缩失败：{e}", "error")
            return
        self._rebuild_tree()
        widgets.notify(self, "已压缩为单行", "success")

    def _on_validate(self):
        self._rebuild_tree(notify_ok=True)

    def _on_escape(self):
        text = self.editor.text()
        if not text:
            widgets.notify(self, "内容为空", "warn")
            return
        try:
            escaped = json.dumps(text, ensure_ascii=False)[1:-1]
            self.editor.set_text(escaped)
            widgets.notify(self, "已转义为 JSON 字符串", "success")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"转义失败：{e}", "error")

    def _on_unescape(self):
        text = self.editor.text()
        if not text:
            widgets.notify(self, "内容为空", "warn")
            return
        result = self._try_unescape(text)
        if result is None:
            widgets.notify(self, "反转义失败：不是合法的转义字符串", "error")
            return
        self.editor.set_text(result)
        widgets.notify(self, "已反转义", "success")

    @staticmethod
    def _try_unescape(text: str):
        # 1) 直接把整段当作 JSON 字符串体来解析
        try:
            return json.loads('"' + text + '"')
        except Exception:  # noqa: BLE001
            pass
        # 2) 先把可能存在的裸控制字符转义，再解析（容错常见的换行/制表）
        try:
            safe = (text.replace("\r\n", "\\n").replace("\n", "\\n")
                        .replace("\r", "\\n").replace("\t", "\\t"))
            return json.loads('"' + safe + '"')
        except Exception:  # noqa: BLE001
            pass
        # 3) 手工替换常见转义序列（含 \uXXXX）
        mapping = {"\\n": "\n", "\\t": "\t", "\\r": "\r", "\\\"": "\"",
                   "\\\\": "\\", "\\/": "/", "\\b": "\b", "\\f": "\f"}

        def repl(m):
            g = m.group(0)
            if g.startswith("\\u"):
                try:
                    return chr(int(g[2:], 16))
                except Exception:  # noqa: BLE001
                    return g
            return mapping.get(g, g[1:])

        try:
            return re.sub(r"\\u[0-9a-fA-F]{4}|\\.", repl, text)
        except Exception:  # noqa: BLE001
            return None

    def _on_copy(self):
        widgets.copy_text(self, self.editor.text())

    def _on_paste(self):
        text = widgets.paste_text()
        if not text:
            widgets.notify(self, "剪贴板为空", "warn")
            return
        self.editor.set_text(text)
        widgets.notify(self, "已粘贴", "success")

    def _on_clear(self):
        self._img_token += 1  # 作废进行中的图片下载
        self.editor.clear()
        self.tree.clear()
        self.data = None
        self.path_edit.clear()
        self.type_label.setText("—")
        self.value_view.set_text("")
        self.img_box.hide()
        self._set_status("muted", "等待输入…")
        widgets.notify(self, "已清空", "success")

    def _safe_parse(self):
        text = self.editor.text()
        try:
            data, err = jsonkit.parse(text)
        except Exception as e:  # noqa: BLE001
            data, err = None, str(e)
        if err:
            self._set_status("err", err)
            widgets.notify(self, f"解析失败：{err}", "error")
        return data, err

    # ------------------------------------------------------------------ #
    #  节点选中
    # ------------------------------------------------------------------ #
    def _on_current(self, cur, _prev):
        if cur is not None:
            self._on_node(cur)

    def _on_node(self, item):
        if item is None:
            return
        try:
            path = jsonkit.item_path(item)
            value = jsonkit.item_value(item)
        except Exception:  # noqa: BLE001
            return
        self.path_edit.setText(path)
        tname = jsonkit.type_name(value)
        try:
            is_leaf = jsonkit.item_is_leaf(item)
        except Exception:  # noqa: BLE001
            is_leaf = not isinstance(value, (dict, list))
        if is_leaf:
            self.type_label.setText(tname)
            self.value_view.set_text(self._leaf_text(value))
        else:
            n = len(value) if isinstance(value, (dict, list)) else 0
            self.type_label.setText(f"{tname} · {n} 项")
            try:
                self.value_view.set_text(jsonkit.pretty(value, 2))
            except Exception:  # noqa: BLE001
                self.value_view.set_text(str(value))
        self._update_image(value)

    @staticmethod
    def _leaf_text(v) -> str:
        if isinstance(v, str):
            return v
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        try:
            return json.dumps(v, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return str(v)

    def _copy_path(self):
        widgets.copy_text(self, self.path_edit.text(), "已复制路径")

    def _copy_value(self):
        widgets.copy_text(self, self.value_view.text(), "已复制值")

    # ------------------------------------------------------------------ #
    #  图片预览
    # ------------------------------------------------------------------ #
    @staticmethod
    def _image_kind(value):
        if not isinstance(value, str):
            return None
        s = value.strip()
        if s.startswith("data:image/"):
            return "data"
        if _IMG_URL_RE.match(s):
            return "url"
        return None

    def _update_image(self, value):
        self._img_token += 1  # 切换节点即作废旧的下载结果
        kind = self._image_kind(value)
        if kind is None:
            self.img_box.hide()
            return
        s = value.strip()
        self.img_box.show()
        if kind == "data":
            self._show_data_image(s)
        else:
            self._start_download(s)

    def _show_data_image(self, s: str):
        try:
            header, _, payload = s.partition(",")
            if "base64" in header.lower():
                raw = base64.b64decode(payload)
            else:  # 例如内联 svg（url 编码文本）
                raw = urllib.parse.unquote(payload).encode("utf-8")
        except Exception as e:  # noqa: BLE001
            self.img_label.setPixmap(QPixmap())
            self.img_label.setText(f"无法解码图片：{e}")
            return
        self._set_pixmap_from_bytes(raw)

    def _start_download(self, url: str):
        self.img_label.setPixmap(QPixmap())
        self.img_label.setText("正在后台下载图片…")
        token = self._img_token
        worker = _ImageWorker(token, url, self)
        worker.done.connect(self._on_image_loaded)
        worker.finished.connect(worker.deleteLater)
        self._img_worker = worker
        worker.start()

    def _on_image_loaded(self, token: int, data: bytes, err: str):
        if token != self._img_token:
            return  # 已切换节点，丢弃过期结果
        if err:
            self.img_label.setPixmap(QPixmap())
            self.img_label.setText(f"图片加载失败：{err}")
            return
        self._set_pixmap_from_bytes(data)

    def _set_pixmap_from_bytes(self, raw: bytes):
        pm = QPixmap()
        if not raw or not pm.loadFromData(raw):
            self.img_label.setPixmap(QPixmap())
            self.img_label.setText("无法解析为图片")
            return
        max_w, max_h = 320, 320
        if pm.width() > max_w or pm.height() > max_h:
            pm = pm.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img_label.setText("")
        self.img_label.setPixmap(pm)

    # ------------------------------------------------------------------ #
    #  辅助：选中第一个图片节点（初始示例展示预览）
    # ------------------------------------------------------------------ #
    def _select_first_image(self):
        target = None
        stack = []
        for i in range(self.tree.topLevelItemCount()):
            stack.append(self.tree.topLevelItem(i))
        while stack:
            it = stack.pop(0)
            try:
                if self._image_kind(jsonkit.item_value(it)) is not None:
                    target = it
                    break
            except Exception:  # noqa: BLE001
                pass
            for c in range(it.childCount()):
                stack.append(it.child(c))
        if target is None:
            target = self.tree.topLevelItem(0)
        if target is not None:
            self.tree.setCurrentItem(target)
            self.tree.scrollToItem(target)

    # ------------------------------------------------------------------ #
    #  主题刷新（切换深浅色时由主窗口调用）
    # ------------------------------------------------------------------ #
    def refresh_theme(self):
        try:
            p = widgets.pal()
            self.img_label.setStyleSheet(
                f"QLabel{{background:{p['bg1']};border:1px solid {p['border']};"
                f"border-radius:8px;color:{p['fg_muted']};padding:8px;}}")
            if self.data is not None:
                jsonkit.populate_tree(self.tree, self.data)
        except Exception:  # noqa: BLE001
            pass
