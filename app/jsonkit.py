"""JSON 工具集：解析 / 格式化 / 压缩 / 树填充 / JSONPath / 深度对比。

供「JSON 解析」与「JSON 对比」两个模块共用。
"""
from __future__ import annotations

import json
from collections import OrderedDict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import QTreeWidgetItem

from . import widgets

PATH_ROLE = Qt.UserRole + 1
VALUE_ROLE = Qt.UserRole + 2
LEAF_ROLE = Qt.UserRole + 3


# --------------------------------------------------------------------------- #
#  解析 / 序列化
# --------------------------------------------------------------------------- #
def parse(text: str):
    """返回 (data, error_message)。成功时 error 为 None。"""
    text = (text or "").strip()
    if not text:
        return None, "内容为空"
    try:
        return json.loads(text, object_pairs_hook=OrderedDict), None
    except json.JSONDecodeError as e:
        return None, f"第 {e.lineno} 行 第 {e.colno} 列：{e.msg}"
    except Exception as e:
        return None, str(e)


def pretty(data, indent: int = 2) -> str:
    return json.dumps(data, ensure_ascii=False, indent=indent)


def minify(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def type_name(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _color(kind: str) -> QColor:
    p = widgets.pal()
    return QColor({
        "string": p["ok"], "number": p["accent_hi"], "bool": p["warn"],
        "null": p["fg_muted"], "array": p["fg_dim"], "object": p["fg_dim"],
    }.get(kind, p["fg"]))


def _child_path(path: str, key) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    if isinstance(key, str) and key.replace("_", "a").isalnum() and (key[0].isalpha() or key[0] == "_"):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


def _leaf_text(v) -> str:
    if isinstance(v, str):
        return v
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return json.dumps(v, ensure_ascii=False)


# --------------------------------------------------------------------------- #
#  树填充
# --------------------------------------------------------------------------- #
def populate_tree(tree, data, root_key: str = "$"):
    """把 data 填充进 QTreeWidget（3 列：键 / 值 / 类型）。"""
    tree.clear()
    root = QTreeWidgetItem(tree)
    _fill(root, root_key, data, "$")
    root.setExpanded(True)
    tree.expandToDepth(1)


def _fill(item: QTreeWidgetItem, key, value, path: str):
    kind = type_name(value)
    item.setText(0, str(key))
    item.setText(2, kind)
    item.setForeground(2, QBrush(QColor(widgets.pal()["fg_muted"])))
    item.setData(0, PATH_ROLE, path)
    item.setData(0, VALUE_ROLE, value)

    kfont = item.font(0)
    kfont.setBold(True)
    item.setFont(0, kfont)

    if isinstance(value, dict):
        item.setText(1, f"{{…}}  {len(value)} 项")
        item.setForeground(1, QBrush(_color("object")))
        item.setData(0, LEAF_ROLE, False)
        for k, v in value.items():
            child = QTreeWidgetItem(item)
            _fill(child, k, v, _child_path(path, k))
    elif isinstance(value, list):
        item.setText(1, f"[…]  {len(value)} 项")
        item.setForeground(1, QBrush(_color("array")))
        item.setData(0, LEAF_ROLE, False)
        for i, v in enumerate(value):
            child = QTreeWidgetItem(item)
            _fill(child, i, v, _child_path(path, i))
    else:
        item.setText(1, _leaf_text(value))
        item.setForeground(1, QBrush(_color(kind)))
        item.setData(0, LEAF_ROLE, True)


def item_path(item: QTreeWidgetItem) -> str:
    return item.data(0, PATH_ROLE) or "$"


def item_value(item: QTreeWidgetItem):
    return item.data(0, VALUE_ROLE)


def item_is_leaf(item: QTreeWidgetItem) -> bool:
    return bool(item.data(0, LEAF_ROLE))


# --------------------------------------------------------------------------- #
#  深度对比
# --------------------------------------------------------------------------- #
def deep_diff(a, b, path: str = "$") -> list[dict]:
    """返回差异列表：{path, type: added|removed|changed|type, left, right}"""
    out: list[dict] = []
    _diff(a, b, path, out)
    return out


def _short(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 200 else s[:200] + "…"


def _diff(a, b, path, out):
    ta, tb = type_name(a), type_name(b)
    if ta != tb:
        out.append({"path": path, "type": "type", "left": _short(a), "right": _short(b)})
        return
    if isinstance(a, dict):
        keys = list(dict.fromkeys(list(a.keys()) + list(b.keys())))
        for k in keys:
            cp = _child_path(path, k)
            if k not in a:
                out.append({"path": cp, "type": "added", "left": None, "right": _short(b[k])})
            elif k not in b:
                out.append({"path": cp, "type": "removed", "left": _short(a[k]), "right": None})
            else:
                _diff(a[k], b[k], cp, out)
    elif isinstance(a, list):
        n = max(len(a), len(b))
        for i in range(n):
            cp = _child_path(path, i)
            if i >= len(a):
                out.append({"path": cp, "type": "added", "left": None, "right": _short(b[i])})
            elif i >= len(b):
                out.append({"path": cp, "type": "removed", "left": _short(a[i]), "right": None})
            else:
                _diff(a[i], b[i], cp, out)
    else:
        if a != b:
            out.append({"path": path, "type": "changed", "left": _short(a), "right": _short(b)})
