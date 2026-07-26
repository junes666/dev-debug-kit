"""离线中英互译模块。

翻译引擎（ctranslate2 + sentencepiece）内联移植自 offline-translator-web/core.py，
去掉 OCR 部分。模型根目录取项目 models/（含 zh_en/ 与 en_zh/，各有 model/ 子目录
与 sentencepiece.model）。

首次翻译时惰性创建 Engine 并加载模型；所有推理放在后台 QThread 执行，通过信号回
主线程刷新界面，避免卡死。ctranslate2 / sentencepiece 缺失或无模型时，模块仍能正常
加载并显示提示，不抛异常。
"""
from __future__ import annotations

import os
import re
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QSplitter

from app import widgets
from app.resources import res

os.environ.setdefault("KMP_AFFINITY", "disabled")
os.environ.setdefault("KMP_TOPOLOGY_METHOD", "all")
os.environ.setdefault("KMP_WARNINGS", "0")
os.environ.setdefault("OMP_NUM_THREADS", "4")

# 依赖用 try import 保护：缺失时模块仍可加载，界面显示提示。
try:
    import ctranslate2
    import sentencepiece as spm
    _DEPS = True
except Exception:  # noqa: BLE001
    ctranslate2 = None
    spm = None
    _DEPS = False


# --------------------------------------------------------------------------- #
#  内联翻译引擎（移植自 core.py 的 Engine，去掉 OCR）
# --------------------------------------------------------------------------- #
_CJK = r"一-鿿㐀-䶿　-〿＀-￯"


def is_cjk_char(ch):
    return "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"


def detect_lang(text):
    for ch in text:
        if is_cjk_char(ch):
            return "zh"
    return "en"


def _detok(tokens):
    return "".join(tokens).replace("▁", " ").strip()


def _collapse_cjk_spaces(s):
    return re.sub(r"(?<=[%s])\s+(?=[%s])" % (_CJK, _CJK), "", s).strip()


def _normalize_cjk_punct(s):
    table = {",": "，", ".": "。", "?": "？", "!": "！", ":": "：",
             ";": "；", "(": "（", ")": "）"}
    return "".join(table.get(c, c) for c in s)


def _split_sentences(line, is_zh):
    if is_zh:
        parts = re.split(r"(?<=[。！？!?；;])", line)
    else:
        parts = re.split(r"(?<=[.!?;])\s+", line)
    return [p.strip() for p in parts if p.strip()]


class Engine:
    DIRS = {("zh", "en"): "zh_en", ("en", "zh"): "en_zh"}

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        self.root = str(res("models"))

    def available(self):
        return [d for d in ("zh_en", "en_zh")
                if os.path.isdir(os.path.join(self.root, d, "model"))]

    def _load(self, name):
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            base = os.path.join(self.root, name)
            t = ctranslate2.Translator(os.path.join(base, "model"),
                                       device="cpu", intra_threads=4)
            sp = spm.SentencePieceProcessor()
            with open(os.path.join(base, "sentencepiece.model"), "rb") as f:
                sp.LoadFromSerializedProto(f.read())
            self._cache[name] = (t, sp)
            return t, sp

    def resolve(self, text, source="auto", target="auto"):
        f = source if source in ("zh", "en") else detect_lang(text)
        t = target if target in ("zh", "en") else ("en" if f == "zh" else "zh")
        if f == t:
            t = "en" if f == "zh" else "zh"
        return f, t

    def translate(self, text, source="auto", target="auto"):
        f, t = self.resolve(text, source, target)
        name = self.DIRS.get((f, t))
        if name is None:
            raise ValueError("暂不支持 %s -> %s" % (f, t))
        tr, sp = self._load(name)
        to_zh = (t == "zh")
        out_lines = []
        for line in text.split("\n"):
            if not line.strip():
                out_lines.append("")
                continue
            sents = _split_sentences(line, f == "zh")
            batch = [sp.encode(s, out_type=str) for s in sents]
            results = tr.translate_batch(batch, beam_size=4, max_batch_size=8)
            pieces = [_detok(r.hypotheses[0]) for r in results]
            joined = ("" if to_zh else " ").join(pieces)
            if to_zh:
                joined = _normalize_cjk_punct(_collapse_cjk_spaces(joined))
            out_lines.append(joined)
        return {"from": f, "to": t, "translated": "\n".join(out_lines)}


# 惰性单例：第一次翻译再创建，缓存模型于 Engine 内部。
_ENGINE: Engine | None = None
_ENGINE_LOCK = threading.Lock()


def _get_engine() -> Engine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = Engine()
        return _ENGINE


