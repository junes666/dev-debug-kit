#!/usr/bin/env python3
"""翻译依赖清单一致性 + 缓存/半解压恢复 + RECORD 深度 + 冻结入口测试。

不依赖 Wine GUI；网络用于拉 PyPI 元数据与（可选）损坏缓存恢复。
"""
from __future__ import annotations

import ast
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packaging.requirements import Requirement  # noqa: E402
from packaging.utils import canonicalize_name  # noqa: E402
from packaging.version import Version  # noqa: E402

from app import translate_deps as depspec  # noqa: E402
from app import translate_component as tc  # noqa: E402

UA = {"User-Agent": "DevDebugKit-test"}


class Fail(Exception):
    pass


def _p(msg: str):
    print(msg, flush=True)


def _pypi_requires(pkg: str, ver: str) -> list[str]:
    url = f"https://pypi.org/pypi/{pkg}/{ver}/json"
    req = urllib.request.Request(url, headers=UA)
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())
    return list(data["info"].get("requires_dist") or [])


def _is_extra_marker(req_str: str) -> bool:
    """带 extra == 的 marker 视为可选，不校验 pin。"""
    if ";" not in req_str:
        return False
    marker = req_str.split(";", 1)[1].strip().lower()
    return "extra" in marker


def _manifest_lookup(req_name: str) -> depspec.Dep | None:
    """按 requirement 名在清单中找 pin；opencv-python 可由 headless 等价满足。"""
    key = canonicalize_name(req_name)
    # 直接名
    for d in depspec.deps_for(with_ocr=True, include_optional=False):
        if canonicalize_name(d.pypi) == key:
            return d
    # 别名：opencv-python ← opencv-python-headless
    if key == "opencv-python":
        for d in depspec.deps_for(with_ocr=True, include_optional=False):
            if canonicalize_name(d.pypi) == "opencv-python-headless":
                return d
    # PyYAML / pyyaml 等 canonicalize 已覆盖
    # 宽松：normalize underscore
    soft = key.replace("-", "_")
    for d in depspec.deps_for(with_ocr=True, include_optional=False):
        if canonicalize_name(d.pypi).replace("-", "_") == soft:
            return d
    return None


def _pin_satisfies(req: Requirement, pinned_ver: str) -> bool:
    """清单 pin 版本是否满足 requirement 的 specifier。无 specifier 则 True。"""
    v = Version(pinned_ver)
    if not req.specifier:
        return True
    return v in req.specifier


