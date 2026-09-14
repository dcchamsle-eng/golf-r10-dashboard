"""golf/*.csv 에 새로 놓인 Garmin R10 export를 찾아 data/r10_history.json에 누적한다.

이미 처리한 파일(processed_files)은 다시 파싱하지 않는다. 같은 세션이 재-export되어
파일명만 다르게 다시 놓인 경우엔 세션 서명(날짜+시작시각)으로 식별해서 샷 수가 늘었을 때만 갱신한다.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from r10_parse import parse_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GOLF_DIR = Path(r"C:\Users\USER\golf")
HISTORY_FILE = Path(__file__).resolve().parent / "data" / "r10_history.json"
FILE_PATTERN = re.compile(r"^DrivingRange-.*\.csv$", re.IGNORECASE)


def load_history():
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return {"sessions": {}, "processed_files": []}


def save_history(history):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def session_id(session):
    return f"{session['date']}_{session['time_start']}"


def main():
    history = load_history()
    processed = set(history["processed_files"])

    candidates = [p for p in GOLF_DIR.glob("*.csv") if FILE_PATTERN.match(p.name)]
    new_files = [p for p in candidates if p.name not in processed]

    if not new_files:
        print("[정보] 새로 처리할 CSV 없음")
        return

    added, updated, skipped = 0, 0, 0
    for path in new_files:
        session = parse_session(path)
        if session is None:
            print(f"[건너뜀] {path.name}: 빈 파일")
            history["processed_files"].append(path.name)
            continue

        sid = session_id(session)
        existing = history["sessions"].get(sid)
        if existing is None:
            history["sessions"][sid] = session
            added += 1
            print(f"[추가] {sid} ({session['total_shots']}구, {path.name})")
        elif session["total_shots"] > existing["total_shots"]:
            history["sessions"][sid] = session
            updated += 1
            print(f"[갱신] {sid}: {existing['total_shots']}구 -> {session['total_shots']}구 ({path.name})")
        else:
            skipped += 1
            print(f"[건너뜀] {sid}: 이미 동일하거나 더 많은 샷 데이터 보유 ({path.name})")

        history["processed_files"].append(path.name)

    save_history(history)
    print(f"[완료] 추가 {added} / 갱신 {updated} / 건너뜀 {skipped} / 누적 세션 {len(history['sessions'])}개")


if __name__ == "__main__":
    main()
