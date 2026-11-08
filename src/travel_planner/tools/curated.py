"""人工维护的热门景点表：门票、开放时间、预约要求。

高德不提供门票价格，但门票在"攻略可信度"里权重很高。
用一张小表覆盖头部景点；查不到的保持 None（渲染成"以景区公示为准"），不编造。

数据文件：data/curated/tickets.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# src/travel_planner/tools/curated.py -> 项目根
_DATA = Path(__file__).resolve().parents[3] / "data" / "curated" / "tickets.json"

_cache: dict | None = None
_cache_mtime: float = 0.0


def _load(path: Path | None = None) -> dict:
    global _cache, _cache_mtime
    p = path or _DATA
    if not p.exists():
        return {"cities": {}}
    mtime = p.stat().st_mtime
    if _cache is None or mtime != _cache_mtime:
        _cache = json.loads(p.read_text(encoding="utf-8"))
        _cache_mtime = mtime
    return _cache


def _norm(text: str) -> str:
    """去掉括号内容、空格和"市"字，用于宽松匹配。

    只用在这里做"候选比较"，不会改动展示用的原始名字。
    """
    s = re.sub(r"[（(].*?[）)]", "", text or "")
    return s.replace(" ", "").replace("·", "").replace("市", "")


def lookup(city: str, name: str) -> dict | None:
    """按「精确 → 别名 → 归一化包含」三级匹配。"""
    entries = (_load().get("cities") or {}).get(city) or {}
    if not entries or not name:
        return None

    # 1. 精确命中
    if name in entries:
        return entries[name]

    # 2. 别名命中
    for item in entries.values():
        if name in (item.get("aliases") or []):
            return item

    # 3. 归一化后完全相等（"大同市博物馆" ↔ "大同博物馆"）
    #    故意不做"包含匹配"：否则"平遥古城-清虚观"会命中"平遥古城"的通票，
    #    错的票价比如实说"未知"更糟
    n = _norm(name)
    if len(n) < 2:
        return None
    for canonical, item in entries.items():
        if _norm(canonical) == n:
            return item
        for alias in item.get("aliases") or []:
            if _norm(alias) == n:
                return item
    return None


def apply(city: str, pois: list) -> int:
    """把查表结果覆盖到 POI 上，返回命中数量。

    只覆盖查到的字段；查不到的保持原值（通常是 None），交给渲染层说"以公示为准"。
    """
    hits = 0
    for poi in pois:
        item = lookup(city, poi.name)
        if not item:
            continue
        if item.get("ticket") is not None:
            poi.ticket = float(item["ticket"])
        if item.get("booking_required") is not None:
            poi.booking_required = bool(item["booking_required"])
        if item.get("open_hours"):
            poi.open_hours = item["open_hours"]
        if item.get("note"):
            poi.tips = item["note"]
        poi.source = "amap+curated"
        hits += 1
    return hits
