#!/usr/bin/env bash
# 在 Linux + Wine11(Windows Python) 上打 win64 精简版 / 全离线版 zip
# 全离线 libs 来自钉死 wheel（app/translate_deps.py），不拷 Wine site-packages。
#
# 用法:
#   bash scripts/build_win64.sh              # 完整 PyInstaller + pack + zip
#   bash scripts/build_win64.sh --verify-zips  # 仅校验已有两包 CRC/SHA（不跑 PyInstaller）
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
export WINEDEBUG=-all
export PATH="${HOME}/wine11/bin:${PATH}"
WINPY='C:\python\python.exe'

verify_existing_zips() {
  python3 - <<'PY'
import hashlib, json, sys, time, zipfile
from pathlib import Path

sys.path.insert(0, ".")
from app import translate_deps as depspec

ROOT = Path(".").resolve()
slim = ROOT / "开发调试-精简版-win64.zip"
full = ROOT / "开发调试-全离线版-win64.zip"
for p in (slim, full):
    if not p.is_file():
        raise SystemExit(f"missing {p}")
    try:
        with zipfile.ZipFile(p) as zf:
            bad = zf.testzip()
    except zipfile.BadZipFile as e:
        raise SystemExit(f"BadZipFile {p.name}: {e}")
    if bad is not None:
        raise SystemExit(f"corrupt member in {p.name}: {bad}")
    print(f"testzip OK: {p.name}")

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
            raise SystemExit(f"full zip missing {m}")
    ver = json.loads(zf.read("开发调试/translate_data/VERSIONS.json").decode("utf-8"))

if str(ver.get("manifest_version")) != str(depspec.MANIFEST_VERSION):
    raise SystemExit(
        f"manifest_version {ver.get('manifest_version')!r} != {depspec.MANIFEST_VERSION!r}"
    )
exp = depspec.expected_versions(with_ocr=True)
pkgs = ver.get("packages") or {}
for k, v in exp.items():
    if pkgs.get(k) != v:
        raise SystemExit(f"VERSIONS.json {k}: {pkgs.get(k)!r} != {v!r}")
for k in ("shapely", "six", "sympy", "mpmath", "coloredlogs"):
    if k not in exp:
        raise SystemExit(f"manifest missing {k}")
if exp.get("shapely") != "2.0.3":
    raise SystemExit(f"shapely must be 2.0.3, got {exp.get('shapely')}")

print("--- zip summary ---")
for p in (slim, full):
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    st = p.stat()
    print(
        f"{p.name}: size={st.st_size} ({st.st_size/1048576:.1f} MB) "
        f"mtime={time.ctime(st.st_mtime)} sha256={h}"
    )
print("verify-zips OK")
PY
}

if [[ "${1:-}" == "--verify-zips" ]]; then
  echo "[verify-zips] 校验现有精简版/全离线版 ZIP…"
  verify_existing_zips
  exit 0
fi

echo "[1/5] PyInstaller (slim, 无翻译原生库)…"
rm -rf build dist
wine "$WINPY" -m PyInstaller --noconfirm --clean devdebug.spec

APP="$ROOT/dist/开发调试"
test -f "$APP/开发调试.exe"

echo "[2/5] trim Qt…"
python3 scripts/trim_qt.py "$APP" || true

echo "[3/5] 精简版 zip…"
python3 - <<'PY'
import hashlib, time, zipfile
from pathlib import Path
root = Path("dist/开发调试")
out = Path("开发调试-精简版-win64.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in root.rglob("*"):
        if p.is_file() and "translate_data" not in p.parts:
            z.write(p, Path("开发调试") / p.relative_to(root))
# 生成后立即 CRC
with zipfile.ZipFile(out) as zf:
    bad = zf.testzip()
    if bad is not None:
        raise SystemExit(f"slim zip corrupt: {bad}")
h = hashlib.sha256(out.read_bytes()).hexdigest()
st = out.stat()
print(f"{out.name}: {st.st_size/1048576:.1f} MB mtime={time.ctime(st.st_mtime)} sha256={h} testzip=OK")
PY

echo "[4/5] 装配全离线 translate_data（钉死 wheel）…"
python3 scripts/pack_offline_runtime.py "$APP" --models "$ROOT/models"

echo "[5/5] 全离线 zip…"
python3 - <<'PY'
import hashlib, json, time, zipfile
from pathlib import Path
import sys
sys.path.insert(0, ".")
from app import translate_deps as depspec

root = Path("dist/开发调试")
out = Path("开发调试-全离线版-win64.zip")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in root.rglob("*"):
        if p.is_file():
            z.write(p, Path("开发调试") / p.relative_to(root))

with zipfile.ZipFile(out) as zf:
    bad = zf.testzip()
    if bad is not None:
        raise SystemExit(f"full zip corrupt: {bad}")
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
    assert m in names, m
with zipfile.ZipFile(out) as zf:
    ver = json.loads(zf.read("开发调试/translate_data/VERSIONS.json").decode("utf-8"))
assert str(ver.get("manifest_version")) == str(depspec.MANIFEST_VERSION)
exp = depspec.expected_versions(with_ocr=True)
for k, v in exp.items():
    assert (ver.get("packages") or {}).get(k) == v, (k, ver.get("packages", {}).get(k), v)

h = hashlib.sha256(out.read_bytes()).hexdigest()
st = out.stat()
print(f"{out.name}: {st.st_size/1048576:.1f} MB mtime={time.ctime(st.st_mtime)} sha256={h} testzip=OK")
print("exe:", (root / "开发调试.exe").stat().st_size)
td = root / "translate_data"
print("translate_data:", sum(p.stat().st_size for p in td.rglob("*") if p.is_file()) // 1048576, "MB")
print("zip content + VERSIONS checks OK")
PY

echo "=== 双包 size/mtime/SHA256 ==="
verify_existing_zips

echo "完成:"
ls -lh 开发调试-*-win64.zip
