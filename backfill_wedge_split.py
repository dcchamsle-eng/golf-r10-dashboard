"""기존 data/r10_history.json 세션에 웨지 샷 분류(wedge_split)를 채워 넣는 1회성 스크립트.

history에는 세션 요약값만 있어 golf/*.csv 원본을 다시 파싱한다. 세션 서명(날짜+시작시각)과
총 샷수가 기록과 일치하는 CSV에서만 wedge_split 필드를 추가하고, 다른 값은 건드리지 않는다.
WEDGE_REFERENCE(기준 거리표)를 바꾼 뒤 다시 실행해도 된다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from r10_parse import parse_session
from update_data import GOLF_DIR, FILE_PATTERN, load_history, save_history, session_id

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    history = load_history()
    filled = 0
    for path in sorted(GOLF_DIR.glob("*.csv")):
        if not FILE_PATTERN.match(path.name):
            continue
        session = parse_session(path)
        if session is None:
            continue
        existing = history["sessions"].get(session_id(session))
        if existing is None or existing["total_shots"] != session["total_shots"]:
            continue
        for club, cs in session["clubs"].items():
            if "wedge_split" in cs and club in existing["clubs"]:
                existing["clubs"][club]["wedge_split"] = cs["wedge_split"]
                filled += 1

    save_history(history)
    missing = [
        sid for sid, s in history["sessions"].items()
        if any(c in s["clubs"] and "wedge_split" not in s["clubs"][c]
               for c in ("피칭웨지", "갭웨지", "샌드웨지", "로브웨지"))
    ]
    print(f"[완료] 웨지 분류 {filled}건 채움" + (f" / 원본 CSV를 못 찾은 세션: {missing}" if missing else ""))


if __name__ == "__main__":
    main()
