"""高德 Web 服务 API 客户端。

职责边界：**只负责取数**——调用、重试、内存缓存、错误归一化。
不依赖 Pydantic 模型，返回扁平 dict，由上层映射成 POICandidate。

为什么这样分层：
- 工具层不关心业务模型，换数据源时只改这一层；
- 上层（notebook / scout）拿到的是稳定字段名，不直接碰高德的原始结构。

用法：
    from travel_planner.tools.amap import AmapClient
    client = AmapClient()
    raw = client.place_text("大同", keywords="景点")
    pois = [normalize_poi(p) for p in raw]
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import httpx

BASE = "https://restapi.amap.com"
DEFAULT_TIMEOUT = 10.0
DEFAULT_CACHE_TTL = 24 * 3600  # 同一参数 24 小时内不重复请求（保护个人 key 配额）


class AmapError(RuntimeError):
    """高德返回 status != 1，或网络失败。infocode 见官方错误码表。"""

    def __init__(self, message: str, infocode: str | None = None):
        super().__init__(message)
        self.infocode = infocode


def _text(value) -> str:
    """高德在字段缺失时会返回 []，这里统一成空字符串。"""
    return value if isinstance(value, str) else ""


def _point(location) -> tuple[float | None, float | None]:
    """'113.122,40.110' -> (113.122, 40.110)。缺失时返回 (None, None)。"""
    if not isinstance(location, str) or "," not in location:
        return None, None
    try:
        lng, lat = location.split(",")[:2]
        return float(lng), float(lat)
    except (TypeError, ValueError):
        return None, None


# ------------------------------------------------------------------
# 分类与时长规则
# 高德 type 形如 "风景名胜;风景名胜;石窟"，取关键词做粗分类。
# 时长是**启发式估算**，不是事实——所以 POI 的 source 仍标 amap，
# 但 duration 属于"估算值"，后续可由人工表覆盖。
# ------------------------------------------------------------------
_CATEGORY_RULES = [
    ("博物馆", "博物馆"),
    ("展览", "展览"),
    ("美术馆", "美术馆"),
    ("文物", "古建"),
    ("古迹", "古建"),
    ("寺", "古建"),
    ("祠", "古建"),
    ("塔", "古建"),
    ("宫", "古建"),
    ("公园", "公园"),
    ("广场", "广场"),
    ("游乐", "游乐"),
    ("风景", "景区"),
    ("旅游", "景区"),
    ("景区", "景区"),
    ("购物", "商圈"),
    ("商场", "商圈"),
    ("步行街", "商圈"),
    ("街区", "街区"),
]

_DURATION_RULES = {
    "博物馆": 150,
    "展览": 90,
    "美术馆": 90,
    "古建": 90,
    "公园": 60,
    "广场": 45,
    "游乐": 240,
    "街区": 120,
    "景区": 150,
    "商圈": 120,
    "景点": 90,
}


_NAME_DURATION = [
    ("博物院", 150), ("博物馆", 150), ("石窟", 150),
    ("古城墙", 90), ("古城", 150), ("古镇", 150), ("街区", 120),
    ("壁", 45), ("楼", 45), ("塔", 60),
    ("寺", 90), ("庙", 60), ("宫", 90),
    ("公园", 60), ("山", 180),
]


def duration_of(name: str, category: str) -> int:
    """先按名字里的地标类型估时长，再退回按 category 的粗规则。

    九龙壁、市楼这类"小而著名"的点，按 category 会估成 150 分钟，
    实际 30-45 分钟就够，所以名字优先。
    """
    for keyword, minutes in _NAME_DURATION:
        if keyword in (name or ""):
            return minutes
    return _DURATION_RULES.get(category, 90)


def category_of(type_str: str) -> str:
    """高德 type 是 ';' 分级，越往后越具体。从最具体的一段开始匹配。"""
    parts = [p for p in (type_str or "").split(";") if p.strip()]
    for part in reversed(parts):
        for keyword, category in _CATEGORY_RULES:
            if keyword in part:
                return category
    return "景点"


def normalize_poi(raw: dict) -> dict:
    """把高德原始 POI 拍平成 POICandidate 可直接消费的字段。"""
    lng, lat = _point(raw.get("location"))
    business = raw.get("business")
    if not isinstance(business, dict):   # 未返回时是 []
        business = {}

    category = category_of(_text(raw.get("type")))
    address = _text(raw.get("address")) or "".join(
        _text(raw.get(k)) for k in ("pname", "cityname", "adname")
    )

    rating = None
    if _text(business.get("rating")):
        try:
            rating = float(business["rating"])
        except (TypeError, ValueError):
            rating = None

    return {
        "id": _text(raw.get("id")),
        "name": _text(raw.get("name")),
        "category": category,
        "rating": rating,
        "address": address,
        "lng": lng,
        "lat": lat,
        # 高德的营业时间信息很全（含"周一全天不开放"），原样保留
        "open_hours": _text(business.get("opentime_week"))
                      or _text(business.get("opentime_today")) or None,
        "ticket": None,          # 高德不提供门票价格
        "duration_min": duration_of(_text(raw.get("name")), category),
        "booking_required": None,  # 需要预约政策库，暂缺
        "tips": None,            # keytag/rectag 只是分类标签，不当作建议
        "source": "amap",
    }


class AmapClient:
    """带缓存与重试的高德客户端。

    key 默认读环境变量 AMAP_MAPS_API_KEY（必须是「Web服务」类型的 Key）。
    """

    def __init__(
        self,
        key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        cache_ttl: int = DEFAULT_CACHE_TTL,
    ):
        self.key = key or os.getenv("AMAP_MAPS_API_KEY")
        if not self.key:
            raise AmapError("未配置 AMAP_MAPS_API_KEY（检查 .env）")
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, dict]] = {}
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "travel-agent/0.1"},
        )

    # ---------------- 内部 ----------------
    @staticmethod
    def _cache_key(path: str, params: dict) -> str:
        raw = json.dumps([path, sorted(params.items())], ensure_ascii=False, default=str)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _get(self, path: str, **params) -> dict:
        params["key"] = self.key
        ck = self._cache_key(path, params)
        now = time.time()

        cached = self._cache.get(ck)
        if cached and now - cached[0] < self.cache_ttl:
            return cached[1]

        last_error: Exception | None = None
        for attempt in range(2):          # 网络抖动重试一次
            try:
                resp = self._client.get(f"{BASE}{path}", params=params)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
                continue

            if str(data.get("status")) != "1":
                raise AmapError(
                    f"{data.get('info')}（infocode={data.get('infocode')}）",
                    infocode=str(data.get("infocode")),
                )

            self._cache[ck] = (now, data)
            return data

        raise AmapError(f"网络请求失败：{last_error}")

    # ---------------- 对外 ----------------
    def place_text(
        self,
        city: str,
        keywords: str | None = None,
        types: str | None = None,
        page_size: int = 20,
        page_num: int = 1,
    ) -> list[dict]:
        """关键字搜索。types 可用 '110000|140100' 组合（风景名胜|博物馆）。"""
        params = {
            "region": city,
            "city_limit": "true",
            "page_size": min(int(page_size), 25),
            "page_num": max(1, min(int(page_num), 8)),
            "show_fields": "business,photos",   # 不加这行，business 全是空的
        }
        if keywords:
            params["keywords"] = keywords
        if types:
            params["types"] = types
        return self._get("/v5/place/text", **params).get("pois", []) or []

    def place_around(
        self,
        location: str,
        radius: int = 3000,
        keywords: str | None = None,
        types: str | None = None,
        page_size: int = 20,
    ) -> list[dict]:
        """周边搜索。location 形如 '113.30,40.09'；酒店用 types='100000'。"""
        params = {
            "location": location,
            "radius": int(radius),
            "page_size": min(int(page_size), 25),
            "show_fields": "business",
        }
        if keywords:
            params["keywords"] = keywords
        if types:
            params["types"] = types
        return self._get("/v5/place/around", **params).get("pois", []) or []

    def weather(self, adcode: str, extensions: str = "all") -> list[dict]:
        """天气预报。注意 city 要传 adcode（如大同 140200），不是城市名。"""
        data = self._get("/v3/weather/weatherInfo", city=adcode, extensions=extensions)
        return data.get("forecasts", []) or []
