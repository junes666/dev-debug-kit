"""JS 调试模块。

在离线 quickjs 引擎里运行用户 JS（纯 V8 或浏览器模拟），捕获 console 输出、
求值表达式、并把定义的变量/函数/类解析成一棵可展开的作用域树。

所有引擎调用（运行 / 格式化 / 压缩）都放在后台 QThread 里执行，通过信号回主
线程刷新 UI，避免界面卡死。
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QSplitter, QComboBox, QSpinBox, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QHeaderView, QAbstractItemView,
    QLabel,
)

from app import widgets
from app.jsengine import JsEngine


# 初始示例代码：含一个函数与一个类，运行即有变量树内容。
_SAMPLE = """// 示例：定义函数 / 类 / 对象，点击「运行」查看右侧变量树。
function greet(name, greeting = "你好") {
  return `${greeting}, ${name}!`;
}

class Counter {
  constructor(start = 0) {
    this.value = start;
  }
  inc(step = 1) {
    this.value += step;
    return this.value;
  }
  get double() {
    return this.value * 2;
  }
  static zero() {
    return new Counter(0);
  }
}

const config = { debug: true, retries: 3, tags: ["a", "b"] };
const nums = [1, 2, 3, 5, 8];

const c = new Counter(10);
console.log("greet =>", greet("世界"));
console.info("counter =>", c.inc(5));
console.warn("config.retries =", config.retries);
"""


# --------------------------------------------------------------------------- #
#  后台工作线程：运行 / 格式化 / 压缩
# --------------------------------------------------------------------------- #
class _EngineWorker(QThread):
    done = Signal(dict)

    def __init__(self, engine: JsEngine, op: str, code: str,
                 expr: str = "", env: str = "v8", timeout: float = 3.0, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._op = op
        self._code = code
        self._expr = expr
        self._env = env
        self._timeout = timeout

    def run(self):
        out = {"op": self._op, "error": None}
        try:
            if self._op == "run":
                out["res"] = self._engine.run(
                    self._code, expr=self._expr, env=self._env, timeout=self._timeout)
            elif self._op == "format":
                out["code"] = self._engine.format_js(self._code)
            elif self._op == "minify":
                out["code"] = self._engine.minify_js(self._code)
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e) or "未知错误"
        self.done.emit(out)


# --------------------------------------------------------------------------- #
#  主模块
# --------------------------------------------------------------------------- #
class JsTool(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = JsEngine()
        self._worker: _EngineWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---- 顶部工具栏 ---------------------------------------------------- #
        self.env_combo = QComboBox()
        self.env_combo.addItem("V8（纯 ECMAScript）", "v8")
        self.env_combo.addItem("浏览器模拟", "browser")
        self.env_combo.setToolTip("选择执行环境：纯 ECMAScript 或带 window/document 的浏览器模拟")

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 60)
        self.timeout_spin.setValue(3)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setFixedWidth(84)
        self.timeout_spin.setToolTip("单次执行的超时时间")

        self.btn_run = widgets.primary("▶ 运行", self._on_run, "在后台执行代码并求值表达式（Ctrl+Enter）")
        self.btn_format = widgets.button("格式化", on_click=self._on_format, tip="美化代码")
        self.btn_minify = widgets.button("压缩", on_click=self._on_minify, tip="压缩代码（Terser）")
        self.btn_clear = widgets.ghost("清空", self._on_clear, "清空代码、变量树与日志")
        self.btn_scope = widgets.button("解析变量", on_click=self._on_scope, tip="只运行并刷新变量树，不求值表达式")

        toolbar = widgets.row(
            widgets.label("环境", "label"), self.env_combo,
            widgets.label("超时", "label"), self.timeout_spin,
            None,
            self.btn_run, self.btn_format, self.btn_minify, self.btn_scope, self.btn_clear,
        )
        root.addWidget(toolbar)

        # ---- 主体：上（代码 | 变量树） + 下（日志） 可拖拽 ------------------ #
        v_split = QSplitter(Qt.Vertical)
        v_split.setChildrenCollapsible(False)

        top_split = QSplitter(Qt.Horizontal)
        top_split.setChildrenCollapsible(False)

        # 左：代码 + 执行表达式行
        code_card = widgets.Card("代码", "定义函数 / 类 / 变量；console.* 输出见下方日志")
        self.editor = widgets.CodeEditor(placeholder="// 在此编写 JavaScript…")
        self.editor.set_text(_SAMPLE)
        code_card.add(widgets.expanding(self.editor))

        self.expr_edit = QLineEdit()
        self.expr_edit.setProperty("mono", True)
        self.expr_edit.setFont(widgets.mono_font(12))
        self.expr_edit.setPlaceholderText("执行函数 / 表达式，如  greet('Claude')  或  a('a','w',1)")
        self.expr_edit.setText("greet('Claude')")
        self.expr_edit.returnPressed.connect(self._on_run)
        self.btn_exec = widgets.button("执行", on_click=self._on_run, tip="在同一作用域里求值该表达式")
        exec_row = widgets.row(
            widgets.label("表达式", "label"),
            (self.expr_edit, 1),
            self.btn_exec,
        )
        code_card.add(exec_row)
        top_split.addWidget(code_card)

        # 右：变量树
        tree_card = widgets.Card("变量树", "运行后解析出的函数与类（类可展开查看方法），双击填入执行框")
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["名称", "类型", "签名"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hdr = self.tree.header()
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 190)
        self.tree.setColumnWidth(1, 90)
        self.tree.itemDoubleClicked.connect(self._on_tree_double)
        tree_card.add(widgets.expanding(self.tree))
        top_split.addWidget(tree_card)

        top_split.setStretchFactor(0, 3)
        top_split.setStretchFactor(1, 2)
        top_split.setSizes([640, 420])

        # 下：日志
        log_card = widgets.Card("控制台输出", "console.log / info / warn / error · 表达式返回值 · 运行错误")
        self.btn_copy_log = widgets.chip("复制", self._copy_log, "复制全部日志")
        log_card.add_header_widget(self.btn_copy_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(widgets.mono_font(12))
        self.log.setPlaceholderText("运行后这里显示 console 输出、表达式返回值与错误信息…")
        log_card.add(widgets.expanding(self.log))

        v_split.addWidget(top_split)
        v_split.addWidget(log_card)
        v_split.setStretchFactor(0, 3)
        v_split.setStretchFactor(1, 1)
        v_split.setSizes([480, 220])

        root.addWidget(v_split, 1)

        # 便于批量禁用/恢复的动作按钮
        self._action_btns = [
            self.btn_run, self.btn_format, self.btn_minify,
            self.btn_scope, self.btn_clear, self.btn_exec,
        ]

    # ------------------------------------------------------------------ #
    #  颜色
    # ------------------------------------------------------------------ #
    @staticmethod
    def _kind_color(kind: str) -> QColor:
        p = widgets.pal()
        if kind == "function":
            key = "accent_hi"
        elif kind == "class":
            key = "warn"
        elif kind in ("object", "array"):
            key = "fg_dim"
        else:
            key = "fg"
        return QColor(p[key])

    # ------------------------------------------------------------------ #
    #  运行 / 解析
    # ------------------------------------------------------------------ #
    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_running(self, running: bool):
        for b in self._action_btns:
            b.setEnabled(not running)
        self.btn_run.setText("运行中…" if running else "▶ 运行")

    def _start(self, op: str, expr: str = ""):
        if self._busy():
            widgets.notify(self, "上一次操作还在执行中，请稍候", "warn")
            return
        code = self.editor.text()
        if op in ("format", "minify") and not code.strip():
            widgets.notify(self, "代码为空", "warn")
            return
        env = self.env_combo.currentData() or "v8"
        timeout = float(self.timeout_spin.value())
        self._set_running(True)
        self._worker = _EngineWorker(self.engine, op, code, expr, env, timeout, self)
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_run(self):
        self._start("run", self.expr_edit.text().strip())

    def _on_scope(self):
        self._start("run", "")

    def _on_format(self):
        self._start("format")

    def _on_minify(self):
        self._start("minify")

    def _on_clear(self):
        if self._busy():
            return
        self.editor.set_text("")
        self.expr_edit.clear()
        self.tree.clear()
        self.log.clear()
        widgets.notify(self, "已清空", "success")

    # ------------------------------------------------------------------ #
    #  后台完成回调（主线程）
    # ------------------------------------------------------------------ #
    def _on_done(self, out: dict):
        self._set_running(False)
        self._worker = None
        op = out.get("op")
        err = out.get("error")

        if op in ("format", "minify"):
            if err:
                widgets.notify(self, f"{'格式化' if op == 'format' else '压缩'}失败：{err}", "error")
                return
            self.editor.set_text(out.get("code") or "")
            widgets.notify(self, "已格式化" if op == "format" else "已压缩", "success")
            return

        # op == "run"
        if err:  # 线程内部意外异常
            self._render_logs([], None, None, err, expr=self.expr_edit.text().strip())
            widgets.notify(self, f"运行失败：{err}", "error")
            return

        res = out.get("res") or {}
        expr = self.expr_edit.text().strip()
        self._build_tree(res.get("scope") or [])
        self._render_logs(
            res.get("logs") or [],
            res.get("result"), res.get("result_type"),
            res.get("error"), expr=expr,
        )
        if res.get("error"):
            widgets.notify(self, "执行出错，详见控制台", "error")
        else:
            widgets.notify(self, "运行完成", "success")

    # ------------------------------------------------------------------ #
    #  变量树
    # ------------------------------------------------------------------ #
    def _build_tree(self, scope: list):
        self.tree.clear()
        # 只保留顶层的函数与类，过滤掉普通变量（object/array/string/...）
        entries = [e for e in (scope or []) if e.get("kind") in ("function", "class")]
        if not entries:
            placeholder = QTreeWidgetItem(["（无函数或类）", "", ""])
            placeholder.setForeground(0, QColor(widgets.pal()["fg_muted"]))
            self.tree.addTopLevelItem(placeholder)
            return
        for entry in entries:
            name = entry.get("name", "")
            kind = entry.get("kind", "")
            sig = entry.get("sig", "")
            preview = entry.get("preview", "")
            members = entry.get("members") or []
            top = QTreeWidgetItem([name, kind, sig])
            color = self._kind_color(kind)
            top.setForeground(0, color)
            top.setForeground(1, color)
            top.setToolTip(2, preview or sig)
            # 双击模板（函数/类）——函数参数仍从作用域数据里取，只是不展开显示
            template = self._call_template(name, kind, members)
            if template:
                top.setData(0, Qt.UserRole, template)
            # 类：只展开方法与静态方法；函数：作为叶子，不加任何子节点
            if kind == "class":
                for m in members:
                    if m.get("kind") not in ("method", "static"):
                        continue
                    m_name = m.get("name", "")
                    m_kind = m.get("kind", "")
                    m_sig = m.get("sig", "")
                    m_prev = m.get("preview")
                    child = QTreeWidgetItem([m_name, m_kind, m_sig])
                    child.setForeground(1, QColor(widgets.pal()["fg_muted"]))
                    if m_prev:
                        child.setToolTip(2, m_prev)
                    top.addChild(child)
            self.tree.addTopLevelItem(top)
        # 默认展开顶层类，便于查看方法
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.text(1) == "class":
                it.setExpanded(True)

    @staticmethod
    def _call_template(name: str, kind: str, members: list) -> str:
        if kind == "function":
            params = [m.get("name", "") for m in members if m.get("kind") == "param"]
            params = [p for p in params if p]
            return f"{name}({', '.join(params)})"
        if kind == "class":
            return f"new {name}()"
        return ""

    def _on_tree_double(self, item: QTreeWidgetItem, _col: int):
        template = item.data(0, Qt.UserRole)
        if template:
            self.expr_edit.setText(template)
            self.expr_edit.setFocus()
            # 选中括号内内容，方便替换参数
            l = template.find("(")
            r = template.rfind(")")
            if l >= 0 and r > l + 1:
                self.expr_edit.setSelection(l + 1, r - l - 1)
            widgets.notify(self, "已填入执行框，按回车执行", "info")

    # ------------------------------------------------------------------ #
    #  日志渲染
    # ------------------------------------------------------------------ #
    def _render_logs(self, logs: list, result, result_type, error, expr: str = ""):
        p = widgets.pal()
        level_color = {
            "log": p["fg"], "info": p["accent_hi"], "warn": p["warn"],
            "error": p["err"], "debug": p["fg_muted"],
        }
        parts: list[str] = []

        def line(text: str, color: str, bold: bool = False):
            safe = html.escape(text).replace("\n", "<br>")
            weight = "font-weight:600;" if bold else ""
            parts.append(f'<span style="color:{color};{weight}white-space:pre-wrap;">{safe}</span>')

        if not logs:
            line("（无 console 输出）", p["fg_muted"])
        for entry in logs:
            lv = entry.get("level", "log")
            tag = {"log": "log", "info": "info", "warn": "warn",
                   "error": "error", "debug": "debug"}.get(lv, lv)
            line(f"[{tag}] {entry.get('text', '')}", level_color.get(lv, p["fg"]))

        # 分隔
        parts.append(f'<span style="color:{p["border2"]};">{"─" * 40}</span>')

        if error:
            line(f"✖ 错误：{error}", p["err"], bold=True)
        elif expr:
            rt = f"  ({result_type})" if result_type else ""
            val = result if result is not None else "undefined"
            line(f"↩ 返回值：{val}{rt}", p["ok"], bold=True)
        else:
            line("（未求值表达式；点「运行/执行」可在同一作用域求值）", p["fg_muted"])

        self.log.clear()
        self.log.appendHtml("<br>".join(parts))
        self.log.verticalScrollBar().setValue(0)

    def _copy_log(self):
        widgets.copy_text(self, self.log.toPlainText())

    # ------------------------------------------------------------------ #
    #  主题刷新（切换深浅色时由主窗口调用）
    # ------------------------------------------------------------------ #
    def refresh_theme(self):
        try:
            # 变量树的前景色是手工设置的，重建一次以适配新主题
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                color = self._kind_color(it.text(1))
                it.setForeground(0, color)
                it.setForeground(1, color)
        except Exception:  # noqa: BLE001
            pass
