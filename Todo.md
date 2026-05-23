# Todo

## Cloud Run 배포

- [ ] `app/Dockerfile` Cloud Run 호환 확인 (포트 8080 불필요 — Socket Mode라 HTTP 서버 없음)
- [ ] GCP 프로젝트 설정 및 `gcloud` CLI 인증
- [ ] Artifact Registry에 Docker 이미지 push
- [ ] Cloud Run 서비스 생성 (환경변수로 `.env` 값 주입)
- [ ] PostgreSQL → Cloud SQL (PostgreSQL 15 + PostGIS) 또는 외부 DB 연결
- [ ] DB `DATABASE_URL` Cloud SQL 연결 문자열로 교체
- [ ] 항상 실행 상태 유지 설정 (최소 인스턴스 1 — WebSocket 유지 필요)


