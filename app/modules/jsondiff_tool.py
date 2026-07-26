"""JSON 对比模块。

左右两个 JSON 编辑区，解析后做深度对比，把差异按 路径 / 类型 / 左值 / 右值
列成一张可着色的表格：新增（绿）、删除（红）、修改（橙）、类型不同（蓝）。

解析 + 深度对比放在后台 QThread 里执行（大 JSON 也不卡界面），完成后通过信号
回主线程刷新表格。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QSplitter, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)

from app import widgets, jsonkit


# 初始示例：左右各有若干差异，涵盖 新增 / 删除 / 修改 / 类型不同 四种情况。
_SAMPLE_LEFT = """{
  "name": "项目A",
  "version": 1,
  "active": true,
  "tags": ["alpha", "beta"],
  "owner": { "id": 1001, "email": "a@x.com" },
  "score": 88,
  "note": "待办"
}"""

_SAMPLE_RIGHT = """{
  "name": "项目A",
  "version": "1",
  "active": false,
  "tags": ["alpha", "gamma", "delta"],
  "owner": { "id": 1001, "phone": "138" },
  "score": 92,
  "extra": "新字段"
}"""

# 差异类型 -> 中文名 / 调色板键
_KIND_LABEL = {"added": "新增", "removed": "删除", "changed": "修改", "type": "类型不同"}
_KIND_PAL = {"added": "ok", "removed": "err", "changed": "warn", "type": "accent"}


def _compute(left_text: str, right_text: str):
    """解析两侧并对比，返回 (diffs, error)。任一侧解析失败则返回错误消息。"""
    a, ea = jsonkit.parse(left_text)
    if ea:
        return None, f"左侧 JSON 解析失败：{ea}"
    b, eb = jsonkit.parse(right_text)
    if eb:
        return None, f"右侧 JSON 解析失败：{eb}"
    return jsonkit.deep_diff(a, b), None


# --------------------------------------------------------------------------- #
#  后台对比线程
# --------------------------------------------------------------------------- #
class _DiffWorker(QThread):
    done = Signal(dict)

    def __init__(self, left_text: str, right_text: str, parent=None):
        super().__init__(parent)
        self._left = left_text
        self._right = right_text

    def run(self):
        out = {"error": None, "diffs": None}
        try:
            diffs, err = _compute(self._left, self._right)
            out["error"] = err
            out["diffs"] = diffs
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e) or "未知错误"
        self.done.emit(out)


# --------------------------------------------------------------------------- #
#  主模块
# --------------------------------------------------------------------------- #
class JsonDiffTool(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _DiffWorker | None = None
        self._diffs: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---- 顶部操作栏 --------------------------------------------------- #
        self.btn_compare = widgets.primary("对比", self._on_compare, "解析两侧并逐路径对比差异")
        self.btn_swap = widgets.button("交换左右", on_click=self._on_swap, tip="交换左右两侧的内容")
        self.btn_format_all = widgets.button("全部格式化", on_click=self._on_format_all, tip="把两侧 JSON 都格式化")
        self.btn_clear_all = widgets.ghost("全部清空", self._on_clear_all, "清空两侧编辑区与差异结果")
        toolbar = widgets.row(
            self.btn_compare, self.btn_swap, self.btn_format_all,
            None,
            self.btn_clear_all,
        )
        root.addWidget(toolbar)

        # ---- 主体：上（左编辑 | 右编辑） + 下（差异表） 可拖拽 ------------- #
        v_split = QSplitter(Qt.Vertical)
        v_split.setChildrenCollapsible(False)

        top_split = QSplitter(Qt.Horizontal)
        top_split.setChildrenCollapsible(False)

        left_card, self.left_editor = self._make_editor_card("左侧 JSON", "left")
        right_card, self.right_editor = self._make_editor_card("右侧 JSON", "right")
        self.left_editor.set_text(_SAMPLE_LEFT)
        self.right_editor.set_text(_SAMPLE_RIGHT)
        top_split.addWidget(left_card)
        top_split.addWidget(right_card)
        top_split.setStretchFactor(0, 1)
        top_split.setStretchFactor(1, 1)
        top_split.setSizes([540, 540])

        # ---- 差异结果区 --------------------------------------------------- #
        diff_card = widgets.Card("差异结果", "点「对比」后逐路径列出，颜色区分新增 / 删除 / 修改 / 类型不同")
        self.summary = widgets.badge("尚未对比", "muted")
        self.btn_copy = widgets.chip("复制差异", self._on_copy, "把差异列表复制为文本")
        diff_card.add(widgets.row(self.summary, None, self.btn_copy))

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["路径", "类型", "左值", "右值"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 280)
        diff_card.add(widgets.expanding(self.table))

        v_split.addWidget(top_split)
        v_split.addWidget(diff_card)
        v_split.setStretchFactor(0, 3)
        v_split.setStretchFactor(1, 2)
        v_split.setSizes([440, 300])

        root.addWidget(v_split, 1)

        self._action_btns = [
            self.btn_compare, self.btn_swap, self.btn_format_all, self.btn_clear_all,
        ]

        # 初始示例做一次同步对比，让界面一进来就有内容（示例很小，安全）。
        try:
            diffs, err = _compute(self.left_editor.text(), self.right_editor.text())
            if not err:
                self._render_diffs(diffs)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    #  编辑卡片
    # ------------------------------------------------------------------ #
    def _make_editor_card(self, title: str, side: str):
        card = widgets.Card(title, "在此粘贴 / 编辑 JSON")
        card.add_header_widget(widgets.chip("格式化", lambda: self._format_one(side)))
        card.add_header_widget(widgets.chip("压缩", lambda: self._minify_one(side)))
        card.add_header_widget(widgets.chip("粘贴", lambda: self._paste_one(side)))
        card.add_header_widget(widgets.chip("清空", lambda: self._clear_one(side)))
        editor = widgets.CodeEditor(placeholder="{ ... }")
        card.add(widgets.expanding(editor))
        return card, editor

    def _editor(self, side: str):
        return self.left_editor if side == "left" else self.right_editor

    def _side_name(self, side: str) -> str:
        return "左侧" if side == "left" else "右侧"

    # ------------------------------------------------------------------ #
    #  单侧小工具
    # ------------------------------------------------------------------ #
    def _format_one(self, side: str):
        ed = self._editor(side)
        data, err = jsonkit.parse(ed.text())
        if err:
            widgets.notify(self, f"{self._side_name(side)}格式化失败：{err}", "error")
            return
        ed.set_text(jsonkit.pretty(data))
        widgets.notify(self, f"{self._side_name(side)}已格式化", "success")

    def _minify_one(self, side: str):
        ed = self._editor(side)
        data, err = jsonkit.parse(ed.text())
        if err:
            widgets.notify(self, f"{self._side_name(side)}压缩失败：{err}", "error")
            return
        ed.set_text(jsonkit.minify(data))
        widgets.notify(self, f"{self._side_name(side)}已压缩", "success")

    def _paste_one(self, side: str):
        try:
            text = widgets.paste_text()
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"读取剪贴板失败：{e}", "error")
            return
        if not text:
            widgets.notify(self, "剪贴板为空", "warn")
            return
        self._editor(side).set_text(text)
        widgets.notify(self, f"已粘贴到{self._side_name(side)}", "success")

    def _clear_one(self, side: str):
        self._editor(side).set_text("")

    # ------------------------------------------------------------------ #
    #  顶部操作
    # ------------------------------------------------------------------ #
    def _on_swap(self):
        l, r = self.left_editor.text(), self.right_editor.text()
        self.left_editor.set_text(r)
        self.right_editor.set_text(l)
        widgets.notify(self, "已交换左右", "info")

    def _on_format_all(self):
        errs = []
        touched = False
        for side in ("left", "right"):
            ed = self._editor(side)
            if not ed.text().strip():
                continue
            data, err = jsonkit.parse(ed.text())
            if err:
                errs.append(f"{self._side_name(side)}：{err}")
                continue
            ed.set_text(jsonkit.pretty(data))
            touched = True
        if errs:
            widgets.notify(self, "格式化失败 — " + "；".join(errs), "error")
        elif touched:
            widgets.notify(self, "两侧已格式化", "success")
        else:
            widgets.notify(self, "两侧均为空", "warn")

    def _on_clear_all(self):
        if self._busy():
            return
        self.left_editor.set_text("")
        self.right_editor.set_text("")
        self.table.setRowCount(0)
        self._diffs = []
        self._set_summary("尚未对比", "muted")
        widgets.notify(self, "已全部清空", "success")

    # ------------------------------------------------------------------ #
    #  对比（后台线程）
    # ------------------------------------------------------------------ #
    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_running(self, running: bool):
        for b in self._action_btns:
            b.setEnabled(not running)
        self.btn_compare.setText("对比中…" if running else "对比")

    def _on_compare(self):
        if self._busy():
            widgets.notify(self, "上一次对比还在进行，请稍候", "warn")
            return
        left, right = self.left_editor.text(), self.right_editor.text()
        if not left.strip() or not right.strip():
            widgets.notify(self, "两侧都需要有内容才能对比", "warn")
            return
        self._set_running(True)
        self._worker = _DiffWorker(left, right, self)
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_done(self, out: dict):
        self._set_running(False)
        self._worker = None
        err = out.get("error")
        if err:
            widgets.notify(self, err, "error")
            return
        diffs = out.get("diffs") or []
        self._render_diffs(diffs)
        if diffs:
            widgets.notify(self, f"对比完成，共 {len(diffs)} 处差异", "success")
        else:
            widgets.notify(self, "两个 JSON 完全一致", "success")

    # ------------------------------------------------------------------ #
    #  结果渲染
    # ------------------------------------------------------------------ #
    def _set_summary(self, text: str, kind: str):
        self.summary.setText(text)
        self.summary.setProperty("badge", kind)
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)

    def _cell(self, text: str, color: QColor | None = None, bold: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFont(widgets.mono_font(12))
        if color is not None:
            item.setForeground(QBrush(color))
        if bold:
            f = item.font()
            f.setBold(True)
            item.setFont(f)
        if text:
            item.setToolTip(text)
        return item

    def _render_diffs(self, diffs: list[dict]):
        self._diffs = diffs or []
        p = widgets.pal()
        self.table.setRowCount(0)

        counts = {"added": 0, "removed": 0, "changed": 0, "type": 0}
        for d in self._diffs:
            counts[d.get("type", "")] = counts.get(d.get("type", ""), 0) + 1

        total = len(self._diffs)
        if total == 0:
            self._set_summary("两个 JSON 完全一致", "ok")
            return
        self._set_summary(
            f"共 {total} 处差异（新增{counts['added']} / 删除{counts['removed']} / "
            f"修改{counts['changed']} / 类型{counts['type']}）",
            "warn",
        )

        muted = QColor(p["fg_muted"])
        path_color = QColor(p["fg_dim"])
        val_color = QColor(p["fg"])
        self.table.setRowCount(total)
        for row, d in enumerate(self._diffs):
            kind = d.get("type", "")
            kind_color = QColor(p.get(_KIND_PAL.get(kind, "fg"), p["fg"]))
            label = _KIND_LABEL.get(kind, kind)

            left = d.get("left")
            right = d.get("right")
            left_txt = "（无）" if left is None else str(left)
            right_txt = "（无）" if right is None else str(right)

            self.table.setItem(row, 0, self._cell(d.get("path", ""), path_color))
            self.table.setItem(row, 1, self._cell(label, kind_color, bold=True))
            self.table.setItem(row, 2, self._cell(left_txt, muted if left is None else val_color))
            self.table.setItem(row, 3, self._cell(right_txt, muted if right is None else val_color))

    # ------------------------------------------------------------------ #
    #  复制
    # ------------------------------------------------------------------ #
    def _on_copy(self):
        if not self._diffs:
            widgets.notify(self, "暂无差异可复制", "warn")
            return
        lines = ["路径\t类型\t左值\t右值"]
        for d in self._diffs:
            left = d.get("left")
            right = d.get("right")
            lines.append("\t".join([
                d.get("path", ""),
                _KIND_LABEL.get(d.get("type", ""), d.get("type", "")),
                "" if left is None else str(left),
                "" if right is None else str(right),
            ]))
        widgets.copy_text(self, "\n".join(lines))

    # ------------------------------------------------------------------ #
    #  主题刷新（切换深浅色时由主窗口调用）
    # ------------------------------------------------------------------ #
    def refresh_theme(self):
        try:
            kind = self.summary.property("badge") or "muted"
            self._set_summary(self.summary.text(), kind)
            if self._diffs is not None:
                self._render_diffs(self._diffs)
        except Exception:  # noqa: BLE001
            pass
