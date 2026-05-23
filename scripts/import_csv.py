#!/usr/bin/env python3
"""data/ 폴더의 모든 CSV를 PostgreSQL에 import하는 스크립트"""

import csv
import glob
import os
import re
import psycopg2


def _valid_filename(path: str) -> bool:
    """한글(가-힣)·영문·숫자·공백만 있는 파일명인지 확인 (깨진 파일명 제외)."""
    name = os.path.basename(path)
    return bool(re.fullmatch(r"[a-zA-Z0-9\s\-_가-힣]+\.csv", name))

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://hermes:hermes1234@localhost:5432/hermes",
)
DATA_DIR = os.environ.get("DATA_DIR", "data")


def import_all():
    csv_files = sorted(f for f in glob.glob(os.path.join(DATA_DIR, "*.csv")) if _valid_filename(f))
    if not csv_files:
        print(f"[WARN] {DATA_DIR}/ 에서 CSV 파일을 찾을 수 없습니다.")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("DELETE FROM matzip")

    total = 0
    for csv_path in csv_files:
        filename = os.path.basename(csv_path)
        inserted = 0
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    cur.execute(
                        """
                        INSERT INTO matzip (seq, name, address, memo, lat, lng, registered_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            int(row["순번"]),
                            row["이름"].strip(),
                            row["주소"].strip(),
                            row["메모"].strip(),
                            float(row["위도"]),
                            float(row["경도"]),
                            row["등록일"].strip() or None,
                        ),
                    )
                    inserted += 1
                except Exception as e:
                    print(f"  [SKIP] {row.get('이름')}: {e}")
        print(f"  {filename}: {inserted}개")
        total += inserted

    conn.commit()
    cur.close()
    conn.close()
    print(f"✅ 총 {total}개 import 완료")


if __name__ == "__main__":
    import_all()
