"""编码/解码 & 哈希 & 对称加解密 & JWT & 时间戳 模块（全部离线）。"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QComboBox, QLineEdit,
    QSplitter, QGridLayout, QLabel,
)

from app import widgets

try:
    from Crypto.Cipher import AES, DES, DES3, ARC4
    from Crypto.Util.Padding import pad, unpad
    _CRYPTO = True
except Exception:  # noqa: BLE001
    _CRYPTO = False


class CodecTool(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._tab_codec(), "编码 / 解码")
        tabs.addTab(self._tab_hash(), "哈希 / HMAC")
        tabs.addTab(self._tab_cipher(), "对称加密")
        tabs.addTab(self._tab_jwt(), "JWT 解析")
        tabs.addTab(self._tab_time(), "时间戳")
        root.addWidget(tabs)

    # ------------------------------------------------------------------ #
    #  1) 编码 / 解码
    # ------------------------------------------------------------------ #
    def _tab_codec(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(10)

        self.codec_op = QComboBox()
        self.codec_op.addItems([
            "Base64", "Base64（URL 安全）", "URL 编码", "HTML 实体",
            "Unicode 转义", "Hex（十六进制）", "字符 ↔ 码点",
        ])
        lay.addWidget(widgets.row(
            widgets.label("方式：", "label"), self.codec_op, None,
            widgets.primary("编码 ▶", self._do_encode),
            widgets.primary("◀ 解码", self._do_decode),
            widgets.chip("⇅ 互换", self._codec_swap),
            widgets.chip("复制结果", lambda: widgets.copy_text(self, self.codec_out.text())),
            widgets.chip("清空", self._codec_clear),
        ))

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        c_in = widgets.Card("输入")
        self.codec_in = widgets.CodeEditor(placeholder="在此输入要编码 / 解码的内容…", wrap=True)
        c_in.add(widgets.expanding(self.codec_in))
        c_out = widgets.Card("输出")
        self.codec_out = widgets.CodeEditor(placeholder="结果显示在这里…", wrap=True)
        self.codec_out.setReadOnly(True)
        c_out.add(widgets.expanding(self.codec_out))
        split.addWidget(c_in)
        split.addWidget(c_out)
        split.setSizes([1, 1])
        lay.addWidget(split, 1)
        return w

    def _codec_clear(self):
        self.codec_in.set_text("")
        self.codec_out.set_text("")

    def _codec_swap(self):
        self.codec_in.set_text(self.codec_out.text())
        self.codec_out.set_text("")

    def _do_encode(self):
        self._codec_run(True)

    def _do_decode(self):
        self._codec_run(False)

    def _codec_run(self, encode: bool):
        op = self.codec_op.currentText()
        src = self.codec_in.text()
        try:
            self.codec_out.set_text(self._codec_transform(op, src, encode))
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"{'编码' if encode else '解码'}失败：{e}", "error")

    @staticmethod
    def _codec_transform(op: str, s: str, encode: bool) -> str:
        if op == "Base64":
            return base64.b64encode(s.encode("utf-8")).decode() if encode \
                else base64.b64decode(_fix_b64(s)).decode("utf-8", "replace")
        if op == "Base64（URL 安全）":
            return base64.urlsafe_b64encode(s.encode("utf-8")).decode() if encode \
                else base64.urlsafe_b64decode(_fix_b64(s)).decode("utf-8", "replace")
        if op == "URL 编码":
            return quote(s, safe="") if encode else unquote(s)
        if op == "HTML 实体":
            return html.escape(s) if encode else html.unescape(s)
        if op == "Unicode 转义":
            if encode:
                return "".join(c if ord(c) < 128 else f"\\u{ord(c):04x}" for c in s)
            return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
        if op == "Hex（十六进制）":
            if encode:
                return s.encode("utf-8").hex()
            return bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", s)).decode("utf-8", "replace")
        if op == "字符 ↔ 码点":
            if encode:
                return " ".join(str(ord(c)) for c in s)
            return "".join(chr(int(x)) for x in s.split())
        return s

    # ------------------------------------------------------------------ #
    #  2) 哈希 / HMAC
    # ------------------------------------------------------------------ #
    def _tab_hash(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(10)

        c_in = widgets.Card("输入文本")
        self.hash_in = widgets.CodeEditor(placeholder="在此输入要计算摘要的内容…", wrap=True)
        self.hash_in.textChanged.connect(self._calc_hash)
        c_in.add(self.hash_in)
        c_in.body.setStretch(0, 0)
        self.hash_in.setFixedHeight(120)
        lay.addWidget(c_in)

        c_out = widgets.Card("摘要（实时计算）")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.hash_fields = {}
        for i, algo in enumerate(["MD5", "SHA1", "SHA256", "SHA512"]):
            grid.addWidget(widgets.label(algo, "label"), i, 0)
            fld = QLineEdit()
            fld.setReadOnly(True)
            fld.setFont(widgets.mono_font(11))
            grid.addWidget(fld, i, 1)
            grid.addWidget(widgets.chip("复制", lambda _=False, f=fld: widgets.copy_text(self, f.text())), i, 2)
            self.hash_fields[algo] = fld
        grid.setColumnStretch(1, 1)
        c_out.add(grid)
        lay.addWidget(c_out)

        c_hmac = widgets.Card("HMAC")
        self.hmac_key = QLineEdit()
        self.hmac_key.setPlaceholderText("密钥 key")
        self.hmac_algo = QComboBox()
        self.hmac_algo.addItems(["sha256", "sha1", "md5", "sha512"])
        self.hmac_out = QLineEdit()
        self.hmac_out.setReadOnly(True)
        self.hmac_out.setFont(widgets.mono_font(11))
        c_hmac.add(widgets.row(
            widgets.label("密钥：", "label"), (self.hmac_key, 1),
            widgets.label("算法：", "label"), self.hmac_algo,
            widgets.primary("计算", self._calc_hmac),
            widgets.chip("复制", lambda: widgets.copy_text(self, self.hmac_out.text())),
        ))
        c_hmac.add(self.hmac_out)
        lay.addWidget(c_hmac)
        lay.addStretch(1)
        self._calc_hash()
        return w

    def _calc_hash(self):
        data = self.hash_in.text().encode("utf-8")
        for algo, fld in self.hash_fields.items():
            fld.setText(hashlib.new(algo.lower(), data).hexdigest())

    def _calc_hmac(self):
        try:
            key = self.hmac_key.text().encode("utf-8")
            algo = self.hmac_algo.currentText()
            digest = hmac.new(key, self.hash_in.text().encode("utf-8"), algo).hexdigest()
            self.hmac_out.setText(digest)
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"HMAC 计算失败：{e}", "error")

    # ------------------------------------------------------------------ #
    #  3) 对称加密
    # ------------------------------------------------------------------ #
    def _tab_cipher(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(10)

        if not _CRYPTO:
            tip = widgets.label("对称加密需要 pycryptodome：pip install pycryptodome", "hint")
            lay.addWidget(tip)
            lay.addStretch(1)
            return w

        self.cip_algo = QComboBox()
        self.cip_algo.addItems(["AES", "DES", "3DES", "RC4"])
        self.cip_algo.currentTextChanged.connect(self._cip_algo_changed)
        self.cip_mode = QComboBox()
        self.cip_mode.addItems(["CBC", "ECB"])
        self.cip_enc = QComboBox()
        self.cip_enc.addItems(["Base64", "Hex"])
        lay.addWidget(widgets.row(
            widgets.label("算法：", "label"), self.cip_algo,
            widgets.label("模式：", "label"), self.cip_mode,
            widgets.label("输出：", "label"), self.cip_enc,
            None,
            widgets.primary("加密 ▶", self._encrypt),
            widgets.primary("◀ 解密", self._decrypt),
        ))

        self.cip_key = QLineEdit()
        self.cip_key.setPlaceholderText("密钥 key（AES: 16/24/32 位，DES: 8 位，3DES: 16/24 位）")
        self.cip_iv = QLineEdit()
        self.cip_iv.setPlaceholderText("IV 向量（CBC 模式必填，AES: 16 位，DES/3DES: 8 位）")
        lay.addWidget(widgets.row(widgets.label("密钥：", "label"), (self.cip_key, 1)))
        lay.addWidget(widgets.row(widgets.label("IV：", "label"), (self.cip_iv, 1)))

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        c_in = widgets.Card("输入（加密填明文 / 解密填密文）")
        self.cip_in = widgets.CodeEditor(placeholder="待加密的明文，或待解密的密文…", wrap=True)
        c_in.add(widgets.expanding(self.cip_in))
        c_out = widgets.Card("输出")
        self.cip_out = widgets.CodeEditor(placeholder="结果…", wrap=True)
        self.cip_out.setReadOnly(True)
        c_out.add(widgets.expanding(self.cip_out))
        cout_bar = widgets.row(None, widgets.chip("复制结果", lambda: widgets.copy_text(self, self.cip_out.text())))
        c_out.add(cout_bar)
        split.addWidget(c_in)
        split.addWidget(c_out)
        split.setSizes([1, 1])
        lay.addWidget(split, 1)
        return w

    def _cip_algo_changed(self, algo: str):
        self.cip_mode.setEnabled(algo != "RC4")
        self.cip_iv.setEnabled(algo != "RC4")

    def _cipher_spec(self):
        algo = self.cip_algo.currentText()
        key = self.cip_key.text().encode("utf-8")
        if algo == "AES":
            return AES, key, AES.block_size
        if algo == "DES":
            return DES, key, DES.block_size
        if algo == "3DES":
            return DES3, key, DES3.block_size
        return ARC4, key, 0

    def _new_cipher(self, factory, key, mode_needs_iv: bool):
        algo = self.cip_algo.currentText()
        if algo == "RC4":
            return ARC4.new(key)
        mode = self.cip_mode.currentText()
        if mode == "ECB":
            return factory.new(key, factory.MODE_ECB)
        iv = self.cip_iv.text().encode("utf-8")
        return factory.new(key, factory.MODE_CBC, iv)

    def _encrypt(self):
        try:
            factory, key, bs = self._cipher_spec()
            data = self.cip_in.text().encode("utf-8")
            cipher = self._new_cipher(factory, key, True)
            if bs:
                data = pad(data, bs)
            ct = cipher.encrypt(data)
            out = base64.b64encode(ct).decode() if self.cip_enc.currentText() == "Base64" else ct.hex()
            self.cip_out.set_text(out)
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"加密失败：{e}", "error")

    def _decrypt(self):
        try:
            factory, key, bs = self._cipher_spec()
            raw = self.cip_in.text().strip()
            ct = base64.b64decode(_fix_b64(raw)) if self.cip_enc.currentText() == "Base64" \
                else bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", raw))
            cipher = self._new_cipher(factory, key, True)
            pt = cipher.decrypt(ct)
            if bs:
                pt = unpad(pt, bs)
            self.cip_out.set_text(pt.decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"解密失败：{e}", "error")

    # ------------------------------------------------------------------ #
    #  4) JWT 解析
    # ------------------------------------------------------------------ #
    def _tab_jwt(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(10)

        c_in = widgets.Card("JWT Token")
        self.jwt_in = widgets.CodeEditor(placeholder="粘贴 JWT（形如 xxxxx.yyyyy.zzzzz）…", wrap=True)
        self.jwt_in.setFixedHeight(110)
        c_in.add(self.jwt_in)
        c_in.add(widgets.row(
            None,
            widgets.chip("粘贴", lambda: self.jwt_in.set_text(widgets.paste_text())),
            widgets.primary("解析", self._parse_jwt),
        ))
        lay.addWidget(c_in)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        c_h = widgets.Card("Header")
        self.jwt_header = widgets.CodeEditor(wrap=True)
        self.jwt_header.setReadOnly(True)
        c_h.add(widgets.expanding(self.jwt_header))
        c_p = widgets.Card("Payload")
        self.jwt_payload = widgets.CodeEditor(wrap=True)
        self.jwt_payload.setReadOnly(True)
        c_p.add(widgets.expanding(self.jwt_payload))
        split.addWidget(c_h)
        split.addWidget(c_p)
        split.setSizes([1, 1])
        lay.addWidget(split, 1)

        self.jwt_sig = QLineEdit()
        self.jwt_sig.setReadOnly(True)
        self.jwt_sig.setFont(widgets.mono_font(11))
        lay.addWidget(widgets.row(widgets.label("签名（未验证）：", "label"), (self.jwt_sig, 1)))
        return w

    def _parse_jwt(self):
        token = self.jwt_in.text().strip()
        parts = token.split(".")
        if len(parts) < 2:
            widgets.notify(self, "不是有效的 JWT（应由 . 分隔为三段）", "error")
            return
        try:
            self.jwt_header.set_text(_b64json(parts[0]))
            self.jwt_payload.set_text(_b64json(parts[1]))
            self.jwt_sig.setText(parts[2] if len(parts) > 2 else "（无签名段）")
            widgets.notify(self, "JWT 解析完成", "success")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"JWT 解析失败：{e}", "error")

    # ------------------------------------------------------------------ #
    #  5) 时间戳
    # ------------------------------------------------------------------ #
    def _tab_time(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(12)

        c1 = widgets.Card("时间戳 → 日期")
        self.ts_in = QLineEdit()
        self.ts_in.setPlaceholderText("输入时间戳（自动识别秒 / 毫秒）")
        self.ts_in.setFont(widgets.mono_font(12))
        c1.add(widgets.row(
            (self.ts_in, 1),
            widgets.chip("填入当前", self._ts_now),
            widgets.primary("转换", self._ts_to_date),
        ))
        self.ts_local = QLineEdit(); self.ts_local.setReadOnly(True); self.ts_local.setFont(widgets.mono_font(12))
        self.ts_utc = QLineEdit(); self.ts_utc.setReadOnly(True); self.ts_utc.setFont(widgets.mono_font(12))
        c1.add(widgets.row(widgets.label("本地时间：", "label"), (self.ts_local, 1),
                           widgets.chip("复制", lambda: widgets.copy_text(self, self.ts_local.text()))))
        c1.add(widgets.row(widgets.label("UTC 时间：", "label"), (self.ts_utc, 1),
                           widgets.chip("复制", lambda: widgets.copy_text(self, self.ts_utc.text()))))
        lay.addWidget(c1)

        c2 = widgets.Card("日期 → 时间戳")
        self.date_in = QLineEdit()
        self.date_in.setPlaceholderText("输入日期，如 2026-07-27 12:00:00")
        self.date_in.setFont(widgets.mono_font(12))
        c2.add(widgets.row(
            (self.date_in, 1),
            widgets.chip("填入当前", self._date_now),
            widgets.primary("转换", self._date_to_ts),
        ))
        self.date_sec = QLineEdit(); self.date_sec.setReadOnly(True); self.date_sec.setFont(widgets.mono_font(12))
        self.date_ms = QLineEdit(); self.date_ms.setReadOnly(True); self.date_ms.setFont(widgets.mono_font(12))
        c2.add(widgets.row(widgets.label("秒级：", "label"), (self.date_sec, 1),
                           widgets.chip("复制", lambda: widgets.copy_text(self, self.date_sec.text()))))
        c2.add(widgets.row(widgets.label("毫秒级：", "label"), (self.date_ms, 1),
                           widgets.chip("复制", lambda: widgets.copy_text(self, self.date_ms.text()))))
        lay.addWidget(c2)
        lay.addStretch(1)
        return w

    def _ts_now(self):
        self.ts_in.setText(str(int(time.time())))

    def _ts_to_date(self):
        raw = self.ts_in.text().strip()
        if not re.fullmatch(r"\d+", raw):
            widgets.notify(self, "请输入纯数字时间戳", "error")
            return
        n = int(raw)
        if len(raw) >= 13:   # 毫秒
            n = n / 1000.0
        try:
            self.ts_local.setText(datetime.fromtimestamp(n).strftime("%Y-%m-%d %H:%M:%S"))
            self.ts_utc.setText(datetime.fromtimestamp(n, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"转换失败：{e}", "error")

    def _date_now(self):
        self.date_in.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _date_to_ts(self):
        raw = self.date_in.text().strip()
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            widgets.notify(self, "无法识别日期格式（如 2026-07-27 12:00:00）", "error")
            return
        try:
            sec = int(dt.timestamp())
        except (OSError, OverflowError, ValueError):
            widgets.notify(self, "该日期超出可转换范围", "error")
            return
        self.date_sec.setText(str(sec))
        self.date_ms.setText(str(sec * 1000))


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _fix_b64(s: str) -> str:
    s = re.sub(r"\s", "", s)
    return s + "=" * (-len(s) % 4)


def _b64json(seg: str) -> str:
    seg = seg.replace("-", "+").replace("_", "/")
    seg += "=" * (-len(seg) % 4)
    raw = base64.b64decode(seg)
    try:
        return json.dumps(json.loads(raw.decode("utf-8")), ensure_ascii=False, indent=2)
    except Exception:
        return raw.decode("utf-8", "replace")