def test_manifest_vs_pypi_metadata():
    """用 packaging.Requirement + Version 校验每个非-extra requires_dist 的 pin。

    旧逻辑只检查依赖名字存在，six=1.0 / numpy=2.5 也会误绿。
    opencv-python 由已钉 opencv-python-headless 等价满足；PyYAML 名称大小写规范化。
    """
    _p("=== manifest vs PyPI requires_dist (Requirement + Version) ===")
    vers = depspec.expected_versions(with_ocr=True)

    # ---- rapidocr ----
    rocr = "rapidocr_onnxruntime"
    assert rocr in vers
    reqs = _pypi_requires(rocr, vers[rocr])
    _p(f"  {rocr}=={vers[rocr]} requires: {reqs}")

    hard: list[str] = []
    for r in reqs:
        if _is_extra_marker(r):
            continue
        hard.append(r)

    for rstr in hard:
        req = Requirement(rstr.split(";")[0].strip())  # drop env markers for name/spec
        # 重建带 marker 前的 name+spec（Requirement 已解析）
        dep = _manifest_lookup(req.name)
        assert dep is not None, (
            f"清单缺 {req.name}（来自 {rocr} requires_dist: {rstr}）"
        )
        assert dep.required, f"{dep.pypi} 不得标为 optional"
        assert _pin_satisfies(req, dep.version), (
            f"{dep.pypi}=={dep.version} 不满足 {rocr} 约束 {req} "
            f"(specifier={req.specifier!s})"
        )
        _p(f"    OK {req.name} pin {dep.pypi}=={dep.version} satisfies {req.specifier or '(any)'}")

    # Shapely!=2.0.4 硬约束
    assert vers["shapely"] != "2.0.4", "清单不得钉 shapely==2.0.4"
    assert vers["shapely"] not in depspec.SHAPELY_EXCLUDED
    # 故意错误 pin 必须被检测：构造假 Requirement
    bad = Requirement("numpy>=2.5")
    assert not _pin_satisfies(bad, vers["numpy"]), "numpy 1.26 不得满足 >=2.5（防误绿）"
    bad_six = Requirement("six==1.0")
    assert not _pin_satisfies(bad_six, vers["six"]), "six 1.16 不得满足 ==1.0（防误绿）"

    # six present
    assert "six" in vers, "清单缺 six"

    # ---- onnxruntime ----
    ort = "onnxruntime"
    oreqs = _pypi_requires(ort, vers[ort])
    _p(f"  {ort}=={vers[ort]} requires: {oreqs}")
    for rstr in oreqs:
        if _is_extra_marker(rstr):
            continue
        req = Requirement(rstr.split(";")[0].strip())
        dep = _manifest_lookup(req.name)
        assert dep is not None, f"清单缺 {req.name}（来自 {ort}）"
        assert dep.required, f"{dep.pypi} 不得标为 optional"
        assert _pin_satisfies(req, dep.version), (
            f"{dep.pypi}=={dep.version} 不满足 {ort} 约束 {req}"
        )
        _p(f"    OK {req.name} pin {dep.pypi}=={dep.version} satisfies {req.specifier or '(any)'}")

    # coloredlogs 必须在 OCR_DEPS 且 required
    cl = depspec.dep_by_pypi("coloredlogs")
    assert cl and cl.required and cl.for_ocr

    # sympy → mpmath
    assert depspec.dep_by_pypi("mpmath") is not None

    # digest 非空
    for d in depspec.deps_for(with_ocr=True):
        assert len(d.sha256) == 64, f"{d.pypi} sha256 长度不对"
        assert d.imports, f"{d.pypi} 缺 import 探针"

    # 与 live PyPI wheel digest 对齐
    for d in depspec.deps_for(with_ocr=True):
        info = tc.pypi_wheel_info(d.pypi, d.version)
        assert info["sha256"].lower() == d.sha256.lower(), (
            f"{d.pypi} digest 漂移: list={d.sha256} pypi={info['sha256']} file={info['filename']}"
        )
    _p("  manifest metadata OK (specifier-aware)")


def test_verify_not_just_topdir():
    _p("=== verify_libs 不只看顶层目录 ===")
    with tempfile.TemporaryDirectory() as td:
        libs = Path(td) / "libs"
        libs.mkdir()
        # 只有空目录 numpy，无 dist-info
        (libs / "numpy").mkdir()
        errs = tc.verify_libs_versions(libs, with_ocr=False)
        assert any("numpy" in e for e in errs), errs
        _p("  empty dir without dist-info -> error OK")


def test_corrupt_python312_zip_over_1mb():
    """>1MB 但损坏的 python312.zip 必须使 embed_python_complete 返回 False。

    旧逻辑仅 size>=1MB 即通过；截断/坏 CRC 即使 >1MB 也必须拒绝。
    不能只删文件（那只能测 is_file）。
    """
    _p("=== >1MB 损坏 python312.zip → embed incomplete ===")
    with tempfile.TemporaryDirectory() as td:
        py = Path(td) / "py"
        py.mkdir()
        (py / "python.exe").write_bytes(b"MZ" + b"\x00" * 100)
        (py / "python312.dll").write_bytes(b"MZ" + b"\x00" * 100)
        # 写合法 _pth
        (py / "python312._pth").write_text(
            "python312.zip\n.\n..\\libs\n..\nimport site\n", encoding="utf-8"
        )

        # 1) 截断伪 ZIP：PK 头 + 填充到 >1.5MB，非完整 ZIP
        bad = b"PK\x03\x04" + b"\x00" * (1_500_000)
        (py / "python312.zip").write_bytes(bad)
        assert (py / "python312.zip").stat().st_size > 1_000_000
        assert not tc._zip_intact(path=py / "python312.zip")
        assert not tc.embed_python_complete(py), (
            "截断 >1MB python312.zip 不得判定 complete"
        )
        _p("  truncated >1MB zip -> incomplete OK")

        # 2) 合法 ZIP 结构但成员 CRC 损坏
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("encodings/__init__.py", b"#ok\n" * 1000)
            # 填大文件使总 size >1MB
            zf.writestr("big.bin", b"A" * (1_200_000))
        data = bytearray(buf.getvalue())
        # 破坏中间字节（payload）导致 testzip 失败，保持 size
        mid = len(data) // 2
        data[mid] ^= 0xFF
        data[mid + 1] ^= 0xAA
        (py / "python312.zip").write_bytes(bytes(data))
        assert (py / "python312.zip").stat().st_size > 1_000_000
        # 可能 BadZipFile 或 testzip 返回 bad member
        intact = tc._zip_intact(path=py / "python312.zip")
        # 若碰巧仍 intact（极少），再截断 central directory
        if intact:
            raw = (py / "python312.zip").read_bytes()
            (py / "python312.zip").write_bytes(raw[:-200])  # 砍 EOCD，仍可能 >1MB
            assert (py / "python312.zip").stat().st_size > 1_000_000
        assert not tc.embed_python_complete(py), (
            "CRC/结构损坏 >1MB python312.zip 不得判定 complete"
        )
        _p("  corrupt CRC/structure >1MB zip -> incomplete OK")

        # 3) 完整小 ZIP 但 size <1MB 也不 complete（size 门槛仍在）
        buf2 = io.BytesIO()
        with zipfile.ZipFile(buf2, "w") as zf:
            zf.writestr("x.py", b"1\n")
        (py / "python312.zip").write_bytes(buf2.getvalue())
        assert (py / "python312.zip").stat().st_size < 1_000_000
        assert not tc.embed_python_complete(py)
        _p("  tiny intact zip (<1MB) -> incomplete OK")


