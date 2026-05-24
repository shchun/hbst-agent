# Hermes 개인비서 — 지원 메시지 예시

Slack DM 또는 멘션(`@Hermes ...`)으로 보낼 수 있는 메시지 목록.
각 메시지는 Hermes가 내부적으로 어떤 MCP 도구를 호출하는지와 함께 정리한다.

> 도구는 두 MCP 서버에 나뉘어 있다 — **vault**(노트 검색/기록), **matzip**(맛집).

---

## 1. 노트 검색 (vault · `search_notes`)

저장된 옵시디언 노트를 키워드로 찾는다. 결과에 **출처 파일 경로**가 함께 붙는다.
`.hermesignore`에 등록된 `Private/` 폴더는 검색되지 않는다.

| 메시지 예시 | 동작 |
|------------|------|
| "카프카 실습 정리한 노트 있었나?" | `search_notes("카프카 실습")` |
| "예전에 BigQuery 관련해서 적어둔 거 찾아줘" | `search_notes("BigQuery")` |
| "MCP 관련 노트 보여줘" | `search_notes("MCP")` |
| "레시피 폴더에서 김밥 찾아줘" | `search_notes("김밥", folder="레시피")` |
| "도커 컴포즈 설정 메모해둔 거 있어?" | `search_notes("docker compose")` |

---

## 2. 노트 기록 (vault · `capture_note`)

새 메모를 볼트 `hermes_inbox/`에 **새 파일로** 저장한다 (기존 노트는 수정하지 않음).
"기록해줘 / 메모해둬 / 저장해줘" 같은 표현이 트리거.

| 메시지 예시 | 동작 |
|------------|------|
| "오늘 간 OO 파스타집 괜찮았어. 기록해둬" | `capture_note(title="OO 파스타집", content=..., source="slack", source_text=원문)` |
| "내일 3시 치과 예약, 메모해줘" | `capture_note(title="치과 예약", content=...)` |
| "이 아이디어 저장해줘: 몰입도 측정 캠" | `capture_note(title="몰입도 측정 캠 아이디어", content=...)` |
| "회의 결론만 노트로 남겨줘: ..." | `capture_note(title=..., content=...)` |

생성되는 노트 형식:
```markdown
---
created: 2026-05-24T14:32:05+09:00
source: slack
type: capture
status: unprocessed
tags: [hermes/inbox]
---

# OO 파스타집

오늘 간 OO 파스타집 괜찮았어.

> 원문(slack): "오늘 간 OO 파스타집 괜찮았어. 기록해둬"
```

---

## 3. 맛집 검색 — 지역명 (matzip · `geocode_area` → `find_nearby`)

| 메시지 예시 | 동작 |
|------------|------|
| "홍대 맛집 알려줘" | `geocode_area("홍대")` → `find_nearby(lat, lng)` |
| "강남역 근처 저장한 곳 있어?" | `geocode_area("강남역")` → `find_nearby` |
| "사당동에 뭐 있더라?" | `geocode_area("사당동")` → `find_nearby` |

---

## 4. 맛집 검색 — 현재 위치 (matzip · `get_current_location` → `find_nearby`)

"주변 / 근처" 표현이면 현재 위치 기준으로 찾는다.

| 메시지 예시 | 동작 |
|------------|------|
| "주변 맛집 있어?" | `get_current_location()` → `find_nearby` |
| "근처에 저장한 곳 알려줘" | `get_current_location()` → `find_nearby` |
| "지금 내 위치 어디야?" | `get_current_location()` (→ `reverse_geocode`로 지역명) |

---

## 5. 맛집 둘러보기 (matzip · `get_area_clusters`)

지역 언급 없이 맛집을 물으면 밀집 지역을 알려준다.

| 메시지 예시 | 동작 |
|------------|------|
| "맛집 알려줘" | `get_area_clusters()` → "이 지역들에 많아요: ..." |
| "어디에 저장한 곳이 많아?" | `get_area_clusters()` |

---

## 6. 프로액티브 알림 (matzip · `check_and_notify`)

사용자가 보내는 메시지가 아니라 **cron으로 주기 실행**된다. 현재 위치 반경
(`PROXIMITY_RADIUS_METERS`, 기본 500m) 안에 저장된 맛집이 있으면 Slack으로 먼저 알림.
이미 알린 곳은 제외(프로세스 수명 동안 기억).

---

## 응답 규칙 (SOUL.md 기준)

- 맛집 거리는 미터(m) + 도보 시간(80m/분), 카카오맵 링크 포함.
- 노트 검색 결과엔 항상 출처(파일 경로) 명시.
- 추측하지 않고 볼트·DB에서 근거를 찾아 답하며, 없으면 없다고 한다.
- 한국어로만 응답.
