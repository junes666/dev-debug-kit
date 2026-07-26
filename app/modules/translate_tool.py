"""离线中英互译模块。

翻译在**独立子进程**里跑（app/translate_worker.py，经 main.py --translate-worker 启动），
以隔离 ctranslate2 原生库在部分 Windows 上的硬崩溃 —— 就算引擎进程崩了，主程序也只会
弹出错误并显示子进程日志，绝不闪退。

依赖/模型齐全时开箱即用（全离线版）；缺失时（精简版）提供「下载翻译组件」一键下载。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QSplitter, QFileDialog, QApplication

from app import widgets
from app import translate_component as tc
from app.resources import base_dir


# --------------------------------------------------------------------------- #
#  子进程翻译引擎（崩溃隔离）
# --------------------------------------------------------------------------- #
class _ProcEngine:
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._logfile = str(base_dir() / "translate_worker.log")

    def _argv(self):
        if getattr(sys, "frozen", False):
            return [sys.executable, "--translate-worker"]
        return [sys.executable, str(base_dir() / "main.py"), "--translate-worker"]

    def _spawn(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        errf = open(self._logfile, "w", encoding="utf-8", errors="replace")
        self._proc = subprocess.Popen(
            self._argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf,
            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags,
        )
        info = self._read_json()   # 跳过原生库可能打到 stdout 的杂输出
        if info is None:
            raise RuntimeError(self._read_err() or "翻译子进程启动失败")
        if not info.get("ready"):
            raise RuntimeError(info.get("error") or "翻译引擎未就绪")

    def _read_json(self):
        """读一行 JSON 响应；跳过非 JSON 噪声行；EOF 返回 None。"""
        for _ in range(500):
            line = self._proc.stdout.readline()
            if not line:
                return None
            try:
                return json.loads(line)
            except Exception:
                continue
        return None

    def _request(self, payload: dict) -> str:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._spawn()
            try:
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
                resp = self._read_json()
            except Exception:
                resp = None
            if resp is None:   # EOF（进程崩溃）或输出无有效 JSON
                err = self._read_err()
                self._kill()
                raise RuntimeError("翻译引擎进程异常退出（已隔离，未影响主程序）。"
                                   + ("\n\n引擎日志：\n" + err if err else ""))
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "处理失败"))
            return resp.get("text", "")

    def translate(self, text, source, target):
        return self._request({"action": "translate", "text": text, "source": source, "target": target})

    def ocr(self, path):
        return self._request({"action": "ocr", "path": path})

    def _read_err(self):
        try:
            with open(self._logfile, encoding="utf-8", errors="replace") as f:
                return f.read().strip()[-1500:]
        except Exception:
            return ""

    def _kill(self):
        try:
            if self._proc:
                self._proc.kill()
        except Exception:
            pass
        self._proc = None


_PROC: _ProcEngine | None = None
_PROC_LOCK = threading.Lock()


def _get_proc() -> _ProcEngine:
    global _PROC
    with _PROC_LOCK:
        if _PROC is None:
            _PROC = _ProcEngine()
        return _PROC


# --------------------------------------------------------------------------- #
#  后台线程
# --------------------------------------------------------------------------- #
class _TranslateWorker(QThread):
    done = Signal(dict)   # {"text": str, "error": str|None}

    def __init__(self, text, source, target, parent=None):
        super().__init__(parent)
        self._text, self._source, self._target = text, source, target

    def run(self):
        out = {"text": "", "error": None}
        try:
            out["text"] = _get_proc().translate(self._text, self._source, self._target)
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e) or "未知错误"
        self.done.emit(out)


class _OcrWorker(QThread):
    """在子进程里对图片做 OCR，返回识别文字。"""
    done = Signal(dict)   # {"text": str, "error": str|None}

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        out = {"text": "", "error": None}
        try:
            out["text"] = _get_proc().ocr(self._path)
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e) or "未知错误"
        self.done.emit(out)


class _DownloadWorker(QThread):
    """精简版：后台下载翻译组件(ct2/sentencepiece/numpy + 模型)。"""
    progress = Signal(str, int, int)
    finished_ok = Signal(str)

    def run(self):
        try:
            tc.download_all(lambda s, d, t: self.progress.emit(s, d, t))
            self.finished_ok.emit("")
        except Exception as e:  # noqa: BLE001
            self.finished_ok.emit(str(e) or "下载失败")


# --------------------------------------------------------------------------- #
#  主模块
# --------------------------------------------------------------------------- #
class TranslateTool(QWidget):
    _DIR_MAP = {0: ("auto", "auto"), 1: ("zh", "en"), 2: ("en", "zh")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _TranslateWorker | None = None
        self._dl_worker: _DownloadWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.dir_combo = QComboBox()
        self.dir_combo.addItems(["自动检测", "中文 → 英文", "英文 → 中文"])
        self.dir_combo.setToolTip("翻译方向；自动检测按原文判断中英")

        self.btn_translate = widgets.primary("翻译", self._on_translate, "在独立进程后台翻译（首次加载模型稍慢）")
        self.btn_paste = widgets.ghost("粘贴翻译", self._on_paste_translate, "粘贴剪贴板文字并立即翻译")
        self.btn_ocr = widgets.ghost("图片翻译", self._on_ocr, "选择图片，OCR 识别文字后翻译")
        self.btn_clear = widgets.ghost("清空", self._on_clear)
        self.btn_copy = widgets.chip("复制结果", self._on_copy)
        self.btn_download = widgets.primary("下载翻译组件", self._on_download,
                                            "下载翻译运行库与中英模型（约180MB，一次性，之后离线；图片OCR请用全离线版）")
        self.btn_download.hide()
        self._ocr_worker: _OcrWorker | None = None

        root.addWidget(widgets.row(
            widgets.label("方向", "label"), self.dir_combo, None,
            self.btn_download, self.btn_ocr, self.btn_paste, self.btn_translate, self.btn_clear, self.btn_copy,
        ))

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        src_card = widgets.Card("原文", "支持多行；逐行逐句翻译")
        self.src_edit = widgets.CodeEditor(placeholder="在此输入或粘贴要翻译的中文/英文…", wrap=True)
        src_card.add(widgets.expanding(self.src_edit))
        split.addWidget(src_card)
        dst_card = widgets.Card("译文", "只读；翻译结果显示在这里")
        self.dst_edit = widgets.CodeEditor(placeholder="译文显示在这里…", wrap=True)
        self.dst_edit.setReadOnly(True)
        dst_card.add(widgets.expanding(self.dst_edit))
        split.addWidget(dst_card)
        split.setSizes([1, 1])
        root.addWidget(split, 1)

        self._check_ready()

    # ------------------------------------------------------------------ #
    def _check_ready(self):
        if tc.is_ready():
            self.btn_download.hide()
            self.btn_translate.setEnabled(True)
            self.btn_paste.setEnabled(True)
            ocr_ok = tc.ocr_available()   # OCR 依赖较重，仅全离线版内置
            self.btn_ocr.setEnabled(ocr_ok)
            self.btn_ocr.setToolTip("选择图片，OCR 识别文字后翻译" if ocr_ok
                                    else "图片翻译（OCR）需使用全离线版")
            self.src_edit.setReadOnly(False)
            self.src_edit.setPlaceholderText("在此输入或粘贴要翻译的中文/英文…")
            self.dst_edit.setPlaceholderText("译文显示在这里…")
            return
        miss = tc.missing_summary()
        hint = (f"翻译组件未安装（缺：{miss}）。点「下载翻译组件」自动下载"
                f"（约 180MB，一次性；之后完全离线）。")
        self.btn_translate.setEnabled(False)
        self.btn_paste.setEnabled(False)
        self.btn_ocr.setEnabled(False)
        self.src_edit.setReadOnly(True)
        self.src_edit.set_text("")
        self.src_edit.setPlaceholderText(hint)
        self.dst_edit.setPlaceholderText(hint)
        self.btn_download.show()

    # ---- 翻译 ---- #
    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_running(self, running: bool):
        self.btn_translate.setEnabled(not running)
        self.btn_paste.setEnabled(not running)
        self.btn_ocr.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.dir_combo.setEnabled(not running)
        self.btn_translate.setText("翻译中…" if running else "翻译")

    def _on_translate(self):
        if not tc.is_ready():
            widgets.notify(self, "翻译组件未安装，请先点「下载翻译组件」", "error")
            self._check_ready()
            return
        if self._busy():
            widgets.notify(self, "上一次翻译还在进行中", "warn")
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

    def _on_paste_translate(self):
        txt = widgets.paste_text()
        if not txt.strip():
            widgets.notify(self, "剪贴板为空", "warn")
            return
        self.src_edit.set_text(txt)
        self._on_translate()

    # ---- 图片 OCR 翻译 ---- #
    def _on_ocr(self):
        if not tc.is_ready() or not tc.ocr_available():
            widgets.notify(self, "图片翻译（OCR）需使用全离线版（含 OCR 引擎）", "error")
            return
        if self._busy() or (self._ocr_worker is not None and self._ocr_worker.isRunning()):
            widgets.notify(self, "正在处理中，请稍候", "warn")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要识别翻译的图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*)")
        if not path:
            return
        self.btn_ocr.setEnabled(False)
        self.btn_ocr.setText("识别中…")
        self._ocr_worker = _OcrWorker(path, self)
        self._ocr_worker.done.connect(self._on_ocr_done)
        self._ocr_worker.finished.connect(self._ocr_worker.deleteLater)
        self._ocr_worker.start()

    def _on_ocr_done(self, out: dict):
        self.btn_ocr.setEnabled(True)
        self.btn_ocr.setText("图片翻译")
        self._ocr_worker = None
        err = out.get("error")
        if err:
            self.dst_edit.set_text(err)
            widgets.notify(self, "OCR 失败（详见译文区）", "error")
            return
        text = (out.get("text") or "").strip()
        if not text:
            widgets.notify(self, "未识别到文字", "warn")
            return
        self.src_edit.set_text(text)
        widgets.notify(self, "识别完成，正在翻译…", "success")
        self._on_translate()

    def _on_done(self, out: dict):
        self._set_running(False)
        self._worker = None
        err = out.get("error")
        if err:
            self.dst_edit.set_text(err)
            widgets.notify(self, "翻译失败（详见译文区）", "error")
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

    def cleanup(self):
        """退出前回收后台线程与子进程，避免 QThread 仍在运行时被析构导致 abort。"""
        try:
            if _PROC is not None:
                _PROC._kill()   # 杀子进程，让阻塞在 readline 的翻译/OCR 线程尽快返回
        except Exception:
            pass
        for w in (self._worker, self._ocr_worker, self._dl_worker):
            try:
                if w is not None and w.isRunning():
                    w.wait(1500)
                    if w.isRunning():
                        w.terminate()
                        w.wait(500)
            except Exception:
                pass

    # ---- 下载翻译组件（精简版）---- #
    def _on_download(self):
        if self._dl_worker is not None and self._dl_worker.isRunning():
            return
        self.btn_download.setEnabled(False)
        self.btn_download.setText("下载中…")
        self.dst_edit.setPlaceholderText("正在下载翻译组件，请保持联网…")
        self._dl_worker = _DownloadWorker(self)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.finished_ok.connect(self._on_dl_done)
        self._dl_worker.finished.connect(self._dl_worker.deleteLater)
        self._dl_worker.start()

    def _on_dl_progress(self, stage: str, done: int, total: int):
        self.btn_download.setText(f"{stage} {done * 100 // total}%" if total > 0 else f"{stage}…")

    def _on_dl_done(self, error: str):
        self._dl_worker = None
        self.btn_download.setEnabled(True)
        self.btn_download.setText("下载翻译组件")
        if error:
            widgets.notify(self, f"下载失败：{error}", "error")
            return
        widgets.notify(self, "翻译组件已就绪", "success")
        self._check_ready()