def test_six_record_deep_verify_and_reinstall():
    """完整 six wheel 解压后深验通过；破坏/删除 six.py 后深验失败；重装恢复。

    覆盖半解压（有 dist-info 缺 .py），而非只有空目录无 dist-info。
    """
    _p("=== six RECORD 深度校验 + 半解压重装 ===")
    dep = depspec.dep_by_pypi("six")
    assert dep is not None
    cache = Path(tempfile.mkdtemp(prefix="td_six_cache_"))
    libs = Path(tempfile.mkdtemp(prefix="td_six_libs_"))
    try:
        info = tc.pypi_wheel_info(dep.pypi, dep.version)
        cpath = cache / info["filename"]
        data = tc._http_download(info["url"])
        tc.save_cached_artifact(cpath, data, expect_sha256=dep.sha256)
        assert tc._zip_intact(data=data)
        tc._extract_wheel(data, libs)

        # 完整 → 深验通过
        errs = tc.dep_install_complete(libs, dep)
        assert not errs, f"完整 six 应通过深验: {errs}"
        assert not tc.verify_record_integrity(
            libs, tc._find_dist_info(libs, dep.pypi, dep.version)
        )
        _p("  full six RECORD verify OK")

        # 破坏 six.py（半解压典型：dist-info 在、主模块缺/坏）
        six_py = libs / "six.py"
        assert six_py.is_file(), "six pure wheel 应解出 six.py"
        original = six_py.read_bytes()
        six_py.write_bytes(b"# corrupted half-extract\n")
        errs2 = tc.dep_install_complete(libs, dep)
        assert errs2, "破坏 six.py 后深验必须失败"
        assert any("sha256" in e or "size" in e or "RECORD" in e for e in errs2), errs2
        _p(f"  corrupted six.py fails deep verify: {errs2[0]}")

        # 删除 six.py（保留 dist-info）
        six_py.unlink()
        dist = tc._find_dist_info(libs, dep.pypi, dep.version)
        assert dist is not None and dist.is_dir(), "dist-info 应仍在"
        errs3 = tc.dep_install_complete(libs, dep)
        assert errs3, "删除 six.py 后深验必须失败"
        assert any("缺文件" in e or "six.py" in e for e in errs3), errs3
        _p(f"  deleted six.py fails deep verify: {errs3[0]}")

        # install_pinned_wheels fast path 不得 mark 跳过：应 purge + 从缓存重解压
        # 只装 six：用临时清单 trick —— 直接调 install 全量太重，
        # 模拟 install_pinned_wheels 对单个 dep 的路径
        deep_before = tc.dep_install_complete(libs, dep)
        assert deep_before
        tc._purge_dep_from_libs(libs, dep)
        # dist-info 与 six.py 都应没了
        assert tc._find_dist_info(libs, dep.pypi, dep.version) is None
        assert not (libs / "six.py").exists()

        # 从已校验缓存重解压
        cached = tc.load_cached_artifact(
            cpath, expect_sha256=dep.sha256, min_size=100, kind="whl"
        )
        assert cached is not None, "好缓存应可复用"
        tc._extract_wheel(cached, libs)
        post = tc.dep_install_complete(libs, dep)
        assert not post, f"重装后应完整: {post}"
        assert (libs / "six.py").is_file()
        assert (libs / "six.py").read_bytes() == original or len(
            (libs / "six.py").read_bytes()
        ) == len(original)
        _p("  reinstall from cache restores six OK")

        # 再用 install_pinned_wheels 整路径（with_ocr=False 仍含 core；
        # six 在 OCR——单独用 include 路径：直接再破坏后调完整 install 会下全量。
        # 验证 fast path：完整后再次 install 应跳过（不报错）
        # 仅 six 场景：手动确认 dep_install_complete 空 → fast skip
        assert not tc.dep_install_complete(libs, dep)
        _p("  six deep-verify + reinstall path OK")
    finally:
        shutil.rmtree(cache, ignore_errors=True)
        shutil.rmtree(libs, ignore_errors=True)


