#!/usr/bin/env python3
"""翻译/OCR 自动验收（A–F 关键路径）。

用法（在项目根）:
  python3 scripts/accept_translate.py              # 全量（需 dist 已 pack）
  python3 scripts/accept_translate.py --quick      # 跳过 slim 下载与 GUI
  python3 scripts/accept_translate.py --pack-only  # 只 pack + worker 测
  python3 scripts/accept_translate.py --skip-download  # 含 E/F，跳过 D 下载
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP = ROOT / "dist" / "开发调试"
TD = APP / "translate_data"
SCREENSHOT = Path("/home/ubuntu/work/taobao_screenshot.png")
# 项目最终验收必须用 wine11（11.x），不用系统 Wine 9
_WINE11 = Path.home() / "wine11" / "bin" / "wine"
if not _WINE11.is_file():
    raise SystemExit(f"验收需要 {_WINE11}（Wine 11），系统 Wine 9 不作最终结论")
WINE = str(_WINE11)
os.environ["WINE"] = WINE
os.environ.setdefault("WINEPREFIX", str(Path.home() / ".wine"))
os.environ.setdefault("WINEDEBUG", "-all")
os.environ["PATH"] = str(Path.home() / "wine11" / "bin") + os.pathsep + os.environ.get("PATH", "")

# 本项目 GUI 验收用的 Xvfb 分辨率（勿与系统 :99 1920x1080 混淆）
_ACCEPT_XVFB_GEOM = "1280x800"
_EXE_NAME = "开发调试.exe"
_EXE_MARKER = str((APP / _EXE_NAME).resolve())


class Fail(Exception):
    pass


def _p(msg: str):
    print(msg, flush=True)


def wine_path(linux_path: Path) -> str:
    """Linux 路径 -> Wine Z: 路径。"""
    p = linux_path.resolve()
    return "Z:" + str(p).replace("/", "\\")


def _list_project_gui_pids() -> list[tuple[int, str]]:
    """列出本项目验收可能残留的 Xvfb(1280x800) / 本项目 EXE / 相关 wine。

    按进程 **argv[0] 程序名** 匹配，避免把仅在命令行字符串里提到
    ``开发调试.exe`` 的 bash/agent 会话误判为残留。
    绝不包含系统 Xvfb :99（1920x1080）。
    """
    out: list[tuple[int, str]] = []
    try:
        raw = subprocess.check_output(["ps", "-eo", "pid=,args="], text=True, errors="replace")
    except Exception:
        return out
    self_pid = os.getpid()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == self_pid:
            continue
        cmd = parts[1]
        # 系统 :99 保护
        if "Xvfb :99" in cmd:
            continue
        if "Xvfb" in cmd and "1920x1080" in cmd:
            continue
        try:
            import shlex
            argv = shlex.split(cmd, posix=True)
        except Exception:
            argv = cmd.split()
        if not argv:
            continue
        prog = Path(argv[0]).name.lower()
        # 壳进程：即使 cmdline 提到 exe 路径也不算残留
        if prog in (
            "bash", "sh", "dash", "zsh", "fish", "python", "python3",
            "rg", "grep", "ps", "sleep", "head", "stat", "date",
        ):
            continue

        hit = False
        # 1) 验收专用 Xvfb 几何
        if prog == "xvfb" and _ACCEPT_XVFB_GEOM in cmd:
            hit = True
        # 2) 直接就是本项目 PE（Wine 有时这样显示）
        elif prog == _EXE_NAME.lower() or argv[0].endswith(_EXE_NAME) or argv[0].endswith(
            _EXE_NAME.replace(".exe", "")
        ):
            # 路径需落在本项目 dist
            joined = " ".join(argv)
            if "debug/dist" in joined.replace("\\", "/") or _EXE_MARKER in joined or str(APP) in joined:
                hit = True
            elif prog == _EXE_NAME.lower():
                hit = True
        # 3) wine / xvfb-run 且参数里带本项目 exe
        elif prog in ("wine", "wine64", "wine-preloader", "xvfb-run"):
            for a in argv[1:]:
                al = a.replace("\\", "/")
                if al.endswith(_EXE_NAME) or _EXE_MARKER in al or f"dist/开发调试/{_EXE_NAME}" in al:
                    hit = True
                    break
        if hit:
            out.append((pid, cmd))
    return out


def _kill_process_group(pgid: int, term_wait: float = 5.0) -> None:
    """对整个进程组：先 SIGTERM，超时 SIGKILL。"""
    if pgid <= 1:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    deadline = time.monotonic() + term_wait
    while time.monotonic() < deadline:
        try:
            # 若组内已无进程，killpg 会 ProcessLookupError
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        time.sleep(0.2)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _assert_no_project_gui_residuals(label: str = "") -> None:
    left = _list_project_gui_pids()
    if left:
        detail = "\n".join(f"  pid={p} {c}" for p, c in left)
        raise Fail(f"本项目 GUI/Xvfb/EXE 残留{('('+label+')') if label else ''}:\n{detail}")


class WorkerClient:
    def __init__(self, td: Path, timeout_spawn=90, timeout_req=180):
        self.td = td
        self.py = td / "py" / "python.exe"
        self.worker = td / "worker_main.py"
        self.timeout_spawn = timeout_spawn
        self.timeout_req = timeout_req
        self.proc = None
        self._err_path = td / "accept_worker.log"

    def start(self):
        if not self.py.is_file():
            raise Fail(f"missing {self.py}")
        if not self.worker.is_file():
            raise Fail(f"missing {self.worker}")
        env = os.environ.copy()
        env["DEVDEBUG_TRANSLATE_DATA"] = str(self.td)
        env["WINEDEBUG"] = "-all"
        env["PYTHONNOUSERSITE"] = "1"
        libs = self.td / "libs"
        env["PYTHONPATH"] = str(self.td) + os.pathsep + str(libs)
        errf = open(self._err_path, "w", encoding="utf-8", errors="replace")
        cmd = [WINE, str(self.py), str(self.worker)]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errf,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.td),
            env=env,
            bufsize=1,
            start_new_session=True,
        )
        self._errf = errf
        line = self._readline(self.timeout_spawn)
        if not line:
            err = self._read_err()
            self.stop()
            raise Fail(f"worker 无 ready 输出\n{err}")
        try:
            info = json.loads(line)
        except Exception as e:
            self.stop()
            raise Fail(f"ready 非 JSON: {line!r} ({e})")
        if not info.get("ready"):
            self.stop()
            raise Fail(f"ready:false {info}")
        _p(f"  worker ready: {info}")
        return info

    def _readline(self, timeout: float) -> str | None:
        import queue
        import threading

        q: queue.Queue = queue.Queue()

        def r():
            try:
                q.put(self.proc.stdout.readline())
            except Exception as e:
                q.put(e)

        t = threading.Thread(target=r, daemon=True)
        t.start()
        try:
            item = q.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, Exception):
            return None
        return item

    def request(self, payload: dict) -> dict:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
        self.proc.stdin.flush()
        line = self._readline(self.timeout_req)
        if not line:
            raise Fail(f"request timeout/exit: {payload.get('action')}\n{self._read_err()}")
        return json.loads(line)

    def _read_err(self) -> str:
        try:
            if getattr(self, "_errf", None):
                self._errf.flush()
            return self._err_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        except Exception:
            return ""

    def stop(self):
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass
            try:
                pgid = os.getpgid(self.proc.pid)
            except Exception:
                pgid = None
            if pgid:
                _kill_process_group(pgid, term_wait=3.0)
            try:
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if getattr(self, "_errf", None):
            try:
                self._errf.close()
            except Exception:
                pass
            self._errf = None


def check_A_layout(td: Path):
    _p("=== A: 全离线目录布局 + 版本 ===")
    from app import translate_component as tc
    from app import translate_deps as depspec

    must = [
        td / "py" / "python.exe",
        td / "py" / "python312.dll",
        td / "py" / "python312.zip",
        td / "worker_main.py",
        td / "app" / "translate_engine.py",
        td / "app" / "translate_component.py",
        td / "app" / "translate_deps.py",
        td / "app" / "resources.py",
        td / "models" / "zh_en" / "model" / "model.bin",
        td / "models" / "en_zh" / "model" / "model.bin",
        td / "models" / "zh_en" / "sentencepiece.model",
        td / "models" / "en_zh" / "sentencepiece.model",
        td / ".runtime_ready",
        td / "VERSIONS.json",
        td / "libs" / "numpy",
        td / "libs" / "ctranslate2",
        td / "libs" / "sentencepiece",
        td / "libs" / "onnxruntime",
        td / "libs" / "rapidocr_onnxruntime",
        td / "libs" / "flatbuffers",
        td / "libs" / "packaging",
        td / "libs" / "google",
        td / "libs" / "cv2",
        td / "libs" / "sympy",
        td / "libs" / "mpmath",
        td / "libs" / "coloredlogs",
        td / "libs" / "six.py",
    ]
    bad = [str(p) for p in must if not p.exists()]
    if str(td / "libs" / "six.py") in bad and (td / "libs" / "six").exists():
        bad = [b for b in bad if not b.endswith("six.py")]
    if bad:
        raise Fail("A missing:\n  " + "\n  ".join(bad))

    if not tc.embed_python_complete(td / "py"):
        raise Fail("A embed_python_complete failed")
    miss = tc.worker_bundle_complete(td)
    if miss:
        raise Fail("A worker_bundle_complete missing: " + ", ".join(miss))

    verrs = tc.verify_libs_versions(td / "libs", with_ocr=True)
    if verrs:
        raise Fail("A version errors:\n  " + "\n  ".join(verrs))
    ver = json.loads((td / "VERSIONS.json").read_text(encoding="utf-8"))
    exp = depspec.expected_versions(with_ocr=True)
    for k, v in exp.items():
        got = ver.get("packages", {}).get(k)
        if got != v:
            raise Fail(f"A VERSIONS.json {k}: {got} != {v}")
    if exp.get("numpy") != "1.26.4":
        raise Fail("manifest numpy not 1.26.4")
    if exp.get("shapely") == "2.0.4":
        raise Fail("shapely must not be 2.0.4")
    if "six" not in exp or "sympy" not in exp:
        raise Fail("manifest missing six/sympy")
    _p("  A OK layout+versions")


def check_B_translate(td: Path):
    _p("=== B: worker ready + en/zh + 20x ===")
    c = WorkerClient(td)
    try:
        c.start()
        r1 = c.request({"action": "translate", "text": "Hello world", "source": "en", "target": "zh"})
        if not r1.get("ok") or not (r1.get("text") or "").strip():
            raise Fail(f"en->zh fail: {r1}")
        _p(f"  en->zh: {r1['text']!r}")
        r2 = c.request({"action": "translate", "text": "你好世界", "source": "zh", "target": "en"})
        if not r2.get("ok") or not (r2.get("text") or "").strip():
            raise Fail(f"zh->en fail: {r2}")
        _p(f"  zh->en: {r2['text']!r}")
        for i in range(20):
            r = c.request({
                "action": "translate",
                "text": f"Test sentence number {i}",
                "source": "en",
                "target": "zh",
            })
            if not r.get("ok"):
                raise Fail(f"20x fail at {i}: {r}")
        _p("  B OK 20x translate")
    finally:
        c.stop()


def check_C_ocr(td: Path):
    _p("=== C: OCR + translate 同进程 ===")
    if not SCREENSHOT.is_file():
        raise Fail(f"missing screenshot {SCREENSHOT}")
    img = wine_path(SCREENSHOT)
    c = WorkerClient(td, timeout_req=300)
    try:
        c.start()
        r = c.request({"action": "ocr", "path": img})
        if not r.get("ok"):
            raise Fail(f"OCR fail: {r}\n{c._read_err()}")
        text = (r.get("text") or "").strip()
        if not text:
            raise Fail(f"OCR empty text: {r}")
        _p(f"  OCR text preview: {text[:120]!r}")
        r2 = c.request({"action": "translate", "text": text[:200], "source": "auto", "target": "auto"})
        if not r2.get("ok") or not (r2.get("text") or "").strip():
            raise Fail(f"OCR后翻译 fail: {r2}")
        _p(f"  after-OCR translate: {r2['text'][:120]!r}")
        _p("  C OK")
    finally:
        c.stop()


def check_D_slim_download(tmpdir: Path):
    _p("=== D: 精简版 download_all 完整布局 ===")
    from app import translate_component as tc
    from app import translate_deps as depspec

    td = tmpdir / "translate_data"
    if td.exists():
        shutil.rmtree(td)
    os.environ["DEVDEBUG_TRANSLATE_DATA"] = str(td)

    td.mkdir(parents=True)
    tc.mark_installing(td)
    assert not tc.is_ready(), "installing 时 is_ready 应为 False"
    tc.clear_installing(td)
    assert not tc.is_ready(), "半成品无 ready 标记时 is_ready 应为 False"
    _p("  partial -> not ready OK")

    def prog(s, d, t):
        if d and t and t > 0 and d == t:
            _p(f"    {s} done")
        elif d == 0:
            _p(f"    {s}…")

    tc.download_all(progress=prog, with_ocr=True)
    assert tc.runtime_ok(), "runtime_ok should be True"
    assert tc.is_ready(), "is_ready should be True"
    assert (td / depspec.READY_MARKER).is_file()
    assert (td / "py" / "python.exe").is_file()
    assert (td / "worker_main.py").is_file()

    verrs = tc.verify_libs_versions(td / "libs", with_ocr=True)
    if verrs:
        raise Fail("D version: " + "; ".join(verrs))

    c = WorkerClient(td)
    try:
        c.start()
        r = c.request({"action": "translate", "text": "Good morning", "source": "en", "target": "zh"})
        if not r.get("ok"):
            raise Fail(f"D worker translate fail: {r}")
        _p(f"  D translate: {r['text']!r}")
    finally:
        c.stop()
    _p("  D OK")
    os.environ.pop("DEVDEBUG_TRANSLATE_DATA", None)


def check_E_gui(app_dir: Path):
    """Wine+xvfb 启动 exe ≥10s；独立进程组，finally 整组 TERM→KILL，无残留。"""
    _p("=== E: Wine+xvfb 启动 exe ≥10s（进程组清理）===")
    exe = app_dir / _EXE_NAME
    if not exe.is_file():
        raise Fail(f"missing exe {exe}")

    # 启动前：若已有本项目残留，先失败（避免假绿叠层）
    pre = _list_project_gui_pids()
    if pre:
        detail = "\n".join(f"  pid={p} {c}" for p, c in pre)
        raise Fail(f"E 启动前已有本项目残留（请先清理，勿动系统 :99）:\n{detail}")

    log = app_dir / "accept_gui.log"
    env = os.environ.copy()
    env["WINEDEBUG"] = "-all"
    # 固定几何，便于残留识别；-a 自动选 display
    cmd = [
        "xvfb-run",
        "-a",
        "-s", f"-screen 0 {_ACCEPT_XVFB_GEOM}x24 -nolisten tcp",
        WINE,
        str(exe.resolve()),
    ]
    proc = None
    pgid = None
    try:
        with open(log, "w", encoding="utf-8", errors="replace") as errf:
            proc = subprocess.Popen(
                cmd,
                stdout=errf,
                stderr=subprocess.STDOUT,
                cwd=str(app_dir),
                env=env,
                start_new_session=True,  # 独立 session/进程组
            )
            try:
                pgid = os.getpgid(proc.pid)
            except Exception:
                pgid = proc.pid
            _p(f"  started pid={proc.pid} pgid={pgid}")
            time.sleep(10)
            rc = proc.poll()
            if rc is not None:
                errf.flush()
                tail = log.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise Fail(f"exe 在 10s 内退出 code={rc}\n{tail}")
            _p("  stayed up 10s")
    finally:
        # 成功与异常都清理整个进程组
        if pgid is not None:
            _kill_process_group(pgid, term_wait=5.0)
        if proc is not None:
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # 再扫一遍本项目路径的孤儿（wine 有时会脱离）
        time.sleep(0.5)
        for pid, cmd in _list_project_gui_pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        time.sleep(1.0)
        for pid, cmd in _list_project_gui_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        time.sleep(0.5)
        _assert_no_project_gui_residuals("E finally")
        # 系统 :99 必须仍在（若本机有的话不强制；但绝不能误杀——这里只检查我们没留下 1280x800）
        _p("  E cleanup OK, no project residuals")

    _p("  E OK")


def _zip_test(path: Path) -> None:
    """CRC/testzip：BadZipFile 或坏 member 均失败。"""
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
    except zipfile.BadZipFile as e:
        raise Fail(f"BadZipFile: {path.name}: {e}") from e
    except Exception as e:
        raise Fail(f"zip open/test failed: {path.name}: {e}") from e
    if bad is not None:
        raise Fail(f"zip corrupt member in {path.name}: {bad}")


def check_F_zips():
    _p("=== F: ZIP CRC + VERSIONS manifest ===")
    from app import translate_deps as depspec

    full = ROOT / "开发调试-全离线版-win64.zip"
    slim = ROOT / "开发调试-精简版-win64.zip"
    if not full.is_file() or not slim.is_file():
        raise Fail("zip missing")

    for p in (slim, full):
        _zip_test(p)
        _p(f"  testzip OK: {p.name}")

    with zipfile.ZipFile(full) as zf:
        names = set(zf.namelist())
        must = [
            "开发调试/translate_data/py/python.exe",
            "开发调试/translate_data/worker_main.py",
            "开发调试/translate_data/app/translate_engine.py",
            "开发调试/translate_data/app/translate_deps.py",
            "开发调试/translate_data/.runtime_ready",
            "开发调试/translate_data/VERSIONS.json",
        ]
        for m in must:
            if m not in names:
                raise Fail(f"full zip missing {m}")
        # 读包内 VERSIONS.json
        raw = zf.read("开发调试/translate_data/VERSIONS.json")
        ver = json.loads(raw.decode("utf-8"))

    mv = str(ver.get("manifest_version", ""))
    if mv != str(depspec.MANIFEST_VERSION):
        raise Fail(
            f"全离线 ZIP manifest_version={mv!r} != 当前 {depspec.MANIFEST_VERSION!r}（疑旧包）"
        )
    exp = depspec.expected_versions(with_ocr=True)
    pkgs = ver.get("packages") or {}
    for k, v in exp.items():
        got = pkgs.get(k)
        if got != v:
            raise Fail(f"ZIP VERSIONS.json {k}: {got!r} != expected {v!r}")
    # 关键钉死项
    for k in ("shapely", "six", "sympy", "mpmath", "coloredlogs", "numpy"):
        if k not in exp:
            raise Fail(f"manifest 缺关键包 {k}")
    if exp.get("shapely") != "2.0.3":
        raise Fail(f"shapely 应为 2.0.3，got {exp.get('shapely')}")
    if exp.get("shapely") == "2.0.4":
        raise Fail("shapely 不得为 2.0.4")

    for p in (full, slim):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        st = p.stat()
        _p(
            f"  {p.name}: {st.st_size / 1048576:.1f} MB  "
            f"mtime={time.ctime(st.st_mtime)}  sha256={h}"
        )
    _p("  F OK")


def check_unit_path_selection():
    _p("=== unit: path selection / external_python ===")
    from app import translate_component as tc
    from app import translate_deps as depspec

    prev = os.environ.get("DEVDEBUG_TRANSLATE_DATA")
    try:
        with tempfile.TemporaryDirectory(prefix="td_path_") as td:
            root = Path(td)
            os.environ["DEVDEBUG_TRANSLATE_DATA"] = str(root)
            # 无 py/python.exe → None
            got = tc.external_python()
            if got is not None:
                raise Fail(f"无 python.exe 时期望 None，got {got}")
            _p("  no python.exe -> None OK")

            # 创建后返回预期路径
            py_dir = root / "py"
            py_dir.mkdir(parents=True)
            exe = py_dir / "python.exe"
            exe.write_bytes(b"MZ fake")
            got2 = tc.external_python()
            if got2 is None or got2.resolve() != exe.resolve():
                raise Fail(f"有 python.exe 时期望 {exe}，got {got2}")
            _p(f"  with python.exe -> {got2} OK")
    finally:
        if prev is None:
            os.environ.pop("DEVDEBUG_TRANSLATE_DATA", None)
        else:
            os.environ["DEVDEBUG_TRANSLATE_DATA"] = prev

    assert depspec.expected_versions()["numpy"] == "1.26.4"
    assert depspec.expected_versions()["onnxruntime"] == "1.17.3"
    assert depspec.expected_versions(with_ocr=True)["shapely"] == "2.0.3"
    assert depspec.MANIFEST_VERSION == "2"
    _p("  unit OK")


def check_wine11():
    _p("=== wine11 验收环境 ===")
    out = subprocess.check_output([WINE, "--version"], text=True, stderr=subprocess.STDOUT)
    _p(f"  WINE={WINE}")
    _p(f"  version: {out.strip()}")
    if not out.strip().startswith("wine-11"):
        raise Fail(f"需要 Wine 11.x，当前: {out.strip()}（Wine 9 仅作环境差异参考）")
    _p("  wine11 OK")


def check_internal_no_native_libs():
    """_internal 不得含翻译/OCR 原生库（扫描证据）。"""
    _p("=== _internal 无翻译原生库 ===")
    internal = APP / "_internal"
    if not internal.is_dir():
        raise Fail(f"missing {internal}")
    forbidden = (
        "ctranslate2",
        "sentencepiece",
        "onnxruntime",
        "rapidocr",
        "numpy",
    )
    hits: list[str] = []
    for p in internal.rglob("*"):
        low = p.name.lower()
        rel = str(p.relative_to(internal))
        for f in forbidden:
            if f in low or f in rel.lower():
                # PIL/_imaging 等允许；只拦翻译相关
                if f == "numpy" and "numpy" not in rel.lower().split("/")[0:2]:
                    # 更严：路径组件含 numpy
                    parts = [x.lower() for x in p.parts]
                    if not any("numpy" in x for x in parts):
                        continue
                hits.append(rel)
                break
    # 细化：ctranslate2/sentencepiece/onnxruntime/rapidocr 任一路径命中即失败
    bad = []
    for p in internal.rglob("*"):
        parts_l = "/".join(p.relative_to(internal).parts).lower()
        for key in ("ctranslate2", "sentencepiece", "onnxruntime", "rapidocr_onnxruntime", "rapidocr"):
            if key in parts_l:
                bad.append(parts_l)
        # numpy 包目录或 .pyd
        if parts_l == "numpy" or parts_l.startswith("numpy/") or "/numpy/" in f"/{parts_l}":
            bad.append(parts_l)
        if "multiarray" in parts_l and "numpy" in parts_l:
            bad.append(parts_l)
    if bad:
        raise Fail("_internal 含翻译原生库:\n  " + "\n  ".join(sorted(set(bad))[:40]))
    _p("  scan OK: no ctranslate2/sentencepiece/onnxruntime/rapidocr/numpy in _internal")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--pack-only", action="store_true")
    ap.add_argument("--skip-gui", action="store_true")
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()

    failures = []

    def run(name, fn):
        try:
            fn()
        except Exception as e:
            failures.append((name, e))
            _p(f"FAIL {name}: {e}")

    run("wine11", check_wine11)
    run("unit", check_unit_path_selection)

    if not TD.is_dir() or not (TD / "py" / "python.exe").is_file():
        _p("pack offline runtime first…")
        if not APP.is_dir():
            raise SystemExit("dist/开发调试 missing — run build first")
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "pack_offline_runtime.py"), str(APP),
             "--models", str(ROOT / "models")],
            cwd=str(ROOT),
        )

    run("internal", check_internal_no_native_libs)
    run("A", lambda: check_A_layout(TD))
    run("B", lambda: check_B_translate(TD))
    run("C", lambda: check_C_ocr(TD))

    if not args.quick and not args.pack_only and not args.skip_download:
        tmp = ROOT / ".cache" / "accept_slim"
        tmp.mkdir(parents=True, exist_ok=True)
        run("D", lambda: check_D_slim_download(tmp))

    if not args.quick and not args.pack_only and not args.skip_gui:
        run("E", lambda: check_E_gui(APP))

    if (ROOT / "开发调试-全离线版-win64.zip").is_file():
        run("F", check_F_zips)
    else:
        _p("skip F (zip not built yet)")

    # 全局残留扫描（验收结束）
    try:
        _assert_no_project_gui_residuals("session end")
        _p("=== residual scan: clean ===")
    except Fail as e:
        failures.append(("residual", e))
        _p(f"FAIL residual: {e}")

    if failures:
        _p("\n===== FAILURES =====")
        for n, e in failures:
            _p(f"  [{n}] {e}")
        sys.exit(1)
    _p("\n===== ALL ACCEPTANCE CHECKS PASSED =====")


if __name__ == "__main__":
    main()
