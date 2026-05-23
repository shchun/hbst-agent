import sys
import os
import logging
import pytest
import psycopg2
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import agent
import bot

DB_URL = "postgresql://hermes:hermes1234@localhost:5432/hermes"


def _db_reachable() -> bool:
    try:
        conn = psycopg2.connect(DB_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(
    not _db_reachable(),
    reason="hermes DB not running — docker-compose up -d 먼저 실행",
)


# ── haversine (순수 단위 테스트) ──────────────────────────────────────────────

class TestHaversine:
    def test_same_point_is_zero(self):
        assert agent.haversine(37.5, 127.0, 37.5, 127.0) == 0.0

    def test_north_south_001deg(self):
        """0.01° 위도 이동 ≈ 1112m"""
        d = agent.haversine(37.50, 127.0, 37.51, 127.0)
        assert 1100 < d < 1130

    def test_east_west_001deg_at_seoul(self):
        """서울 위도(37.5°)에서 0.01° 경도 이동 ≈ 883m"""
        d = agent.haversine(37.5, 127.00, 37.5, 127.01)
        assert 870 < d < 900

    def test_symmetry(self):
        d1 = agent.haversine(37.5, 127.0, 37.51, 127.01)
        d2 = agent.haversine(37.51, 127.01, 37.5, 127.0)
        assert abs(d1 - d2) < 0.001

    def test_sadang_to_sinchon(self):
        """사당역 → 신촌역 실측 약 9.5km"""
        d = agent.haversine(37.4768, 126.9816, 37.5549, 126.9368)
        assert 9300 < d < 9800


# ── find_nearby (DB 통합 테스트) ──────────────────────────────────────────────

@needs_db
class TestFindNearby:

    @pytest.fixture(autouse=True)
    def patch_db(self, monkeypatch):
        monkeypatch.setattr(agent, "DATABASE_URL", DB_URL)

    # 사당동 — 맛집 밀집 구역 (시민소머리국밥·파이공장·행복한맥주 등 8개)
    def test_sadang_500m_finds_multiple(self):
        results = agent.find_nearby(37.4878, 126.9803, 500)
        assert len(results) >= 5

    def test_sadang_contains_expected_restaurant(self):
        results = agent.find_nearby(37.4878, 126.9803, 500)
        names = [r["name"] for r in results]
        assert "시민소머리국밥" in names

    def test_sadang_results_sorted_by_distance(self):
        results = agent.find_nearby(37.4878, 126.9803, 500)
        distances = [r["distance_m"] for r in results]
        assert distances == sorted(distances)

    def test_sadang_all_within_radius(self):
        radius = 500
        results = agent.find_nearby(37.4878, 126.9803, radius)
        for r in results:
            assert r["distance_m"] <= radius

    # 성수동 — 카페·식당 클러스터 (제스티살룬·카페차·스아게성수)
    def test_seongsu_500m_finds_cluster(self):
        results = agent.find_nearby(37.5474, 127.0420, 500)
        assert len(results) >= 3

    # 방배동 — 중밀도 구역 (브리즈버거·보나블랑제리·우주돈가스 등)
    def test_bangbae_500m_finds_places(self):
        results = agent.find_nearby(37.4880, 126.9950, 500)
        assert len(results) >= 3

    # 남산 정상 — 맛집 없는 곳
    def test_namsan_300m_no_results(self):
        results = agent.find_nearby(37.5512, 126.9882, 300)
        assert len(results) == 0

    # 경계 케이스
    def test_zero_radius_no_results(self):
        results = agent.find_nearby(37.4878, 126.9803, 0)
        assert len(results) == 0

    def test_result_has_required_keys(self):
        results = agent.find_nearby(37.4878, 126.9803, 500)
        assert len(results) > 0
        required = {"id", "name", "address", "memo", "lat", "lng", "distance_m"}
        for r in results:
            assert required <= r.keys()

    def test_distance_m_is_integer(self):
        results = agent.find_nearby(37.4878, 126.9803, 500)
        for r in results:
            assert isinstance(r["distance_m"], int)


# ── send_slack (mock 테스트) ──────────────────────────────────────────────────

SAMPLE_PLACES = [
    {
        "id": 32,
        "name": "시민소머리국밥",
        "address": "서울 동작구 동작대로27길 50 (사당동)",
        "memo": "",
        "lat": 37.48810382,
        "lng": 126.97954307,
        "distance_m": 120,
    },
    {
        "id": 33,
        "name": "파이공장",
        "address": "서울 동작구 동작대로27길 49 1층 (사당동)",
        "memo": "",
        "lat": 37.48792722,
        "lng": 126.97950241,
        "distance_m": 135,
    },
]


# ── parse_area (순수 단위 테스트) ─────────────────────────────────────────────

class TestParseArea:
    def test_nearby_keyword_returns_none(self):
        assert bot.parse_area("주변 맛집 알려줘") is None

    def test_geunche_keyword_returns_none(self):
        assert bot.parse_area("근처 맛집 알려줘") is None

    def test_area_extracted(self):
        assert bot.parse_area("홍대 맛집 알려줘") == "홍대"

    def test_area_with_suffix(self):
        assert bot.parse_area("강남 맛집 정보 알려줘") == "강남"

    def test_area_with_mention_stripped(self):
        assert bot.parse_area("<@U12345> 이태원 맛집 알려줘") == "이태원"

    def test_no_matzip_keyword_returns_none(self):
        assert bot.parse_area("안녕하세요") is None

    def test_matzip_only_returns_none(self):
        assert bot.parse_area("맛집 알려줘") is None


# ── geocode_area (mock 테스트) ─────────────────────────────────────────────────

class TestGeocodeArea:
    def test_returns_lat_lng_on_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"lat": "37.5571", "lon": "126.9258"}]
        with patch("requests.get", return_value=mock_resp):
            result = bot.geocode_area("홍대")
        assert result == pytest.approx((37.5571, 126.9258))

    def test_returns_none_on_empty_result(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        with patch("requests.get", return_value=mock_resp):
            result = bot.geocode_area("존재하지않는지역xyz")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("requests.get", side_effect=Exception("timeout")):
            result = bot.geocode_area("홍대")
        assert result is None


# ── build_blocks 테스트 ───────────────────────────────────────────────────────

class TestBuildBlocks:
    LAT, LNG = 37.5571, 126.9258

    def _places(self, n=1):
        return [
            {"id": i, "name": f"맛집{i}", "address": "서울 마포구", "memo": "",
             "lat": 37.556, "lng": 126.924, "distance_m": i * 50}
            for i in range(1, n + 1)
        ]

    def test_empty_places_shows_empty_message(self):
        blocks = bot.build_blocks([], "홍대", self.LAT, self.LNG)
        assert "없어요" in str(blocks)

    def test_place_name_appears_in_blocks(self):
        places = [{"id": 1, "name": "무야호랜드", "address": "서울 마포구", "memo": "",
                   "lat": 37.556, "lng": 126.924, "distance_m": 200}]
        blocks = bot.build_blocks(places, "홍대", self.LAT, self.LNG)
        assert any("무야호랜드" in str(b) for b in blocks)

    def test_radius_shown_in_subtitle(self):
        blocks = bot.build_blocks(self._places(), "홍대", self.LAT, self.LNG, radius_m=2000)
        assert "2km" in str(blocks)

    def test_morelink_contains_kakaomap(self):
        blocks = bot.build_blocks(self._places(), "홍대", self.LAT, self.LNG)
        assert "map.kakao.com" in str(blocks)
        assert "더보기" in str(blocks)

    def test_morelink_centered_on_area_coords(self):
        blocks = bot.build_blocks(self._places(), "홍대", self.LAT, self.LNG)
        blocks_str = str(blocks)
        assert str(self.LNG) in blocks_str
        assert str(self.LAT) in blocks_str

    def test_max_5_places_shown(self):
        blocks = bot.build_blocks(self._places(8), "테스트", self.LAT, self.LNG)
        place_blocks = [b for b in blocks if b.get("type") == "section" and "🚶" in str(b)]
        assert len(place_blocks) == 5

    def test_extra_count_shown_when_more_than_5(self):
        blocks = bot.build_blocks(self._places(7), "테스트", self.LAT, self.LNG)
        assert "외 2곳" in str(blocks)


class TestSendSlack:

    def _ok_response(self):
        resp = MagicMock()
        resp.json.return_value = {"ok": True}
        return resp

    def test_calls_slack_api(self):
        with patch("requests.post", return_value=self._ok_response()) as mock_post:
            agent.send_slack(SAMPLE_PLACES, "사당동")
            mock_post.assert_called_once()
            url = mock_post.call_args.args[0]
            assert "slack.com/api/chat.postMessage" in url

    def test_payload_contains_channel(self):
        with patch("requests.post", return_value=self._ok_response()) as mock_post:
            agent.send_slack(SAMPLE_PLACES, "사당동")
            payload = mock_post.call_args.kwargs["json"]
            assert "channel" in payload

    def test_blocks_contain_place_name(self):
        with patch("requests.post", return_value=self._ok_response()) as mock_post:
            agent.send_slack(SAMPLE_PLACES, "사당동")
            payload = mock_post.call_args.kwargs["json"]
            blocks_str = str(payload["blocks"])
            assert "시민소머리국밥" in blocks_str

    def test_blocks_contain_kakaomap_url(self):
        with patch("requests.post", return_value=self._ok_response()) as mock_post:
            agent.send_slack(SAMPLE_PLACES, "사당동")
            payload = mock_post.call_args.kwargs["json"]
            blocks_str = str(payload["blocks"])
            assert "map.kakao.com" in blocks_str

    def test_error_response_is_logged(self, caplog):
        resp = MagicMock()
        resp.json.return_value = {"ok": False, "error": "channel_not_found"}
        with patch("requests.post", return_value=resp):
            with caplog.at_level(logging.ERROR, logger="agent"):
                agent.send_slack(SAMPLE_PLACES, "사당동")
        assert "channel_not_found" in caplog.text

    def test_max_5_places_in_blocks(self):
        """6개 이상 입력해도 블록은 최대 8개 (헤더+위치+구분선+장소5개)"""
        many = [
            {"id": i, "name": f"맛집{i}", "address": "주소", "memo": "",
             "lat": 37.5, "lng": 127.0, "distance_m": i * 50}
            for i in range(8)
        ]
        with patch("requests.post", return_value=self._ok_response()) as mock_post:
            agent.send_slack(many, "테스트")
            payload = mock_post.call_args.kwargs["json"]
            assert len(payload["blocks"]) <= 8  # header + section + divider + 5 places
