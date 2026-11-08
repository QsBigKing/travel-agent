"""探测高德接口的真实返回，把样本存到 data/samples/。

运行（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\probe_amap.py

它会做三件事：
1. 关键字搜景点（验证 key 与字段）
2. 周边搜酒店（验证酒店能不能拿到价格/名称）
3. 查天气（验证 adcode 与预报天数）
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from travel_planner.tools.amap import AmapClient, AmapError, normalize_poi  # noqa: E402

OUT = ROOT / "data" / "samples"
OUT.mkdir(parents=True, exist_ok=True)


def dump(name: str, obj) -> None:
    (OUT / f"{name}.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    client = AmapClient()
    print("key:", (client.key[:6] + "…") if client.key else "空")

    # ---------- 1. 景点 ----------
    print("\n[1] 关键字搜索：大同 · 景点")
    try:
        pois = client.place_text(
            "大同", keywords="景点", types="110000|140100", page_size=10
        )
    except AmapError as exc:
        print("  失败:", exc, "| infocode =", exc.infocode)
        return

    print(f"  返回 {len(pois)} 条")
    if pois:
        print("  字段:", sorted(pois[0].keys()))
        print(json.dumps(pois[0], ensure_ascii=False, indent=2)[:900])
        dump("poi_text", pois)
        dump("poi_normalized", [normalize_poi(p) for p in pois])

    # ---------- 2. 酒店 ----------
    print("\n[2] 周边搜索：以第一个 POI 为中心，3km 内住宿")
    loc = pois[0].get("location") if pois else None
    if isinstance(loc, str) and loc:
        try:
            hotels = client.place_around(loc, radius=3000, types="100000", page_size=5)
            print(f"  返回 {len(hotels)} 条")
            if hotels:
                print("  第一条:", hotels[0].get("name"), "| 字段:", sorted(hotels[0].keys()))
                print("  business:", hotels[0].get("business"))
            dump("hotel_around", hotels)
        except AmapError as exc:
            print("  失败:", exc)

    # ---------- 3. 天气 ----------
    print("\n[3] 天气：大同 adcode=140200")
    try:
        forecasts = client.weather("140200")
        if forecasts:
            casts = forecasts[0].get("casts", [])
            print(f"  预报 {len(casts)} 天")
            for c in casts[:5]:
                print(
                    "  ",
                    c.get("date"),
                    c.get("dayweather"),
                    f'{c.get("nighttemp")}~{c.get("daytemp")}℃',
                )
            dump("weather", forecasts)
    except AmapError as exc:
        print("  失败:", exc)

    print(f"\n样本已写入 {OUT}")


if __name__ == "__main__":
    main()