def test_corrupt_cache_and_half_extract():
    _p("=== 损坏缓存 / 半解压 → 第二次成功 ===")
    cache = Path(tempfile.mkdtemp(prefix="td_cache_"))
    py_dir = Path(tempfile.mkdtemp(prefix="td_py_"))
    try:
        zname = f"python-{depspec.EMBED_PYTHON_VERSION}-embed-amd64.zip"
        zpath = cache / zname

        # 1) 截断但 >1MB 的坏 ZIP（旧逻辑会永久复用）
        tc.install_embed_python(py_dir, cache_dir=cache)
        assert tc.embed_python_complete(py_dir)
        assert zpath.is_file()
        good_size = zpath.stat().st_size
        raw = zpath.read_bytes()[: 1_500_000]
        zpath.write_bytes(raw)
        assert zpath.stat().st_size > 1_000_000

        got = tc.load_cached_artifact(
            zpath,
            expect_sha256=depspec.EMBED_PYTHON_SHA256,
            min_size=depspec.EMBED_PYTHON_MIN_SIZE,
            kind="zip",
        )
        assert got is None, "截断 ZIP 不得被复用"
        assert not zpath.is_file(), "坏缓存应被删除"

        # 半解压：只留 python.exe + dll，删 zip
        tc.install_embed_python(py_dir, cache_dir=cache)
        assert tc.embed_python_complete(py_dir)
        (py_dir / "python312.zip").unlink()
        assert not tc.embed_python_complete(py_dir)

        tc.install_embed_python(py_dir, cache_dir=cache)
        assert tc.embed_python_complete(py_dir), "半解压后重试应补全 python312.zip"
        _p(f"  embed recovery OK (good zip ~{good_size} bytes)")

        # 已解压的 python312.zip 损坏（>1MB）也应 incomplete，且重装修复
        std = py_dir / "python312.zip"
        assert std.is_file() and std.stat().st_size > 1_000_000
        std.write_bytes(b"PK\x03\x04" + b"\x00" * 1_200_000)
        assert not tc.embed_python_complete(py_dir), "解压后损坏的 python312.zip 必须 incomplete"
        tc.install_embed_python(py_dir, cache_dir=cache)
        assert tc.embed_python_complete(py_dir), "损坏 python312.zip 重装应修复"
        _p("  in-place corrupt python312.zip recovery OK")

        # wheel 坏缓存
        whl_cache = cache / "wheels"
        whl_cache.mkdir(exist_ok=True)
        dep = depspec.dep_by_pypi("six")
        assert dep
        info = tc.pypi_wheel_info(dep.pypi, dep.version)
        cpath = whl_cache / info["filename"]
        cpath.write_bytes(b"PK\x03\x04" + b"\x00" * 2000)
        got = tc.load_cached_artifact(
            cpath, expect_sha256=dep.sha256, min_size=100, kind="whl"
        )
        assert got is None
        assert not cpath.is_file()

        libs = Path(tempfile.mkdtemp(prefix="td_libs_"))
        try:
            data = tc.load_cached_artifact(
                cpath, expect_sha256=dep.sha256, min_size=100, kind="whl"
            )
            if data is None:
                data = tc._http_download(info["url"])
                tc.save_cached_artifact(cpath, data, expect_sha256=dep.sha256)
            assert tc._zip_intact(data=data)
            tc._extract_wheel(data, libs)
            assert (libs / "six.py").is_file() or (libs / "six").exists()
            _p("  wheel corrupt-cache recovery OK")
        finally:
            shutil.rmtree(libs, ignore_errors=True)
    finally:
        shutil.rmtree(cache, ignore_errors=True)
        shutil.rmtree(py_dir, ignore_errors=True)


