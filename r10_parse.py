"""Garmin R10 드라이빙레인지 CSV 파싱 + 클럽별 통계 집계.

golf/*.csv (Garmin Golf 앱 "DrivingRange-...csv" export)를 읽어 세션 단위로 그룹화하고,
클럽별 평균/표준편차 및 훈련일지 종료 규칙 위반 구간을 계산한다. 표준 라이브러리만 사용.
"""

import csv
import statistics as st
from pathlib import Path

COLS = [
    "date", "player", "club_name", "brand", "club_type", "club_speed", "attack_angle",
    "club_path", "club_face", "face_path", "ball_speed", "smash", "launch_angle",
    "launch_dir", "backspin", "sidespin", "spin_rate", "spin_type", "spin_axis",
    "apex", "carry", "carry_dev_angle", "carry_dev_dist", "total", "total_dev_angle",
    "total_dev_dist", "target_total", "target_apex", "note", "tag", "air_density",
    "temp", "pressure", "humidity", "backstroke_len", "target_backswing_t",
    "target_downswing_t", "forwardstroke_len", "backswing_t", "downswing_t",
    "target_tempo", "swing_tempo",
]

NUMERIC = {
    "club_speed", "attack_angle", "club_path", "club_face", "face_path", "ball_speed",
    "smash", "launch_angle", "launch_dir", "backspin", "sidespin", "spin_rate", "spin_axis",
    "apex", "carry", "carry_dev_angle", "carry_dev_dist", "total", "total_dev_angle",
    "total_dev_dist", "backswing_t", "downswing_t", "swing_tempo",
}

# 훈련일지 0708 기준
DRIVER_PATH_TARGET = (2, 6)       # 헤드 경로 정착 구간 (R)
DRIVER_BACKSPIN_TARGET = (2200, 2800)
IRON_FACE_PATH_GAP_TARGET = (5, 7)  # 장기 수렴 목표 (관찰만, 개입 안 함)
IRON_FACE_PATH_GAP_BASELINE = (8, 10)  # 0708 기준선
DRIVER_REPRO_THRESHOLD = 0.6  # "10구 중 6개 이상 정착구간" = 감각 생존 판정 기준

# 클럽별 현실적 헤드스피드 상한(㎧) - 이보다 높으면 센서 오류(볼스피드를 클럽스피드로 잘못 잡는 등)로 간주.
# 이 골퍼의 22세션 실측 최고치(드라이버 46.7)에 여유를 두고 설정 - 필요시 상향 조정.
CLUB_SPEED_CEILING = {
    "드라이버": 50, "3 우드": 48, "5 우드": 46,
    "3 하이브리드": 45, "4 하이브리드": 45, "5 하이브리드": 44,
    "3 아이언": 44, "4 아이언": 43, "5 아이언": 42, "6 아이언": 41,
    "7 아이언": 40, "8 아이언": 39, "9 아이언": 38,
    "피칭웨지": 37, "갭웨지": 36, "샌드웨지": 34, "로브웨지": 32,
}
DEFAULT_CLUB_SPEED_CEILING = 50

# 웨지 기준 거리표(사용자 제공, 2026-09-24). P=피칭웨지, 50도=갭웨지, 56도=샌드웨지, 60도=로브웨지.
# full: 풀샷 캐리 범위(m), control: 컨트롤샷 캐리(m, 없으면 None).
# 짧은 웨지 샷은 미스샷이 아니라 어프로치 연습이므로 40% 미스샷 필터와 별개로 샷 단위 분류한다.
WEDGE_REFERENCE = {
    "피칭웨지": {"full": (115, 115), "control": None},
    "갭웨지": {"full": (100, 105), "control": None},
    "샌드웨지": {"full": (85, 90), "control": 75},
    "로브웨지": {"full": (65, 65), "control": None},
}
WEDGE_CONTROL_MARGIN = 10   # 컨트롤 하한 = 컨트롤 기준 - 10m
WEDGE_FULL_RATIO = 0.8      # 컨트롤 기준이 없으면 풀샷 하한 = 풀샷 기준 하단의 80%
WEDGE_OUTLIER_RATIO = 1.3   # 풀샷 기준 상단의 130% 초과 = 클럽 태깅 오류로 보고 제외


