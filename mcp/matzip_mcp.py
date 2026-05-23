#!/usr/bin/env python3
"""
Matzip MCP Server
Hermes Agent가 호출하는 맛집 관련 Tool 모음
"""

import os
import sys
import json
import math
import logging
import asyncio
import threading
import requests
import psycopg2
from contextlib import contextmanager
from dotenv import load_dotenv

# .env 로드 (로컬 실행 시)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#hermes")

# 이미 알림 보낸 장소 (프로세스 수명 동안 유지)
_notified: set[int] = set()
_notified_lock = threading.Lock()

server = Server("matzip")


# ── 내부 유틸 ──────────────────────────────────────────────────────────────────

@contextmanager
def _db_cursor():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor()
        yield cur
        cur.close()
    finally:
        conn.close()


def _json_response(obj) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=json.dumps(obj, ensure_ascii=False))]


def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _db_find_nearby(lat: float, lng: float, radius: int) -> list[dict]:
    with _db_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, address, memo, lat, lng,
                   ST_Distance(location::geography,
                               ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography) AS dist
            FROM matzip
            WHERE ST_DWithin(location::geography,
                             ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s)
            ORDER BY dist
            """,
            (lng, lat, lng, lat, radius),
        )
        rows = cur.fetchall()
    return [
        {"id": r[0], "name": r[1], "address": r[2], "memo": r[3],
         "lat": r[4], "lng": r[5], "distance_m": int(r[6])}
        for r in rows
    ]


def _reverse_geocode(lat: float, lng: float) -> str:
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lng}", "language": "ko", "key": GOOGLE_MAPS_API_KEY},
            timeout=5,
        )
        results = r.json().get("results", [])
        for result in results:
            by_type = {t: c["long_name"] for c in result["address_components"] for t in c["types"]}
            for t in ("sublocality_level_2", "sublocality_level_1", "locality",
                      "administrative_area_level_3", "administrative_area_level_2"):
                if t in by_type:
                    return by_type[t]
    except Exception as e:
        log.error(f"역지오코딩 실패: {e}")
    return f"{lat:.4f}, {lng:.4f}"


def _geocode_area(area: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": area, "language": "ko", "region": "KR", "key": GOOGLE_MAPS_API_KEY},
            timeout=5,
        )
        results = r.json().get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return float(loc["lat"]), float(loc["lng"])
    except Exception as e:
        log.error(f"지오코딩 실패: {e}")
    return None


def _get_current_location() -> tuple[float, float] | None:
    home_lat = os.environ.get("HOME_LAT")
    home_lng = os.environ.get("HOME_LNG")
    if home_lat and home_lng:
        return float(home_lat), float(home_lng)
    try:
        r = requests.get("http://ip-api.com/json/?lang=ko&fields=status,lat,lon", timeout=5)
        data = r.json()
        if data.get("status") == "success":
            return float(data["lat"]), float(data["lon"])
    except Exception:
        pass
    return None


# ── Tool 정의 ──────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="find_nearby",
            description="현재 위치 또는 지정 좌표 반경 내 저장된 맛집을 검색합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "위도"},
                    "lng": {"type": "number", "description": "경도"},
                    "radius_m": {"type": "integer", "description": "검색 반경(미터), 기본 1000", "default": 1000},
                },
                "required": ["lat", "lng"],
            },
        ),
        types.Tool(
            name="geocode_area",
            description="지역명을 위도·경도로 변환합니다. 예: '홍대' → (37.55, 126.92)",
            inputSchema={
                "type": "object",
                "properties": {
                    "area": {"type": "string", "description": "지역명 (동·구·역 이름 등)"},
                },
                "required": ["area"],
            },
        ),
        types.Tool(
            name="get_current_location",
            description="현재 위치(위도·경도)를 반환합니다. HOME_LAT/HOME_LNG 설정 시 우선 사용.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="reverse_geocode",
            description="위도·경도를 지역명으로 변환합니다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                },
                "required": ["lat", "lng"],
            },
        ),
        types.Tool(
            name="get_area_clusters",
            description="저장된 맛집 데이터에서 밀집 지역 상위 5곳을 반환합니다.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="check_and_notify",
            description="현재 위치 근처의 새 맛집을 확인하고 Slack으로 알림을 보냅니다. 이미 알림 보낸 곳은 제외.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "find_nearby":
        lat = arguments["lat"]
        lng = arguments["lng"]
        radius = arguments.get("radius_m", 1000)
        for r in [radius, 2000, 5000]:
            places = _db_find_nearby(lat, lng, r)
            if len(places) >= 3 or r == 5000:
                result = {"radius_used_m": r, "count": len(places), "places": places}
                break
        log.info("find_nearby lat=%.4f lng=%.4f radius=%dm → %d개", lat, lng, result["radius_used_m"], result["count"])
        return _json_response(result)

    elif name == "geocode_area":
        area = arguments["area"]
        loc = _geocode_area(area)
        if loc:
            log.info("geocode_area '%s' → lat=%.4f lng=%.4f", area, loc[0], loc[1])
            return _json_response({"lat": loc[0], "lng": loc[1]})
        log.warning("geocode_area '%s' → 결과 없음", area)
        return _json_response({"error": f"'{area}' 위치를 찾을 수 없습니다"})

    elif name == "get_current_location":
        loc = _get_current_location()
        if loc:
            area = _reverse_geocode(loc[0], loc[1])
            log.info("get_current_location → lat=%.4f lng=%.4f (%s)", loc[0], loc[1], area)
            return _json_response({"lat": loc[0], "lng": loc[1], "area": area})
        log.warning("get_current_location → 위치 조회 실패")
        return _json_response({"error": "위치를 가져올 수 없습니다"})

    elif name == "reverse_geocode":
        area = _reverse_geocode(arguments["lat"], arguments["lng"])
        log.info("reverse_geocode lat=%.4f lng=%.4f → '%s'", arguments["lat"], arguments["lng"], area)
        return _json_response({"area": area})

    elif name == "get_area_clusters":
        with _db_cursor() as cur:
            cur.execute(
                """
                SELECT m1.lat, m1.lng, COUNT(m2.id) AS nearby_cnt
                FROM matzip m1
                JOIN matzip m2 ON ST_DWithin(m1.location::geography, m2.location::geography, 3000)
                GROUP BY m1.id, m1.lat, m1.lng
                ORDER BY nearby_cnt DESC
                """,
            )
            rows = cur.fetchall()

        selected = []
        for lat, lng, cnt in rows:
            if all(_haversine(lat, lng, s[0], s[1]) > 3000 for s in selected):
                selected.append((lat, lng, cnt))
                if len(selected) >= 5:
                    break

        clusters = [{"lat": lat, "lng": lng, "count": int(cnt), "area": _reverse_geocode(lat, lng)}
                    for lat, lng, cnt in selected]
        log.info("get_area_clusters → %d개 클러스터: %s", len(clusters), [c["area"] for c in clusters])
        return _json_response(clusters)

    elif name == "check_and_notify":
        loc = _get_current_location()
        if not loc:
            return _json_response({"error": "위치 조회 실패"})
        lat, lng = loc
        nearby = _db_find_nearby(lat, lng, int(os.environ.get("PROXIMITY_RADIUS_METERS", 500)))

        with _notified_lock:
            new_places = [p for p in nearby if p["id"] not in _notified]
            if new_places:
                area = _reverse_geocode(lat, lng)
                _send_slack_notification(new_places, area)
                for p in new_places:
                    _notified.add(p["id"])
            nearby_ids = {p["id"] for p in nearby}
            _notified.intersection_update(nearby_ids)

        log.info("check_and_notify lat=%.4f lng=%.4f → 근처 %d개, 신규 알림 %d개: %s",
                 lat, lng, len(nearby), len(new_places), [p["name"] for p in new_places])
        return _json_response({"new_count": len(new_places), "notified": [p["name"] for p in new_places]})

    raise ValueError(f"Unknown tool: {name}")


def _send_slack_notification(places: list[dict], area: str):
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🗺️ 근처에 저장한 맛집이 있어요!", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*현재 위치:* {area} 근방"}},
        {"type": "divider"},
    ]
    for p in places[:5]:
        memo = f"\n> _{p['memo']}_" if p["memo"] else ""
        walk = max(1, p["distance_m"] // 80)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": (
                f"*{p['name']}*{memo}\n"
                f"📍 {p['address']}\n"
                f"🚶 {p['distance_m']}m · 도보 약 {walk}분"
            )},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "카카오맵 보기"},
                "url": f"https://map.kakao.com/?q={p['name']}&from=roughmap&lon={p['lng']}&lat={p['lat']}&level=3",
            },
        })
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=10,
    )


# ── 진입점 ─────────────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