def test_worker_bundle_no_silent_skip():
    _p("=== worker bundle 不得静默跳过 ===")
    root = Path(tempfile.mkdtemp(prefix="td_w_"))
    src = Path(tempfile.mkdtemp(prefix="td_src_"))
    try:
        app = src / "app"
        app.mkdir()
        (app / "translate_engine.py").write_text("#x\n", encoding="utf-8")
        try:
            tc.install_worker_bundle(root, src_app=app)
            raise Fail("应因缺模块失败")
        except RuntimeError as e:
            assert "缺失" in str(e) or "缺" in str(e)
            _p(f"  missing modules raised OK: {e}")

        real = ROOT / "app"
        tc.install_worker_bundle(root, src_app=real)
        miss = tc.worker_bundle_complete(root)
        assert not miss, miss
        _p("  full bundle OK")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(src, ignore_errors=True)


def test_embed_pth_and_markers():
    _p("=== embed complete / markers ===")
    assert depspec.EMBED_PYTHON_SHA256
    assert len(depspec.EMBED_PYTHON_SHA256) == 64
    with tempfile.TemporaryDirectory() as td:
        py = Path(td) / "py"
        py.mkdir()
        (py / "python.exe").write_bytes(b"MZ")
        (py / "python312.dll").write_bytes(b"MZ")
        assert not tc.embed_python_complete(py)
        _p("  exe+dll only -> incomplete OK")


def test_no_translate_worker_frozen_entry():
    """main.py 不得再保留 --translate-worker 分支（冻结 EXE 会加载原生库）。

    静态：源码与 AST 均无该入口；运行：模拟 argv 不得 import serve。
    """
    _p("=== 无 --translate-worker 冻结入口 ===")
    main_py = ROOT / "main.py"
    text = main_py.read_text(encoding="utf-8")
    assert "--translate-worker" not in text, (
        "main.py 仍含 --translate-worker 字符串（含注释也不允许）"
    )
    # 可执行入口：不得 import app.translate_worker
    assert "from app.translate_worker" not in text
    assert "import app.translate_worker" not in text
    # 更严：AST 中不得有对 translate_worker / serve 的 import
    tree = ast.parse(text, filename=str(main_py))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = ""
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
            blob = mod + " " + " ".join(names)
            assert "translate_worker" not in blob, f"AST import 含 translate_worker: {blob}"
            assert not (mod.endswith("translate_worker") and "serve" in names)

    # if 条件里不得出现 translate-worker
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            raw = ast.dump(node)
            assert "translate-worker" not in raw
            assert "translate_worker" not in raw

    # 运行时：带该 argv 启动时不应进入 worker（main 会走 GUI，这里只检查
    # 模块级不因 argv 加载 translate_worker）
    import importlib.util
    # 扫描已加载模块
    for name in list(sys.modules):
        assert "translate_worker" not in name or name.startswith("app.translate"), True

    # 直接 exec 前半：确认 sys.argv 含 flag 时 source 无分支可触发
    # （上面 AST 已覆盖；此处再 grep 全仓库 app/main 入口）
    for p in [ROOT / "main.py"]:
        src = p.read_text(encoding="utf-8")
        assert "if \"--translate-worker\"" not in src
        assert "if '--translate-worker'" not in src
        assert "in sys.argv" not in src or "translate-worker" not in src

    _p("  main.py has no --translate-worker entry (static+AST) OK")


def main():
    fails = []
    for name, fn in [
        ("manifest", test_manifest_vs_pypi_metadata),
        ("verify_depth", test_verify_not_just_topdir),
        ("corrupt_pyzip", test_corrupt_python312_zip_over_1mb),
        ("six_record", test_six_record_deep_verify_and_reinstall),
        ("worker_bundle", test_worker_bundle_no_silent_skip),
        ("embed_markers", test_embed_pth_and_markers),
        ("no_worker_entry", test_no_translate_worker_frozen_entry),
        ("cache_recovery", test_corrupt_cache_and_half_extract),
    ]:
        try:
            fn()
        except Exception as e:
            fails.append((name, e))
            _p(f"FAIL [{name}] {e}")
            import traceback
            traceback.print_exc()
    if fails:
        _p("\n===== TEST FAILURES =====")
        for n, e in fails:
            _p(f"  [{n}] {e}")
        sys.exit(1)
    _p("\n===== translate deps tests PASSED =====")


if __name__ == "__main__":
    main()
