"""离线中英互译模块。

翻译在独立子进程跑（外置 CPython worker），原生库与 UI 隔离。
运行库 + 模型来自程序目录 translate_data/（全离线版自带；精简版可下载一次后离线）。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QSplitter, QFileDialog, QApplication

from app import widgets
from app import translate_component as tc
from app.resources import base_dir

# 有界超时（秒）
_SPAWN_TIMEOUT = 90.0
_REQUEST_TIMEOUT = 180.0
_LINE_TIMEOUT = 60.0


def _log_path() -> str:
    """日志写到可写目录：冻结后为 exe 同级，源码为项目根。"""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent / "translate_worker.log")
    return str(base_dir() / "translate_worker.log")


class _ProcEngine:
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._logfile = _log_path()
        self._errf = None
        self._spawn_failures = 0

    def _data_root(self) -> Path:
        return tc._writable_root()

    def _argv_and_env(self):
        """只用 translate_data/py/python.exe 跑 worker；冻结进程禁止加载原生库。"""
        td = self._data_root()
        env = os.environ.copy()
        env["DEVDEBUG_TRANSLATE_DATA"] = str(td)
        env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        env["OMP_NUM_THREADS"] = env.get("OMP_NUM_THREADS") or "4"
        env["KMP_AFFINITY"] = "disabled"
        env["KMP_WARNINGS"] = "0"
        libs = td / "libs"
        py_dir = td / "py"
        path_parts = []
        if py_dir.is_dir():
            path_parts.append(str(py_dir))
        if libs.is_dir():
            path_parts.append(str(libs))
            for sub in ("ctranslate2", "numpy.libs", "onnxruntime/capi", "cv2"):
                p = libs / sub.replace("/", os.sep)
                if p.is_dir():
                    path_parts.append(str(p))
        path_parts.append(env.get("PATH", ""))
        env["PATH"] = os.pathsep.join(path_parts)

        ext_py = tc.external_python()
        worker = tc.worker_script()
        if ext_py is not None and worker is not None and tc.runtime_ok():
            env["PYTHONPATH"] = os.pathsep.join(
                [str(td), str(libs), env.get("PYTHONPATH", "")]
            )
            env["PYTHONNOUSERSITE"] = "1"
            return [str(ext_py), str(worker)], env, str(td)

        if getattr(sys, "frozen", False) or tc._uses_external_layout():
            raise RuntimeError(
                "翻译引擎未就绪：缺少外置 Python 运行时。\n"
                "请完整解压「全离线版」（必须含 translate_data/py/python.exe "
                "与 translate_data/worker_main.py），\n"
                "或点「下载离线组件」自动安装。\n"
                f"缺：{tc.missing_summary() or 'runtime'}\n"
                f"当前 translate_data：{td}"
            )
        # 源码开发且无外置布局：本机解释器
        alt = base_dir() / "app" / "translate_data_worker.py"
        if alt.is_file():
            return [sys.executable, str(alt)], env, str(base_dir())
        raise RuntimeError("找不到翻译 worker 脚本")

    def _close_errf(self):
        f = self._errf
        self._errf = None
        if f is not None:
            try:
                f.flush()
            except Exception:
                pass
            try:
                f.close()
            except Exception:
                pass

    def _readline_timeout(self, timeout: float):
        """有界读取 worker stdout 一行；超时返回 None。"""
        if self._proc is None:
            return None
        q: queue.Queue = queue.Queue(maxsize=1)

        def _reader():
            try:
                line = self._proc.stdout.readline()
                q.put(line)
            except Exception as e:  # noqa: BLE001
                q.put(e)

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        try:
            item = q.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, Exception):
            return None
        return item

    def _read_json(self, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return None
            remain = max(0.05, deadline - time.monotonic())
            line = self._readline_timeout(min(_LINE_TIMEOUT, remain))
            if line is None:
                return None
            if not line:
                return None
            try:
                return json.loads(line)
            except Exception:
                continue
        return None

    def _spawn(self):
        self._kill()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        argv, env, cwd = self._argv_and_env()
        try:
            self._errf = open(self._logfile, "w", encoding="utf-8", errors="replace")
        except Exception as e:
            raise RuntimeError(f"无法打开引擎日志：{e}") from e
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._errf,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
                env=env,
                cwd=cwd,
            )
        except Exception as e:
            self._close_errf()
            self._spawn_failures += 1
            raise RuntimeError(f"无法启动翻译子进程：{e}\n命令: {' '.join(argv)}") from e

        info = self._read_json(_SPAWN_TIMEOUT)
        if info is None:
            err = self._read_err()
            cmd = " ".join(argv)
            self._kill()
            self._spawn_failures += 1
            raise RuntimeError(
                (err or "翻译子进程启动超时或异常退出")
                + f"\n命令: {cmd}"
            )
        if not info.get("ready"):
            err = info.get("error") or "翻译引擎未就绪"
            self._kill()
            self._spawn_failures += 1
            raise RuntimeError(err)
        self._spawn_failures = 0

    def _request(self, payload: dict) -> str:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._spawn()
            try:
                self._proc.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
                self._proc.stdin.flush()
                resp = self._read_json(_REQUEST_TIMEOUT)
            except Exception:
                resp = None
            if resp is None:
                err = self._read_err()
                self._kill()
                raise RuntimeError(
                    "翻译引擎进程异常退出或超时（已隔离，未影响主程序）。"
                    + ("\n\n引擎日志：\n" + err if err else "")
                )
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "处理失败"))
            return resp.get("text", "")

    def translate(self, text, source, target):
        return self._request({
            "action": "translate", "text": text, "source": source, "target": target,
        })

    def ocr(self, path):
        return self._request({"action": "ocr", "path": path})

    def _read_err(self):
        try:
            self._close_errf()
            with open(self._logfile, encoding="utf-8", errors="replace") as f:
                return f.read().strip()[-2000:]
        except Exception:
            return ""

    def _kill(self):
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                if proc.poll() is None:
                    proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            except Exception:
                pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
        self._close_errf()


_PROC: _ProcEngine | None = None
_PROC_LOCK = threading.Lock()


def _get_proc() -> _ProcEngine:
    global _PROC
    with _PROC_LOCK:
        if _PROC is None:
            _PROC = _ProcEngine()
        return _PROC


class _TranslateWorker(QThread):
    done = Signal(dict)

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
    done = Signal(dict)

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
    """精简版：下载离线运行库+模型到 translate_data（一次联网，之后离线）。"""
    progress = Signal(str, int, int)
    finished_ok = Signal(str)

    def __init__(self, with_ocr: bool = True, parent=None):
        super().__init__(parent)
        self._with_ocr = with_ocr

    def run(self):
        try:
            tc.download_all(
                lambda s, d, t: self.progress.emit(s, d, t),
                with_ocr=self._with_ocr,
            )
            self.finished_ok.emit("")
        except Exception as e:  # noqa: BLE001
            self.finished_ok.emit(str(e) or "下载失败")


class TranslateTool(QWidget):
    _DIR_MAP = {0: ("auto", "auto"), 1: ("zh", "en"), 2: ("en", "zh")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _TranslateWorker | None = None
        self._dl_worker: _DownloadWorker | None = None
        self._ocr_worker: _OcrWorker | None = None
        self._ocr_cleanup_path: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.dir_combo = QComboBox()
        self.dir_combo.addItems(["自动检测", "中文 → 英文", "英文 → 中文"])
        self.dir_combo.setToolTip("翻译方向；自动检测按原文判断中英")

        self.btn_translate = widgets.primary(
            "翻译", self._on_translate, "离线翻译（本地模型，不联网）")
        self.btn_paste = widgets.ghost(
            "粘贴翻译", self._on_paste_translate, "粘贴翻译（文字或图片OCR）")
        self.btn_ocr = widgets.ghost(
            "图片翻译", self._on_ocr, "选择图片，离线 OCR 后翻译")
        self.btn_clear = widgets.ghost("清空", self._on_clear)
        self.btn_copy = widgets.chip("复制结果", self._on_copy)
        self.btn_download = widgets.primary(
            "下载离线组件", self._on_download,
            "下载外置Python+运行库+OCR+中英模型到 translate_data（约250MB，仅一次；之后完全离线）")
        self.btn_download.hide()

        root.addWidget(widgets.row(
            widgets.label("方向", "label"), self.dir_combo, None,
            self.btn_download, self.btn_ocr, self.btn_paste,
            self.btn_translate, self.btn_clear, self.btn_copy,
        ))

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        src_card = widgets.Card("原文", "支持多行；离线本地翻译")
        self.src_edit = widgets.CodeEditor(
            placeholder="在此输入或粘贴要翻译的中文/英文…", wrap=True)
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

    def _check_ready(self):
        if tc.is_ready():
            self.btn_download.hide()
            self.btn_translate.setEnabled(True)
            self.btn_paste.setEnabled(True)
            ocr_ok = tc.ocr_available()
            self.btn_ocr.setEnabled(ocr_ok)
            self.btn_ocr.setToolTip(
                "选择图片，离线 OCR 识别后翻译" if ocr_ok
                else "离线 OCR 未安装（全离线版自带；或下载离线组件）")
            self.src_edit.setReadOnly(False)
            self.src_edit.setPlaceholderText("在此输入或粘贴要翻译的中文/英文…（离线）")
            self.dst_edit.setPlaceholderText("译文显示在这里…")
            return
        miss = tc.missing_summary() or "离线组件"
        hint = (
            f"离线翻译未就绪（缺：{miss}）。\n"
            f"· 全离线版：完整解压，目录须含 translate_data/py/python.exe\n"
            f"· 精简版：点「下载离线组件」（含外置Python+模型，联网一次后离线）"
        )
        self.btn_translate.setEnabled(False)
        self.btn_paste.setEnabled(False)
        self.btn_ocr.setEnabled(False)
        self.src_edit.setReadOnly(True)
        self.src_edit.set_text("")
        self.src_edit.setPlaceholderText(hint)
        self.dst_edit.setPlaceholderText(hint)
        self.btn_download.show()

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_running(self, running: bool):
        self.btn_translate.setEnabled(not running)
        self.btn_paste.setEnabled(not running)
        self.btn_ocr.setEnabled(
            False if running else (tc.is_ready() and tc.ocr_available()))
        self.btn_clear.setEnabled(not running)
        self.dir_combo.setEnabled(not running)
        self.btn_translate.setText("翻译中…" if running else "翻译")

    def _on_translate(self):
        if not tc.is_ready():
            widgets.notify(self, "离线翻译未就绪，请检查 translate_data 或下载组件", "error")
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
        if not tc.is_ready():
            widgets.notify(self, "离线翻译未就绪", "error")
            self._check_ready()
            return
        if self._busy() or (self._ocr_worker is not None and self._ocr_worker.isRunning()):
            widgets.notify(self, "正在处理中，请稍候", "warn")
            return

        cb = QApplication.clipboard()
        txt = ""
        try:
            txt = cb.text() or ""
        except Exception:
            try:
                txt = widgets.paste_text() or ""
            except Exception:
                txt = ""
        if txt.strip():
            self.src_edit.set_text(txt)
            self._on_translate()
            return

        img = None
        try:
            img = cb.image()
        except Exception:
            img = None
        if img is not None and not img.isNull():
            self._paste_image_ocr(img)
            return
        widgets.notify(self, "剪贴板为空（无文字也无图片）", "warn")

    def _paste_image_ocr(self, qimage):
        if not tc.ocr_available():
            widgets.notify(self, "离线 OCR 未安装，无法识别图片文字", "error")
            return
        fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="devdebug_paste_")
        os.close(fd)
        try:
            if not qimage.save(tmp_path, "PNG"):
                raise RuntimeError("无法保存剪贴板图片")
        except Exception as e:  # noqa: BLE001
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            widgets.notify(self, f"保存图片失败：{e}", "error")
            return
        self._start_ocr(tmp_path, cleanup_path=tmp_path, from_paste=True)

    def _on_ocr(self):
        if not tc.is_ready() or not tc.ocr_available():
            widgets.notify(self, "离线 OCR 未就绪", "error")
            return
        if self._busy() or (self._ocr_worker is not None and self._ocr_worker.isRunning()):
            widgets.notify(self, "正在处理中，请稍候", "warn")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要识别翻译的图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;所有文件 (*)")
        if not path:
            return
        self._start_ocr(path)

    def _start_ocr(self, path: str, cleanup_path: str | None = None, from_paste: bool = False):
        self._ocr_cleanup_path = cleanup_path
        self.btn_ocr.setEnabled(False)
        self.btn_paste.setEnabled(False)
        self.btn_translate.setEnabled(False)
        if from_paste:
            self.btn_paste.setText("识别中…")
        else:
            self.btn_ocr.setText("识别中…")
        self._ocr_worker = _OcrWorker(path, self)
        self._ocr_worker.done.connect(self._on_ocr_done)
        self._ocr_worker.finished.connect(self._ocr_worker.deleteLater)
        self._ocr_worker.start()

    def _on_ocr_done(self, out: dict):
        tmp = getattr(self, "_ocr_cleanup_path", None)
        self._ocr_cleanup_path = None
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        self.btn_ocr.setEnabled(tc.ocr_available() if tc.is_ready() else False)
        self.btn_ocr.setText("图片翻译")
        self.btn_paste.setEnabled(tc.is_ready())
        self.btn_paste.setText("粘贴翻译")
        self.btn_translate.setEnabled(tc.is_ready())
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
        widgets.notify(self, "识别完成，正在离线翻译…", "success")
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
        try:
            if _PROC is not None:
                _PROC._kill()
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

    def _on_download(self):
        if self._dl_worker is not None and self._dl_worker.isRunning():
            return
        self.btn_download.setEnabled(False)
        self.btn_download.setText("下载中…")
        self.dst_edit.setPlaceholderText("正在下载离线组件（完成后可断网使用）…")
        # 精简版下载时带上 OCR，方便图片翻译
        self._dl_worker = _DownloadWorker(with_ocr=True, parent=self)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.finished_ok.connect(self._on_dl_done)
        self._dl_worker.finished.connect(self._dl_worker.deleteLater)
        self._dl_worker.start()

    def _on_dl_progress(self, stage: str, done: int, total: int):
        self.btn_download.setText(
            f"{stage} {done * 100 // total}%" if total > 0 else f"{stage}…")

    def _on_dl_done(self, error: str):
        self._dl_worker = None
        self.btn_download.setEnabled(True)
        self.btn_download.setText("下载离线组件")
        if error:
            widgets.notify(self, f"下载失败：{error}", "error")
            return
        # 杀掉旧子进程，下次翻译用新的 translate_data 冷启动（原生库不可热重载）
        global _PROC
        try:
            if _PROC is not None:
                _PROC._kill()
                _PROC = None
        except Exception:
            pass
        widgets.notify(self, "离线组件已就绪，可断网使用", "success")
        self._check_ready()