def wedge_bands(club):
    """(풀샷 하한, 컨트롤 하한 또는 None, 이상치 상한). 풀샷 하한은 컨트롤 기준이 있으면
    컨트롤과 풀샷 하단의 중간, 없으면 풀샷 하단의 80%."""
    ref = WEDGE_REFERENCE[club]
    full_lo, full_hi = ref["full"]
    if ref["control"] is not None:
        return (ref["control"] + full_lo) / 2, ref["control"] - WEDGE_CONTROL_MARGIN, full_hi * WEDGE_OUTLIER_RATIO
    return full_lo * WEDGE_FULL_RATIO, None, full_hi * WEDGE_OUTLIER_RATIO


def wedge_split(club, all_shots):
    """웨지 샷을 풀샷/컨트롤/어프로치/이상치로 분류. 세션 간 합산 중앙값을 낼 수 있게 캐리 목록을 남긴다."""
    full_min, control_min, outlier_max = wedge_bands(club)
    full, control, n_approach, n_outlier = [], [], 0, 0
    for s in all_shots:
        c = s["carry"]
        if c is None:
            continue
        if c > outlier_max:
            n_outlier += 1
        elif c >= full_min:
            full.append(round(c, 1))
        elif control_min is not None and c >= control_min:
            control.append(round(c, 1))
        else:
            n_approach += 1
    return {"full": full, "control": control, "n_approach": n_approach, "n_outlier": n_outlier}


def _to_float(v):
    v = v.strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_file(path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader)  # header
        next(reader)  # units
        for raw in reader:
            if not raw or all(c.strip() == "" for c in raw):
                continue
            rec = {}
            for i, col in enumerate(COLS):
                val = raw[i] if i < len(raw) else ""
                rec[col] = _to_float(val) if col in NUMERIC else val.strip()
            rows.append(rec)
    return rows


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(st.mean(vals), 2) if vals else None


def _stdev(vals):
    vals = [v for v in vals if v is not None]
    return round(st.stdev(vals), 2) if len(vals) > 1 else None


def sanitize_club_speed(shots, club_type):
    """club_speed가 이 클럽의 현실적 상한을 넘으면 센서 오류로 보고 club_speed/smash만 무효화한다
    (다른 필드는 정상인 경우가 많아 그대로 둔다). n_sanitized를 함께 반환."""
    ceiling = CLUB_SPEED_CEILING.get(club_type, DEFAULT_CLUB_SPEED_CEILING)
    out = []
    n_sanitized = 0
    for s in shots:
        if s["club_speed"] is not None and s["club_speed"] > ceiling:
            s = dict(s)
            s["club_speed"] = None
            s["smash"] = None
            n_sanitized += 1
        out.append(s)
    return out, n_sanitized


def club_stats(all_shots):
    carries = [s["carry"] for s in all_shots if s["carry"] is not None]
    median_carry = st.median(carries) if carries else 0
    # 클럽별 캐리 중앙값의 40% 미만 샷은 미스샷/워밍업으로 간주 (휴리스틱 - CSV에 확인 샷 표기가 없음)
    clean = [s for s in all_shots if s["carry"] is None or s["carry"] >= median_carry * 0.4]
    mishits = [s for s in all_shots if s not in clean]
    shots = clean if clean else all_shots

    face_path_gaps = [
        (s["club_face"] - s["club_path"])
        for s in shots if s["club_face"] is not None and s["club_path"] is not None
    ]
    carry_list = [s["carry"] for s in shots if s["carry"] is not None]
    # R10 레이더가 클럽 데이터(경로/페이스)를 못 잡는 샷이 흔함(전체 이력 기준 드라이버 약 50%) -
    # carry 기반 n_clean과 별도로 실제 경로 표본 수를 노출해 재현율/평균의 신뢰도를 판단할 수 있게 한다.
    n_path_valid = sum(1 for s in shots if s["club_path"] is not None)

    return {
        "n": len(all_shots),
        "n_clean": len(clean),
        "n_mishit": len(mishits),
        "n_path_valid": n_path_valid,
        "club_speed_avg": _mean([s["club_speed"] for s in shots]),
        "attack_angle_avg": _mean([s["attack_angle"] for s in shots]),
        "club_path_avg": _mean([s["club_path"] for s in shots]),
        "club_path_std": _stdev([s["club_path"] for s in shots]),
        "club_face_avg": _mean([s["club_face"] for s in shots]),
        "face_path_gap_avg": _mean(face_path_gaps),
        "ball_speed_avg": _mean([s["ball_speed"] for s in shots]),
        "smash_avg": _mean([s["smash"] for s in shots]),
        "launch_angle_avg": _mean([s["launch_angle"] for s in shots]),
        "backspin_avg": _mean([s["backspin"] for s in shots]),
        "sidespin_avg": _mean([s["sidespin"] for s in shots]),
        "carry_avg": _mean([s["carry"] for s in shots]),
        "carry_median": round(st.median(carry_list), 2) if carry_list else None,
        "carry_std": _stdev([s["carry"] for s in shots]),
        "carry_dev_dist_avg_abs": _mean(
            [abs(s["carry_dev_dist"]) if s["carry_dev_dist"] is not None else None for s in shots]
        ),
        "total_avg": _mean([s["total"] for s in shots]),
        "swing_tempo_avg": _mean([s["swing_tempo"] for s in shots]),
        "_clean_shots": clean,
    }


