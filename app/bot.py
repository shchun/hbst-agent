"""
Slack Bot — 맛집 질의 처리
- "주변 맛집 알려줘"  → 현재 IP 위치 기준 조회
- "홍대 맛집 알려줘"  → 지역명 지오코딩 후 조회
"""

import re
import os
import logging
import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import agent

log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
AREA_RADIUS_METERS = int(os.environ.get("AREA_RADIUS_METERS", 1000))
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

NEARBY_KEYWORDS = {"주변", "근처", "근방", "이근처", "이근방", "여기", "내", "현재", "지금"}


def parse_area(text: str) -> str | None:
    """텍스트에서 지역명 추출. '홍대 근처 맛집' → '홍대', 현재위치 키워드면 None."""
    text = re.sub(r"<@\w+>", "", text).strip()
    m = re.search(r"(.+?)\s*맛집", text)
    if m:
        area = m.group(1).strip()
        # "홍대 근처", "강남 주변" 처럼 뒤에 붙은 위치 키워드 제거
        area = re.sub(r"\s*(근처|주변|근방|이근처|이근방)$", "", area).strip()
        return None if area in NEARBY_KEYWORDS or not area else area
    return None


def geocode_area(area: str) -> tuple[float, float] | None:
    """지역명 → (위도, 경도). Google Geocoding API 사용."""
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
        log.error(f"지오코딩 실패 ({area}): {e}")
    return None


_EXPAND_STEPS = [1_000, 2_000, 5_000]
_MIN_RESULTS = 3


def find_with_expanding_radius(lat: float, lng: float) -> tuple[list[dict], int]:
    """결과가 적으면 반경을 자동으로 넓혀가며 검색. (결과 리스트, 사용된 반경) 반환."""
    for radius in _EXPAND_STEPS:
        places = agent.find_nearby(lat, lng, radius)
        if len(places) >= _MIN_RESULTS or radius == _EXPAND_STEPS[-1]:
            return places, radius
    return [], _EXPAND_STEPS[-1]


def kakaomap_url(name: str, lat: float, lng: float, level: int = 4) -> str:
    return f"https://map.kakao.com/?q={name}&from=roughmap&lon={lng}&lat={lat}&level={level}"


def build_blocks(places: list[dict], area_label: str, lat: float, lng: float, radius_m: int = 1_000) -> list:
    radius_label = f"{radius_m // 1000}km" if radius_m >= 1000 else f"{radius_m}m"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🗺️ 저장된 맛집이에요!", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{area_label}* 반경 {radius_label} 내 저장된 맛집"},
        },
        {"type": "divider"},
    ]

    if not places:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "😅 저장된 맛집이 없어요."},
        })
        return blocks

    shown = places[:5]
    for p in shown:
        memo_text = f"\n> _{p['memo']}_" if p["memo"] else ""
        walk_min = max(1, p["distance_m"] // 80)
        blocks.append({
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
                "url": kakaomap_url(p["name"], p["lat"], p["lng"], level=3),
            },
        })

    extra = len(places) - len(shown)
    more_text = f" 외 {extra}곳 더 있어요." if extra > 0 else ""

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"{more_text} 지도에서 전체 보기 →",
        },
        "accessory": {
            "type": "button",
            "text": {"type": "plain_text", "text": "더보기 🗺️"},
            "url": kakaomap_url(area_label, lat, lng, level=5),
        },
    })

    return blocks


def _has_nearby_keyword(text: str) -> bool:
    """'주변 맛집', '근처 맛집' 등 현재 위치 기반 질의인지 판단."""
    text = re.sub(r"<@\w+>", "", text).strip()
    m = re.search(r"(.+?)\s*맛집", text)
    if m:
        area = re.sub(r"\s*(근처|주변|근방|이근처|이근방)$", "", m.group(1).strip()).strip()
        return area in NEARBY_KEYWORDS
    return False


def _show_area_buttons(say) -> None:
    clusters = agent.get_area_clusters()
    if not clusters:
        say("저장된 맛집 데이터가 없어요.")
        return
    say(
        text="어느 지역 맛집을 찾으시나요?",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "어느 지역 맛집을 찾으시나요?"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": c["label"]},
                        "action_id": f"area_query_{i}",
                        "value": f"{c['lat']}|{c['lng']}|{c['label']}",
                    }
                    for i, c in enumerate(clusters)
                ],
            },
        ],
    )


def handle_query(text: str, say):
    if "맛집" not in text:
        say(
            text="맛집 정보를 알려드릴게요!",
            blocks=[{
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "맛집 정보를 알려드릴게요! 예시:\n"
                        "• `주변 맛집 알려줘`\n"
                        "• `홍대 맛집 알려줘`\n"
                        "• `강남 맛집 정보 알려줘`"
                    ),
                },
            }],
        )
        return

    area_name = parse_area(text)

    if area_name:
        loc = geocode_area(area_name)
        if loc is None:
            say(f"'{area_name}' 지역을 찾을 수 없어요 😕\n구·동·역 이름으로 다시 시도해보세요 (예: `해운대구 맛집 알려줘`)")
            return
        lat, lng = loc
        area_label = area_name
    elif _has_nearby_keyword(text):
        loc = agent.get_current_location()
        if loc is None:
            say(
                text="위치를 가져올 수 없어요",
                blocks=[{
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "📍 현재 위치를 가져올 수 없어요.\n"
                            "• 지역명을 직접 알려주세요 → `홍대 맛집 알려줘`\n"
                            "• 또는 `.env`에 `HOME_LAT` / `HOME_LNG` 를 설정하면 주변 맛집도 조회돼요"
                        ),
                    },
                }],
            )
            return
        lat, lng = loc
        area_label = agent.reverse_geocode(lat, lng)
    else:
        _show_area_buttons(say)
        return

    places, radius = find_with_expanding_radius(lat, lng)
    blocks = build_blocks(places, area_label, lat, lng, radius_m=radius)
    say(text=f"{area_label} 근처 맛집이에요!", blocks=blocks)


def _register_handlers(app: App) -> None:
    @app.event("app_mention")
    def handle_mention(event, say):
        handle_query(event["text"], say)

    @app.event("message")
    def handle_dm(event, say):
        # DM 채널에서 사용자 메시지만 처리 (봇 자신 메시지·이벤트 subtype 제외)
        if (
            event.get("channel_type") == "im"
            and not event.get("bot_id")
            and not event.get("subtype")
        ):
            handle_query(event.get("text", ""), say)

    @app.action(re.compile(r"area_query_\d+"))
    def handle_area_button(ack, body, say):
        ack()
        value = body["actions"][0]["value"]
        lat_str, lng_str, label = value.split("|", 2)
        lat, lng = float(lat_str), float(lng_str)
        places, radius = find_with_expanding_radius(lat, lng)
        blocks = build_blocks(places, label, lat, lng, radius_m=radius)
        say(text=f"{label} 근처 맛집이에요!", blocks=blocks)

    @app.action(re.compile(r".*"))
    def handle_button_click(ack):
        ack()


def start() -> None:
    _app = App(token=SLACK_BOT_TOKEN)
    _register_handlers(_app)
    handler = SocketModeHandler(_app, SLACK_APP_TOKEN)
    log.info("🤖 Slack 봇 시작 (Socket Mode)")
    handler.start()
