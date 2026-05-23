#!/usr/bin/env python3
"""
Hermes 맛집 에이전트
현재 위치를 주기적으로 확인하고, 저장된 맛집 근처에 있으면 Slack으로 알림
"""

import os
import time
import math
import logging
import threading
import requests
import psycopg2
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "#hermes")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", 60))
RADIUS_METERS = int(os.environ.get("PROXIMITY_RADIUS_METERS", 500))
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

# 이미 알림 보낸 장소 추적 (재알림 방지)
notified: set[int] = set()


def get_current_location() -> tuple[float, float] | None:
    """현재 위치 반환. HOME_LAT/HOME_LNG 설정 시 우선 사용."""
    home_lat = os.environ.get("HOME_LAT")
    home_lng = os.environ.get("HOME_LNG")
    if home_lat and home_lng:
        return float(home_lat), float(home_lng)
    try:
        r = requests.get("http://ip-api.com/json/?lang=ko&fields=status,lat,lon", timeout=5)
        data = r.json()
        if data.get("status") == "success":
            return float(data["lat"]), float(data["lon"])
        log.error(f"위치 조회 실패: {data}")
    except Exception as e:
        log.error(f"위치 조회 실패: {e}")
    return None


def haversine(lat1, lng1, lat2, lng2) -> float:
    """두 좌표 사이 거리 계산 (미터)"""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearby(lat: float, lng: float, radius: int) -> list[dict]:
    """DB에서 반경 내 맛집 조회"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, address, memo, lat, lng,
               ST_Distance(
                   location::geography,
                   ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
               ) AS distance_m
        FROM matzip
        WHERE ST_DWithin(
            location::geography,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        ORDER BY distance_m
        """,
        (lng, lat, lng, lat, radius),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0],
            "name": r[1],
            "address": r[2],
            "memo": r[3],
            "lat": r[4],
            "lng": r[5],
            "distance_m": int(r[6]),
        }
        for r in rows
    ]


def send_slack(places: list[dict], current_area: str):
    """Slack Block Kit 알림 발송"""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🗺️ 근처에 저장한 맛집이 있어요!", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*현재 위치:* {current_area} 근방"},
        },
        {"type": "divider"},
    ]

    for p in places[:5]:  # 최대 5개
        memo_text = f"\n> _{p['memo']}_" if p["memo"] else ""
        walk_min = max(1, p["distance_m"] // 80)  # 도보 속도 ~80m/min
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{p['name']}*{memo_text}\n"
                        f"📍 {p['address']}\n"
                        f"🚶 {p['distance_m']}m · 도보 약 {walk_min}분"
                    ),
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "카카오맵 보기"},
                    "url": f"https://map.kakao.com/?q={p['name']}&from=roughmap&lon={p['lng']}&lat={p['lat']}&level=3",
                },
            }
        )

    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=10,
    )
    data = r.json()
    if not data.get("ok"):
        log.error(f"Slack 전송 실패: {data.get('error')}")
    else:
        log.info(f"Slack 알림 전송 완료 ({len(places)}개 맛집)")


def reverse_geocode(lat: float, lng: float) -> str:
    """위도경도 → 지역명. Google Reverse Geocoding API 사용."""
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


_clusters_cache: list[dict] | None = None
_clusters_lock = threading.Lock()


def get_area_clusters(show: int = 5, radius: int = 3_000) -> list[dict]:
    """각 식당 기준 반경 radius(m) 내 식당 수가 가장 많은 지점을 탐욕적으로 show개 선택."""
    global _clusters_cache
    with _clusters_lock:
        if _clusters_cache is not None:
            return _clusters_cache

        try:
            conn = psycopg2.connect(DATABASE_URL)
            cur = conn.cursor()
            # 각 식당 기준 반경 내 식당 수 집계 (PostGIS self-join)
            cur.execute(
                """
                SELECT m1.lat, m1.lng, COUNT(m2.id) AS nearby_cnt
                FROM matzip m1
                JOIN matzip m2
                  ON ST_DWithin(m1.location::geography, m2.location::geography, %s)
                GROUP BY m1.id, m1.lat, m1.lng
                ORDER BY nearby_cnt DESC
                """,
                (radius,),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()

            # 탐욕적 선택: 이미 선택된 지점과 radius 이상 떨어진 곳만 추가
            selected: list[tuple[float, float, int]] = []
            for lat, lng, cnt in rows:
                if all(haversine(lat, lng, s[0], s[1]) > radius for s in selected):
                    selected.append((lat, lng, cnt))
                    if len(selected) >= show:
                        break

            clusters = []
            for lat, lng, cnt in selected:
                label = reverse_geocode(lat, lng)
                clusters.append({"lat": lat, "lng": lng, "label": label, "count": int(cnt)})

            _clusters_cache = clusters
            labels = [f"{c['label']}({c['count']}개)" for c in clusters]
            log.info(f"핫스팟 {len(clusters)}개: {labels}")
        except Exception as e:
            log.error(f"클러스터링 실패: {e}")
            _clusters_cache = []

        return _clusters_cache


def run():
    log.info(f"🚀 Hermes 에이전트 시작 (반경 {RADIUS_METERS}m, {CHECK_INTERVAL}초 간격)")
    global notified

    while True:
        loc = get_current_location()
        if loc:
            lat, lng = loc
            nearby = find_nearby(lat, lng, RADIUS_METERS)

            # 아직 알림 안 보낸 것만 필터
            new_places = [p for p in nearby if p["id"] not in notified]

            if new_places:
                area = reverse_geocode(lat, lng)
                log.info(f"📍 {area} 근처 새 맛집 {len(new_places)}개 발견")
                send_slack(new_places, area)
                for p in new_places:
                    notified.add(p["id"])
            else:
                log.info(f"반경 {RADIUS_METERS}m 내 새 맛집 없음")

            # 반경 벗어난 곳은 notified에서 제거 (다시 방문시 재알림)
            nearby_ids = {p["id"] for p in nearby}
            notified &= nearby_ids
        else:
            log.warning("위치를 가져올 수 없어 건너뜁니다")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