def path_in_target_rate(shots, target):
    """target 구간(예: 헤드 경로 2~6R) 안에 든 샷의 비율. 표본 부족이면 None.
    "감이 아니라 숫자" 원칙 - 평균이 구간 안이어도 샷별 재현율은 낮을 수 있다."""
    lo, hi = target
    paths = [s["club_path"] for s in shots if s["club_path"] is not None]
    if not paths:
        return None
    hits = sum(1 for p in paths if lo <= p <= hi)
    return round(hits / len(paths), 3)


def termination_flags(shots):
    """훈련일지 종료 규칙: 타구각 +2 미만 또는 캐리 (세션평균-10m) 이상 하회, 3연속."""
    carries = [s["carry"] for s in shots if s["carry"] is not None]
    if not carries:
        return []
    avg_carry = st.mean(carries)
    flags = []
    streak = 0
    streak_start = None
    for i, s in enumerate(shots):
        bad = (s["attack_angle"] is not None and s["attack_angle"] < 2) or \
              (s["carry"] is not None and s["carry"] < avg_carry - 10)
        if bad:
            if streak == 0:
                streak_start = i
            streak += 1
            if streak == 3:
                flags.append({
                    "start_time": shots[streak_start]["date"],
                    "end_time": s["date"],
                    "len": streak,
                })
            elif streak > 3:
                flags[-1]["end_time"] = s["date"]
                flags[-1]["len"] = streak
        else:
            streak = 0
    return flags


def parse_session(csv_path):
    """CSV 한 파일(=한 세션) -> {date, total_shots, time_start, time_end, source_file, clubs:{...}}"""
    rows = parse_file(csv_path)
    if not rows:
        return None

    by_club = {}
    for r in rows:
        by_club.setdefault(r["club_type"], []).append(r)

    session_date = rows[0]["date"].split(".")[0:3]
    date_str = f"{session_date[0].strip()}-{int(session_date[1]):02d}-{int(session_date[2]):02d}"

    clubs = {}
    for club, shots in by_club.items():
        shots, n_speed_sanitized = sanitize_club_speed(shots, club)
        cs = club_stats(shots)
        cs["n_speed_sanitized"] = n_speed_sanitized
        if club == "드라이버":
            cs["termination_flags"] = termination_flags(cs["_clean_shots"])
            cs["path_in_target_rate"] = path_in_target_rate(cs["_clean_shots"], DRIVER_PATH_TARGET)
        if club in WEDGE_REFERENCE:
            cs["wedge_split"] = wedge_split(club, shots)
        del cs["_clean_shots"]
        clubs[club] = cs

    return {
        "date": date_str,
        "total_shots": len(rows),
        "time_start": rows[0]["date"],
        "time_end": rows[-1]["date"],
        "source_file": Path(csv_path).name,
        "clubs": clubs,
    }