# --------------------------------------------------------------------------- #
#  后台翻译线程
# --------------------------------------------------------------------------- #
class _TranslateWorker(QThread):
    done = Signal(dict)  # {"text": str, "error": str|None}

    def __init__(self, text: str, source: str, target: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._source = source
        self._target = target

    def run(self):
        out = {"text": "", "error": None}
        try:
            eng = _get_engine()
            r = eng.translate(self._text, self._source, self._target)
            out["text"] = r.get("translated", "")
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e) or "未知错误"
        self.done.emit(out)


# --------------------------------------------------------------------------- #
#  主模块
# --------------------------------------------------------------------------- #
class TranslateTool(QWidget):
    # 方向 -> (source, target)
    _DIR_MAP = {
        0: ("auto", "auto"),
        1: ("zh", "en"),
        2: ("en", "zh"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _TranslateWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ---- 顶部工具栏 -------------------------------------------------- #
        self.dir_combo = QComboBox()
        self.dir_combo.addItems(["自动检测", "中文 → 英文", "英文 → 中文"])
        self.dir_combo.setToolTip("翻译方向；自动检测会根据原文判断中英")

        self.btn_translate = widgets.primary("翻译", self._on_translate, "在后台加载模型并翻译（首次较慢）")
        self.btn_clear = widgets.ghost("清空", self._on_clear, "清空原文与译文")
        self.btn_copy = widgets.chip("复制结果", self._on_copy, "复制译文到剪贴板")

        root.addWidget(widgets.row(
            widgets.label("方向", "label"), self.dir_combo,
            None,
            self.btn_translate, self.btn_clear, self.btn_copy,
        ))

        # ---- 主体：左原文 / 右译文 -------------------------------------- #
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        src_card = widgets.Card("原文", "支持多行；逐行逐句翻译")
        self.src_edit = widgets.CodeEditor(placeholder="在此输入要翻译的中文或英文…", wrap=True)
        src_card.add(widgets.expanding(self.src_edit))
        split.addWidget(src_card)

        dst_card = widgets.Card("译文", "只读；翻译结果显示在这里")
        self.dst_edit = widgets.CodeEditor(placeholder="译文显示在这里…", wrap=True)
        self.dst_edit.setReadOnly(True)
        dst_card.add(widgets.expanding(self.dst_edit))
        split.addWidget(dst_card)

        split.setSizes([1, 1])
        root.addWidget(split, 1)

        # ---- 依赖/模型不可用时的提示 ------------------------------------- #
        self._check_ready()

    # ------------------------------------------------------------------ #
    #  就绪检查：依赖缺失或无模型时禁用翻译并提示
    # ------------------------------------------------------------------ #
    def _check_ready(self):
        hint = ""
        if not _DEPS:
            hint = "翻译需要 ctranslate2 与 sentencepiece，且需 models/ 模型目录"
        else:
            try:
                avail = _get_engine().available()
            except Exception:  # noqa: BLE001
                avail = []
            if not avail:
                hint = "翻译需要 ctranslate2 与 sentencepiece，且需 models/ 模型目录"
        if hint:
            self.btn_translate.setEnabled(False)
            self.src_edit.setReadOnly(True)
            self.src_edit.set_text("")
            self.src_edit.setPlaceholderText(hint)
            self.dst_edit.setPlaceholderText(hint)

    # ------------------------------------------------------------------ #
    #  运行
    # ------------------------------------------------------------------ #
    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_running(self, running: bool):
        self.btn_translate.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.dir_combo.setEnabled(not running)
        self.btn_translate.setText("翻译中…" if running else "翻译")

    def _on_translate(self):
        if not _DEPS:
            widgets.notify(self, "翻译需要 ctranslate2 与 sentencepiece，且需 models/ 模型目录", "error")
            return
        if self._busy():
            widgets.notify(self, "上一次翻译还在进行中，请稍候", "warn")
            return
        text = self.src_edit.text()
        if not text.strip():
            widgets.notify(self, "原文为空", "warn")
            return
        source, target = self._DIR_MAP.get(self.dir_combo.currentIndex(), ("auto", "auto"))

        self._set_running(True)
        self._worker = _TranslateWorker(text, source, target, self)
        self._worker.done.connect(self._on_done)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_done(self, out: dict):
        self._set_running(False)
        self._worker = None
        err = out.get("error")
        if err:
            widgets.notify(self, f"翻译失败：{err}", "error")
            return
        self.dst_edit.set_text(out.get("text") or "")
        widgets.notify(self, "翻译完成", "success")

    def _on_clear(self):
        if self._busy():
            return
        self.src_edit.set_text("")
        self.dst_edit.set_text("")

    def _on_copy(self):
        try:
            widgets.copy_text(self, self.dst_edit.text())
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"复制失败：{e}", "error")
