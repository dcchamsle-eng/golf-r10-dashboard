"""data/r10_history.json 누적 데이터로 index.html 대시보드를 생성한다.

update_data.py 다음 단계로 실행. golf_dashboard/generate_app.py와 동일한 PBKDF2-HMAC-SHA256 +
HMAC 키스트림 XOR 암호화로 본문 HTML을 통째로 암호화해 심는다(React/Babel 불필요 - 순수 정적 HTML이라
비밀번호 입력 후 바로 innerHTML로 주입). 표준 라이브러리만 사용한다.
"""

import base64
import hashlib
import hmac
import json
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chart_svg import line_chart, bar_chart
from r10_parse import (
    DRIVER_PATH_TARGET, DRIVER_BACKSPIN_TARGET, IRON_FACE_PATH_GAP_TARGET,
    IRON_FACE_PATH_GAP_BASELINE, DRIVER_REPRO_THRESHOLD,
    WEDGE_REFERENCE, WEDGE_CONTROL_MARGIN, WEDGE_FULL_RATIO, WEDGE_OUTLIER_RATIO, wedge_bands,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
HISTORY_FILE = BASE_DIR / "data" / "r10_history.json"
OUTPUT_HTML = BASE_DIR / "index.html"
PBKDF2_ITERATIONS = 200_000
THEME_COLOR = "#0E3B2E"
RECENT_N = 20  # 대시보드에 표시할 최근 세션 수 (전체 이력은 data/r10_history.json에 그대로 누적)

MISHIT_THRESHOLD_NOTE = (
    "이상치 처리: 클럽별 캐리 중앙값의 40% 미만 샷은 미스샷/워밍업으로 간주해 평균 계산에서 제외"
    "(\"클린 샷\"), 전체 샷수와 병기. CSV에 \"확인 샷\"(의도적 저출력) 표기가 없어 이 필터는 휴리스틱."
)


def load_env():
    env = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


# ---------------------------------------------------------------------------
# 대시보드 본문 HTML 생성
# ---------------------------------------------------------------------------

def fmt_ko_date(d):
    y, m, day = d.split("-")
    return f"{int(m)}/{int(day)}"


def fmt_lr(v):
    """헤드 경로 표기 통일: R10 값은 +가 R(인투아웃), -가 L(아웃투인). 부호 대신 방향 글자만 쓴다."""
    if v is None:
        return "-"
    if round(abs(v), 1) == 0:
        return "0.0"
    return f"{abs(v):.1f}{'R' if v >= 0 else 'L'}"


def status_badge(kind, text):
    return f'<span class="badge {kind}"><span class="dot"></span>{text}</span>'


def build_overview_tiles(sessions):
    total_shots = sum(s["total_shots"] for s in sessions)
    total_clean = sum(
        sum(c.get("n_clean", 0) for c in s["clubs"].values()) for s in sessions
    )
    dates = sorted(s["date"] for s in sessions)
    date_range = f"{dates[0]} ~ {dates[-1]}" if len(dates) > 1 else dates[0]

    driver_sessions = [s for s in sessions if "드라이버" in s["clubs"]]
    if driver_sessions:
        latest = max(driver_sessions, key=lambda s: s["date"])
        path_txt = fmt_lr(latest["clubs"]["드라이버"]["club_path_avg"])
        latest_flags = len(latest["clubs"]["드라이버"].get("termination_flags", []))
        latest_date = fmt_ko_date(latest["date"])
    else:
        path_txt = "-"
        latest_flags, latest_date = 0, "-"

    flag_count = sum(
        len(s["clubs"].get("드라이버", {}).get("termination_flags", [])) for s in sessions
    )

    return f"""
  <div class="tile-row">
    <div class="tile"><div class="label">세션</div><div class="value">{len(sessions)}회</div><div class="sub">{date_range}</div></div>
    <div class="tile"><div class="label">총 샷</div><div class="value">{total_shots}구</div><div class="sub">클린 샷 {total_clean}구</div></div>
    <div class="tile"><div class="label">최근 드라이버 헤드경로</div><div class="value">{path_txt}</div><div class="sub">정착구간 {DRIVER_PATH_TARGET[0]}~{DRIVER_PATH_TARGET[1]}R</div></div>
    <div class="tile"><div class="label">후반 피로 신호 (최근 세션 {latest_date})</div><div class="value">{latest_flags}건</div><div class="sub">종료 조건(3연속) · 최근 {len(sessions)}회 누적 {flag_count}건</div></div>
  </div>"""


def build_driver_trend(sessions):
    driver_sessions = sorted(
        [s for s in sessions if "드라이버" in s["clubs"]], key=lambda s: (s["date"], s["time_start"])
    )
    if not driver_sessions:
        return "<p class='section-desc'>드라이버 데이터가 있는 세션이 아직 없습니다.</p>"

    def note_for(s, club):
        n = club.get("n_clean", 0)
        return "표본부족" if n < 5 else ""

    def note_for_path(s, club):
        # R10이 클럽 경로/페이스 데이터를 못 잡는 샷이 흔함(약 절반) - 경로 기반 통계는
        # n_clean이 아니라 실제 경로 표본 수(n_path_valid) 기준으로 표본부족을 판정한다.
        # 원인을 둘로 구분: 그날 총 스윙 자체가 적었던 경우(잘 맞아서 일찍 끝낸 세션 등)와,
        # 스윙은 충분했는데 유독 결측률이 높았던 경우(센서가 그날따라 못 잡은 경우)는
        # 의미가 다르므로 표기를 나눈다.
        n = club.get("n_path_valid", 0)
        if n >= 5:
            return ""
        n_clean = club.get("n_clean", 0)
        if n_clean < 5:
            return f"총 샷수 적음({n_clean}구)"
        return f"경로결측률 높음(표본 {n}개)"

    path_pts = [
        (fmt_ko_date(s["date"]), s["clubs"]["드라이버"]["club_path_avg"], note_for_path(s, s["clubs"]["드라이버"]))
        for s in driver_sessions
    ]
    backspin_pts = [
        (fmt_ko_date(s["date"]), s["clubs"]["드라이버"]["backspin_avg"], note_for(s, s["clubs"]["드라이버"]))
        for s in driver_sessions
    ]
    carry_pts = [
        (fmt_ko_date(s["date"]), s["clubs"]["드라이버"]["carry_avg"], note_for(s, s["clubs"]["드라이버"]))
        for s in driver_sessions
    ]
    std_pts = [
        (fmt_ko_date(s["date"]), s["clubs"]["드라이버"]["club_path_std"], note_for_path(s, s["clubs"]["드라이버"]))
        for s in driver_sessions
    ]
    # 재현율: 막대마다 "구간 내 샷수/경로표본수"를 붙이고, 세션당 표본이 작아 들쭉날쭉하므로
    # 최근 3세션의 경로표본을 합쳐 계산한 재현율을 보조 라인으로 겹친다(합산 표본 5개 미만이면 생략).
    def path_hits(club):
        rate, n_valid = club.get("path_in_target_rate"), club.get("n_path_valid", 0)
        if rate is None or not n_valid:
            return 0, 0
        return round(rate * n_valid), n_valid

    repro_pts = []
    for i, s in enumerate(driver_sessions):
        club = s["clubs"]["드라이버"]
        hits, n_valid = path_hits(club)
        window = [path_hits(w["clubs"]["드라이버"]) for w in driver_sessions[max(0, i - 2):i + 1]]
        w_hits, w_n = sum(h for h, _ in window), sum(n for _, n in window)
        pooled = w_hits / w_n * 100 if w_n >= 5 else None
        note = f"{hits}/{n_valid}" + (" 참고용" if n_valid < 5 else "")
        repro_pts.append((
            fmt_ko_date(s["date"]),
            club["path_in_target_rate"] * 100 if club.get("path_in_target_rate") is not None else None,
            note,
            pooled,
        ))
    speed_pts = [
        (fmt_ko_date(s["date"]), s["clubs"]["드라이버"]["club_speed_avg"], note_for(s, s["clubs"]["드라이버"]))
        for s in driver_sessions
    ]
    ballspeed_pts = [
        (fmt_ko_date(s["date"]), s["clubs"]["드라이버"]["ball_speed_avg"], note_for(s, s["clubs"]["드라이버"]))
        for s in driver_sessions
    ]
    tempo_pts = [
        (fmt_ko_date(s["date"]), s["clubs"]["드라이버"]["swing_tempo_avg"], note_for(s, s["clubs"]["드라이버"]))
        for s in driver_sessions
    ]

    path_vals = [v for _, v, _ in path_pts if v is not None]
    path_min, path_max = min(path_vals + [-2]) - 4, max(path_vals + [8]) + 4

    backspin_vals = [v for _, v, _ in backspin_pts if v is not None]
    bs_min, bs_max = min(backspin_vals + [2000]) - 300, max(backspin_vals + [2900]) + 300

    carry_vals = [v for _, v, _ in carry_pts if v is not None]
    carry_min, carry_max = min(carry_vals) - 15, max(carry_vals) + 15

    std_vals = [v for _, v, _ in std_pts if v is not None]
    std_max = max(std_vals + [15]) + 3

    speed_vals = [v for _, v, _ in speed_pts if v is not None]
    speed_min, speed_max = min(speed_vals) - 3, max(speed_vals) + 3

    ballspeed_vals = [v for _, v, _ in ballspeed_pts if v is not None]
    bspeed_min, bspeed_max = min(ballspeed_vals) - 3, max(ballspeed_vals) + 3

    tempo_vals = [v for _, v, _ in tempo_pts if v is not None]
    tempo_min, tempo_max = max(0, min(tempo_vals) - 1), max(tempo_vals) + 1

    path_chart = line_chart(
        path_pts, path_min, path_max, band=DRIVER_PATH_TARGET,
        band_label=f"목표 {DRIVER_PATH_TARGET[0]}~{DRIVER_PATH_TARGET[1]}R",
        value_fmt=fmt_lr, aria_label="드라이버 헤드 경로 평균 추이",
    )
    backspin_chart = line_chart(
        backspin_pts, bs_min, bs_max, band=DRIVER_BACKSPIN_TARGET,
        band_label=f"목표 {DRIVER_BACKSPIN_TARGET[0]}~{DRIVER_BACKSPIN_TARGET[1]}",
        value_fmt="{:.0f}", aria_label="드라이버 백스핀 평균 추이",
    )
    carry_chart = line_chart(
        carry_pts, carry_min, carry_max,
        value_fmt="{:.0f}m", aria_label="드라이버 캐리 평균 추이",
    )
    std_chart = bar_chart(
        std_pts, 0, std_max,
        value_fmt="{:.1f}°", aria_label="드라이버 헤드 경로 표준편차 추이",
    )
    repro_chart = bar_chart(
        repro_pts, 0, 100,
        value_fmt="{:.0f}%", aria_label="드라이버 헤드 경로 정착구간 재현율 추이",
        ref_line=(DRIVER_REPRO_THRESHOLD * 100, f"감각 생존 기준 {int(DRIVER_REPRO_THRESHOLD*100)}%"),
        label_all=True,
    )
    speed_chart = line_chart(
        speed_pts, speed_min, speed_max,
        value_fmt="{:.1f}㎧", aria_label="드라이버 클럽 헤드스피드 평균 추이",
    )
    ballspeed_chart = line_chart(
        ballspeed_pts, bspeed_min, bspeed_max,
        value_fmt="{:.1f}㎧", aria_label="드라이버 볼스피드 평균 추이",
    )
    tempo_chart = line_chart(
        tempo_pts, tempo_min, tempo_max,
        value_fmt="{:.2f}", aria_label="드라이버 스윙 템포(백스윙:다운스윙 비율) 평균 추이",
    )

    badges = []
    in_range = [v for v in path_vals if DRIVER_PATH_TARGET[0] <= v <= DRIVER_PATH_TARGET[1]]
    badges.append(status_badge(
        "good" if len(in_range) == len(path_vals) else "warning",
        f"정착구간(2~6R) 내 세션 {len(in_range)}/{len(path_vals)}",
    ))
    low_n = [fmt_ko_date(s["date"]) for s in driver_sessions if s["clubs"]["드라이버"].get("n_clean", 0) < 5]
    if low_n:
        badges.append(status_badge("warning", f"총 샷수 적은 세션(잘 맞아 일찍 끝냈을 수 있음): {', '.join(low_n)} (n&lt;5)"))
    low_path_high_n = [
        fmt_ko_date(s["date"]) for s in driver_sessions
        if s["clubs"]["드라이버"].get("n_path_valid", 0) < 5 and s["clubs"]["드라이버"].get("n_clean", 0) >= 5
    ]
    if low_path_high_n:
        badges.append(status_badge(
            "warning",
            f"경로결측률 높은 세션(샷수는 충분했으나 R10이 경로를 못 잡음): {', '.join(low_path_high_n)}",
        ))

    repro_vals = [p[1] for p in repro_pts if p[1] is not None]
    below_thresh = [v for v in repro_vals if v < DRIVER_REPRO_THRESHOLD * 100]
    repro_badges = [status_badge(
        "good" if not below_thresh else "warning",
        f"재현율 {int(DRIVER_REPRO_THRESHOLD*100)}% 미만 세션 {len(below_thresh)}/{len(repro_vals)}"
        f" — 평균이 구간 안이어도 샷별 재현율은 낮을 수 있음",
    )]
    latest_pooled = next((p[3] for p in reversed(repro_pts) if p[3] is not None), None)
    if latest_pooled is not None:
        repro_badges.append(status_badge(
            "good" if latest_pooled >= DRIVER_REPRO_THRESHOLD * 100 else "warning",
            f"최근 3세션 합산 재현율 {latest_pooled:.0f}%",
        ))

    return f"""
    <div class="card">
      <h3>드라이버 — 헤드 경로 (정착구간 {DRIVER_PATH_TARGET[0]}~{DRIVER_PATH_TARGET[1]}R)</h3>
      {path_chart}
      <div class="badge-row">{''.join(badges)}</div>
    </div>
    <div class="card">
      <h3>드라이버 — 정착구간 재현율 (샷별로 몇 %가 2~6R 안에 들어왔는지)</h3>
      <p class="section-desc">평균이 구간 안이어도 샷마다 들쭉날쭉하면 일관성은 낮은 것 — 훈련일지 "10구 중 6개 이상 = 감각 생존" 기준선을 점선으로 표시.
      R10이 경로 데이터를 못 잡는 샷이 흔해(전체 이력 기준 드라이버 약 50%) 날짜 옆에 "구간 내 샷수/경로표본수"를 표시하고, 표본 5개 미만 세션은 "참고용"으로 표시.
      <span style="color:var(--series-2);font-weight:600">주황 라인</span>은 최근 3세션의 경로표본을 합쳐 계산한 재현율(합산 표본 5개 이상일 때만) — 세션 하나의 흔들림보다 추세를 볼 때 쓸 것.</p>
      {repro_chart}
      <div class="badge-row">{''.join(repro_badges)}</div>
    </div>
    <div class="chart-grid">
      <div class="card">
        <h3>드라이버 — 백스핀 평균 (목표 {DRIVER_BACKSPIN_TARGET[0]}~{DRIVER_BACKSPIN_TARGET[1]})</h3>
        {backspin_chart}
      </div>
      <div class="card">
        <h3>드라이버 — 캐리 평균</h3>
        {carry_chart}
      </div>
      <div class="card">
        <h3>드라이버 — 헤드스피드 평균</h3>
        {speed_chart}
      </div>
      <div class="card">
        <h3>드라이버 — 볼스피드 평균</h3>
        {ballspeed_chart}
      </div>
      <div class="card">
        <h3>드라이버 — 스윙 템포 (백스윙:다운스윙 비율)</h3>
        <p class="section-desc" style="margin-bottom:8px">샷 단위 상관 분석(2026-09) 기준: 템포가 높을수록(다운스윙이 백스윙 대비 느릴수록) 경로가 목표에 더 가깝고 편차도 작은 약한 경향이 있음(상관계수 0.12~0.15, 절대적 요인은 아님).</p>
        {tempo_chart}
      </div>
    </div>
    <div class="card">
      <h3>드라이버 헤드 경로 일관성 (표준편차, 낮을수록 안정)</h3>
      {std_chart}
    </div>"""


def build_termination_section(sessions):
    items = []
    for s in sorted(sessions, key=lambda s: s["date"]):
        flags = s["clubs"].get("드라이버", {}).get("termination_flags", [])
        for f in flags:
            items.append(
                f"<li><b>{s['date']}</b> {f['start_time']} ~ {f['end_time']} ({f['len']}구 연속) "
                f"— 타구각 +2 미만 또는 캐리 -10m 이상 조건 충족</li>"
            )
    if not items:
        return """
  <div class="section">
    <h2>종료 규칙 감지</h2>
    <p class="section-desc">아직 종료 조건(3연속)에 해당하는 구간이 감지되지 않았습니다.</p>
  </div>"""
    return f"""
  <div class="section">
    <h2>종료 규칙 감지 — 후반 피로 신호</h2>
    <p class="section-desc">훈련일지 종료 규칙: "타구각 +2 미만 또는 캐리 평균 -10m 이상, 3연속 → 그날 종료". 세션당 여러 번 걸릴 수 있음(피로 누적이 아니라 그 시점의 국소적 편차 신호로 해석).</p>
    <details>
      <summary>감지된 구간 <span class="meta">{len(items)}건</span></summary>
      <div class="flag-note"><ul style="margin:4px 0 0;padding-left:18px">{''.join(items)}</ul></div>
    </details>
  </div>"""


def build_iron_gap_section(sessions):
    iron_sessions = sorted(
        [s for s in sessions if "7 아이언" in s["clubs"]], key=lambda s: s["date"]
    )
    if not iron_sessions:
        return ""
    # 갭은 페이스·경로가 둘 다 잡힌 샷으로만 계산되므로 표본 판정도 경로표본 수 기준(드라이버와 동일).
    # 관찰 전용 항목이라 판정은 하지 않고 표본 경고와 목표 구간 표시만 한다.
    lo, hi = IRON_FACE_PATH_GAP_TARGET
    rows = []
    for s in iron_sessions:
        c = s["clubs"]["7 아이언"]
        gap = c["face_path_gap_avg"]
        if gap is None:
            continue
        n_path = c.get("n_path_valid", c["n_clean"])
        low_n = n_path < 5
        n_txt = f"{n_path}개" + (" (표본부족 · 참고용)" if low_n else "")
        in_band = status_badge("good", "구간 내") if lo <= gap <= hi and not low_n else ""
        muted = " class='muted-row'" if low_n else ""
        rows.append(
            f"<tr{muted}><td>{s['date']}</td><td>{gap:.1f}°</td><td>{in_band}</td>"
            f"<td class='n-note'>{n_txt}</td><td>{c['n_clean']}/{c['n']}</td></tr>"
        )
    rows = "".join(rows)
    return f"""
  <div class="section">
    <h2>관찰 항목 — 7번 아이언 페이스-궤도 갭</h2>
    <p class="section-desc">0708 기준선 {IRON_FACE_PATH_GAP_BASELINE[0]}~{IRON_FACE_PATH_GAP_BASELINE[1]}° →
    장기적으로 {IRON_FACE_PATH_GAP_TARGET[0]}~{IRON_FACE_PATH_GAP_TARGET[1]}° 수렴 관찰 중
    (개입 없이 관찰만, 아이언은 성역). 경로표본 5개 미만 세션은 흐리게 표시 — 그날 값으로 판단하지 말 것.</p>
    <div class="table-scroll">
      <table>
        <thead><tr><th>세션</th><th>페이스-궤도 갭</th><th>목표 {lo}~{hi}°</th><th>경로표본</th><th>클린 샷수</th></tr></thead>
        <tbody>
          <tr><td>0708 기준선</td><td>{IRON_FACE_PATH_GAP_BASELINE[0]}~{IRON_FACE_PATH_GAP_BASELINE[1]}°</td><td></td><td>-</td><td>-</td></tr>
          {rows}
        </tbody>
      </table>
    </div>
  </div>"""


CLUB_ORDER = ["드라이버", "3 우드", "5 우드", "3 하이브리드", "4 하이브리드", "5 하이브리드",
              "3 아이언", "4 아이언", "5 아이언", "6 아이언", "7 아이언", "8 아이언", "9 아이언",
              "피칭웨지", "갭웨지", "샌드웨지", "로브웨지"]


def sort_clubs(club_dict):
    known = [c for c in CLUB_ORDER if c in club_dict]
    unknown = [c for c in club_dict if c not in CLUB_ORDER]
    return known + unknown


CARRY_TABLE_SESSIONS = 5  # 클럽별 캐리표: 클럽마다 그 클럽을 친 최근 N세션을 합산


def build_carry_table(sessions):
    """클럽별 캐리표. 세션 요약값만 누적돼 있어 샷 단위 중앙값 대신 클린 샷수 가중평균,
    세션 중앙값의 범위, 세션 내 표준편차의 가중평균을 보여준다."""
    by_club = {}
    for s in sorted(sessions, key=lambda s: (s["date"], s["time_start"])):
        for club, c in s["clubs"].items():
            if c.get("carry_avg") is not None and c.get("n_clean", 0) > 0:
                by_club.setdefault(club, []).append((s["date"], c))

    rows = []
    for club in sort_clubs(by_club):
        if club in WEDGE_REFERENCE:  # 웨지는 어프로치 샷이 섞여 있어 아래 웨지 거리표에서 샷 단위로 따로 본다
            continue
        recent = by_club[club][-CARRY_TABLE_SESSIONS:]
        n_total = sum(c["n_clean"] for _, c in recent)
        avg = sum(c["carry_avg"] * c["n_clean"] for _, c in recent) / n_total
        medians = [c["carry_median"] for _, c in recent if c.get("carry_median") is not None]
        std_pts = [(c["carry_std"], c["n_clean"]) for _, c in recent if c.get("carry_std") is not None]
        std = sum(v * n for v, n in std_pts) / sum(n for _, n in std_pts) if std_pts else None
        last_date, last = recent[-1]
        range_txt = f"{min(medians):.0f}~{max(medians):.0f}m" if len(medians) > 1 else (f"{medians[0]:.0f}m" if medians else "-")
        n_txt = f"{n_total}구 · {len(recent)}세션" + (" (표본부족)" if n_total < 10 else "")
        rows.append(
            f"<tr><td>{club}</td><td>{avg:.0f}m</td><td>{'±' + format(std, '.0f') + 'm' if std is not None else '-'}</td>"
            f"<td>{range_txt}</td><td>{last['carry_avg']:.0f}m <span class='n-note'>({fmt_ko_date(last_date)}, {last['n_clean']}구)</span></td>"
            f"<td class='n-note'>{n_txt}</td></tr>"
        )
    if not rows:
        return ""
    return f"""
  <div class="section">
    <h2>클럽별 캐리표</h2>
    <p class="section-desc">클럽마다 그 클럽을 친 최근 {CARRY_TABLE_SESSIONS}세션의 클린 샷 기준. 캐리는 클린 샷수 가중평균, ±는 세션 안 흩어짐(표준편차) 평균,
    범위는 세션별 중앙값의 최소~최대. 합산 10구 미만은 표본부족. 웨지는 어프로치 연습 샷이 섞여 있어 아래 "웨지 거리표 검증"에서 샷 단위로 따로 봄.</p>
    <div class="table-scroll card">
      <table>
        <thead><tr><th>클럽</th><th>캐리</th><th>흩어짐</th><th>세션 중앙값 범위</th><th>최근 세션</th><th>표본</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </div>"""


WEDGE_LABELS = {"피칭웨지": "P", "갭웨지": "50도", "샌드웨지": "56도", "로브웨지": "60도"}


def _carry_summary(carries):
    """샷 캐리 목록 -> '중앙값 (사분위 범위)' 문자열. 4구 미만이면 사분위 생략."""
    if not carries:
        return "-", None
    med = st.median(carries)
    if len(carries) >= 4:
        q1, _, q3 = st.quantiles(carries, n=4)
        return f"{med:.0f}m <span class='n-note'>({q1:.0f}~{q3:.0f})</span>", med
    return f"{med:.0f}m", med


def build_wedge_table(sessions):
    """사용자 기준 거리표 대비 실측 웨지 캐리. 샷 단위로 풀샷/컨트롤/어프로치를 나눈 wedge_split을
    클럽마다 그 클럽을 친 최근 CARRY_TABLE_SESSIONS세션에 걸쳐 합산한다."""
    rows = []
    ordered = sorted(sessions, key=lambda s: (s["date"], s["time_start"]))
    for club, ref in WEDGE_REFERENCE.items():
        splits = [s["clubs"][club]["wedge_split"] for s in ordered
                  if club in s["clubs"] and "wedge_split" in s["clubs"][club]][-CARRY_TABLE_SESSIONS:]
        if not splits:
            continue
        full = [c for sp in splits for c in sp["full"]]
        control = [c for sp in splits for c in sp["control"]]
        n_approach = sum(sp["n_approach"] for sp in splits)
        n_outlier = sum(sp["n_outlier"] for sp in splits)
        full_min, control_min, _ = wedge_bands(club)

        full_lo, full_hi = ref["full"]
        ref_full = f"{full_lo}m" if full_lo == full_hi else f"{full_lo}~{full_hi}m"
        full_txt, full_med = _carry_summary(full)
        if full_med is not None:
            diff = full_med - full_lo if full_med < full_lo else (full_med - full_hi if full_med > full_hi else 0)
            diff_txt = "기준 안" if diff == 0 else f"{diff:+.0f}m"
            full_txt += f" · {diff_txt}"
        full_txt += f" <span class='n-note'>{len(full)}구" + (" 표본부족" if len(full) < 5 else "") + "</span>"

        if ref["control"] is not None:
            ctrl_txt, ctrl_med = _carry_summary(control)
            if ctrl_med is not None:
                ctrl_txt += f" · {ctrl_med - ref['control']:+.0f}m"
            ctrl_txt += f" <span class='n-note'>{len(control)}구" + (" 표본부족" if len(control) < 5 else "") + "</span>"
            ref_ctrl = f"{ref['control']}m"
            band_txt = f"풀 ≥{full_min:.0f} · 컨트롤 {control_min:.0f}~{full_min:.0f}"
        else:
            ctrl_txt, ref_ctrl = "-", "-"
            band_txt = f"풀 ≥{full_min:.0f}"

        rows.append(
            f"<tr><td>{WEDGE_LABELS.get(club, club)} <span class='n-note'>{club}</span></td>"
            f"<td>{ref_full}</td><td>{full_txt}</td><td>{ref_ctrl}</td><td>{ctrl_txt}</td>"
            f"<td>{n_approach}구" + (f" <span class='n-note'>(이상치 {n_outlier})</span>" if n_outlier else "") + "</td>"
            f"<td class='n-note'>{band_txt} · {len(splits)}세션</td></tr>"
        )
    if not rows:
        return ""
    return f"""
  <div class="section">
    <h2>웨지 거리표 검증</h2>
    <p class="section-desc">내 거리표(기준) 대비 실측 캐리. 짧은 웨지 샷은 미스샷이 아니라 어프로치 연습이므로 샷마다 풀샷/컨트롤/어프로치로 나눠 집계
    (클럽마다 그 클럽을 친 최근 {CARRY_TABLE_SESSIONS}세션 합산). 값은 중앙값, 괄호는 가운데 50% 범위, 뒤의 ±는 기준과의 차이.
    분류 경계: 컨트롤 기준이 있으면 풀샷 하한 = 컨트롤과 풀샷 기준의 중간, 컨트롤 하한 = 컨트롤 기준 -{WEDGE_CONTROL_MARGIN}m,
    컨트롤 기준이 없으면 풀샷 하한 = 풀샷 기준의 {int(WEDGE_FULL_RATIO*100)}%. 그 아래는 어프로치로 세고 캐리 계산에서 뺌.
    풀샷 기준의 {int(WEDGE_OUTLIER_RATIO*100)}% 초과는 클럽 선택 오류로 보고 제외(이상치).</p>
    <div class="table-scroll card">
      <table>
        <thead><tr><th>클럽</th><th>풀샷 기준</th><th>풀샷 실측</th><th>컨트롤 기준</th><th>컨트롤 실측</th><th>어프로치</th><th>분류 경계</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </div>"""


def build_session_table(session):
    rows = []
    for club in sort_clubs(session["clubs"]):
        c = session["clubs"][club]
        n_note = f"{c['n_clean']}/{c['n']}" + (" (표본부족)" if c["n_clean"] < 5 else "")
        if c.get("n_speed_sanitized"):
            n_note += f" (스피드 오류 {c['n_speed_sanitized']}건 제외)"
        path_valid = c.get("n_path_valid")
        if path_valid is not None and path_valid < c["n_clean"]:
            n_note += f" (경로표본 {path_valid}개)"
        def f1(v, suffix=""):
            return f"{v:.1f}{suffix}" if v is not None else "-"

        rows.append(
            f"<tr><td>{club}</td><td class='n-note'>{n_note}</td>"
            f"<td>{fmt_lr(c['club_path_avg'])}</td>"
            f"<td>{f1(c['club_face_avg'])}</td>"
            f"<td>{f1(c['attack_angle_avg'])}</td>"
            f"<td>{f1(c['backspin_avg'])}</td>"
            f"<td>{f1(c['carry_avg'], 'm')}/{f1(c['carry_median'], 'm')}</td>"
            f"<td>{f1(c['smash_avg'])}</td>"
            f"<td>{f1(c['swing_tempo_avg'])}</td></tr>"
        )
    return "".join(rows)


def build_session_details(sessions):
    ordered = sorted(sessions, key=lambda s: (s["date"], s["time_start"]), reverse=True)
    blocks = []
    for i, s in enumerate(ordered):
        clubs_list = ", ".join(sort_clubs(s["clubs"]))
        open_attr = " open" if i == 0 else ""
        blocks.append(f"""
    <details{open_attr}>
      <summary>{s['date']} <span class="meta">{s['total_shots']}구 · {clubs_list}</span></summary>
      <div class="table-scroll card">
        <table>
          <thead><tr><th>클럽</th><th>샷(클린/전체)</th><th>헤드경로</th><th>페이스</th><th>타구각</th><th>백스핀</th><th>캐리(평균/중앙)</th><th>스매시</th><th>템포</th></tr></thead>
          <tbody>{build_session_table(s)}</tbody>
        </table>
      </div>
    </details>""")
    return f"""
  <div class="section">
    <h2>세션별 클럽 상세</h2>
    {''.join(blocks)}
  </div>"""


def build_body_html(history):
    all_sessions = list(history["sessions"].values())
    all_sessions.sort(key=lambda s: (s["date"], s["time_start"]))
    total_n = len(all_sessions)
    sessions = all_sessions[-RECENT_N:]

    window_note = (
        f"CSV를 golf 폴더에 추가하고 업데이트를 요청할 때마다 자동으로 누적 반영됩니다. "
        f"최근 {len(sessions)}회 기준(전체 누적 {total_n}회 중)."
        if total_n > RECENT_N else
        "CSV를 golf 폴더에 추가하고 업데이트를 요청할 때마다 자동으로 누적 반영됩니다."
    )

    return f"""
  <h1>R10 드라이빙레인지 대시보드</h1>
  <p class="subtitle">{window_note}</p>

  {build_overview_tiles(sessions)}

  <div class="section">
    <h2>드라이버 추이</h2>
    <p class="section-desc">훈련일지 기준: 이상적 정착 구간 헤드 경로 {DRIVER_PATH_TARGET[0]}~{DRIVER_PATH_TARGET[1]}R, 백스핀 목표 {DRIVER_BACKSPIN_TARGET[0]}~{DRIVER_BACKSPIN_TARGET[1]}.</p>
    {build_driver_trend(sessions)}
  </div>

  {build_carry_table(sessions)}
  {build_wedge_table(sessions)}
  {build_termination_section(sessions)}
  {build_iron_gap_section(sessions)}
  {build_session_details(sessions)}

  <footer>{MISHIT_THRESHOLD_NOTE}</footer>"""


# ---------------------------------------------------------------------------
# 암호화 (golf_dashboard/generate_app.py와 동일한 방식)
# ---------------------------------------------------------------------------

def expand_keystream(key, salt, length):
    out = b""
    counter = 0
    while len(out) < length:
        out += hmac.new(key, salt + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def encrypt_payload(data_blob, password):
    plaintext = json.dumps(data_blob, ensure_ascii=False).encode("utf-8")
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32)
    keystream = expand_keystream(key, salt, len(plaintext))
    cipher = bytes(a ^ b for a, b in zip(plaintext, keystream))
    return {
        "salt": salt.hex(),
        "iterations": PBKDF2_ITERATIONS,
        "cipher": base64.b64encode(cipher).decode("ascii"),
    }


GATE_HTML = """<div class="gate-overlay" id="gateOverlay">
  <div class="gate-box">
    <h1>비밀번호를 입력하세요</h1>
    <input type="password" id="gatePassword" placeholder="비밀번호" autocomplete="off" />
    <button id="gateSubmit">확인</button>
    <div class="gate-error" id="gateError"></div>
  </div>
</div>"""

GATE_JS = r"""const ENCRYPTED = __DATA_JSON__;

async function derivePasswordKey(password, saltBytes, iterations) {
  const enc = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey("raw", enc.encode(password), { name: "PBKDF2" }, false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt: saltBytes, iterations, hash: "SHA-256" }, keyMaterial, 256);
  return new Uint8Array(bits);
}

async function hmacSha256(keyBytes, msgBytes) {
  const key = await crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, msgBytes);
  return new Uint8Array(sig);
}

async function expandKeystream(key, saltBytes, length) {
  let out = new Uint8Array(0);
  let counter = 0;
  while (out.length < length) {
    const counterBytes = new Uint8Array(4);
    new DataView(counterBytes.buffer).setUint32(0, counter, false);
    const msg = new Uint8Array(saltBytes.length + 4);
    msg.set(saltBytes, 0);
    msg.set(counterBytes, saltBytes.length);
    const block = await hmacSha256(key, msg);
    const combined = new Uint8Array(out.length + block.length);
    combined.set(out, 0);
    combined.set(block, out.length);
    out = combined;
    counter++;
  }
  return out.slice(0, length);
}

async function tryUnlock(password) {
  const saltBytes = new Uint8Array(ENCRYPTED.salt.match(/.{2}/g).map(b => parseInt(b, 16)));
  const cipherBytes = Uint8Array.from(atob(ENCRYPTED.cipher), c => c.charCodeAt(0));
  const key = await derivePasswordKey(password, saltBytes, ENCRYPTED.iterations);
  const keystream = await expandKeystream(key, saltBytes, cipherBytes.length);
  const plainBytes = cipherBytes.map((b, i) => b ^ keystream[i]);
  const text = new TextDecoder("utf-8", { fatal: true }).decode(plainBytes);
  return JSON.parse(text);
}

function mountApp(bodyHtml) {
  document.getElementById("gateOverlay").remove();
  const root = document.getElementById("root");
  root.innerHTML = bodyHtml;
  root.classList.remove("hidden");
}

const gateSubmit = document.getElementById("gateSubmit");
const gatePassword = document.getElementById("gatePassword");
const gateError = document.getElementById("gateError");

async function attemptUnlock() {
  gateError.textContent = "";
  gateSubmit.disabled = true;
  try {
    const data = await tryUnlock(gatePassword.value);
    sessionStorage.setItem("r10Pw", gatePassword.value);
    mountApp(data.html);
  } catch (e) {
    gateError.textContent = "비밀번호가 틀렸습니다.";
    gateSubmit.disabled = false;
  }
}

gateSubmit.addEventListener("click", attemptUnlock);
gatePassword.addEventListener("keydown", (e) => { if (e.key === "Enter") attemptUnlock(); });

(async () => {
  const saved = sessionStorage.getItem("r10Pw");
  if (saved) {
    try {
      const data = await tryUnlock(saved);
      mountApp(data.html);
      return;
    } catch (e) { /* 저장된 비밀번호가 안 맞으면 다시 입력받는다 */ }
  }
  gatePassword.focus();
})();"""


PAGE_CSS = """
  .viz-root {
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6; --series-2: #d9621f; --band: #cde2fb;
    --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .viz-root {
      color-scheme: dark;
      --surface-1: #1a1a19; --page: #0d0d0d;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
      --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
      --series-1: #3987e5; --series-2: #f08a4b; --band: #184f95;
    }
  }
  :root[data-theme="dark"] .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19; --page: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #f08a4b; --band: #184f95;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--page); }
  .viz-root { background: var(--page); color: var(--text-primary); font-family: system-ui, -apple-system, "Segoe UI", sans-serif; padding: 24px 16px 64px; max-width: 920px; margin: 0 auto; }
  .viz-root h1 { font-size: 22px; margin: 0 0 4px; }
  .viz-root .subtitle { color: var(--text-secondary); font-size: 14px; margin: 0 0 28px; }
  .viz-root h2 { font-size: 16px; margin: 0 0 4px; }
  .viz-root h3 { font-size: 14px; margin: 0 0 10px; color: var(--text-primary); }
  .viz-root .section { margin-bottom: 40px; }
  .viz-root .section-desc { color: var(--text-secondary); font-size: 13px; margin: 0 0 16px; line-height: 1.5; }
  .viz-root .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; margin-bottom: 12px; }
  .viz-root .tile-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .viz-root .tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
  .viz-root .tile .label { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
  .viz-root .tile .value { font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .viz-root .tile .sub { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
  .viz-root .chart-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 16px; }
  .viz-root svg text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .viz-root .axis-label { fill: var(--text-muted); font-size: 11px; }
  .viz-root .value-label { fill: var(--text-primary); font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .viz-root .point-date { fill: var(--text-secondary); font-size: 11px; }
  .viz-root .baseline { stroke: var(--baseline); stroke-width: 1; }
  .viz-root .band-label { fill: var(--text-secondary); font-size: 10px; }
  .viz-root .badge { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 3px 9px 3px 7px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface-1); color: var(--text-primary); white-space: nowrap; }
  .viz-root .badge .dot { width: 8px; height: 8px; border-radius: 2px; flex: none; }
  .viz-root .badge.good .dot { background: var(--good); }
  .viz-root .badge.warning .dot { background: var(--warning); }
  .viz-root .badge.serious .dot { background: var(--serious); }
  .viz-root .badge-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
  .viz-root table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .viz-root th, .viz-root td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .viz-root th:first-child, .viz-root td:first-child { text-align: left; font-variant-numeric: normal; }
  .viz-root th { color: var(--text-muted); font-weight: 500; font-size: 12px; }
  .viz-root td.n-note, .viz-root span.n-note { color: var(--text-muted); font-size: 11px; }
  .viz-root tr.muted-row td { color: var(--text-muted); }
  .viz-root .table-scroll { overflow-x: auto; }
  .viz-root details { margin-bottom: 14px; }
  .viz-root summary { cursor: pointer; font-size: 14px; font-weight: 600; padding: 10px 0; list-style: none; display: flex; align-items: center; gap: 8px; }
  .viz-root summary::-webkit-details-marker { display: none; }
  .viz-root summary::before { content: "▸"; color: var(--text-muted); font-size: 11px; }
  .viz-root details[open] summary::before { transform: rotate(90deg); display: inline-block; }
  .viz-root summary .meta { color: var(--text-muted); font-weight: 400; font-size: 12px; }
  .viz-root .flag-note { font-size: 12px; color: var(--text-secondary); background: var(--surface-1); border: 1px solid var(--border); border-left: 3px solid var(--serious); border-radius: 6px; padding: 8px 12px; margin-top: 10px; }
  .viz-root footer { color: var(--text-muted); font-size: 12px; line-height: 1.6; border-top: 1px solid var(--border); padding-top: 16px; margin-top: 32px; display: block; }
  .hidden { display: none; }
  .gate-overlay { position: fixed; inset: 0; background: __THEME__; display: flex; align-items: center; justify-content: center; z-index: 10; font-family: system-ui, -apple-system, sans-serif; }
  .gate-box { background: #0A2C22; border: 1px solid rgba(242,235,216,0.14); padding: 36px 32px; text-align: center; width: 280px; border-radius: 4px; }
  .gate-box h1 { font-size: 16px; margin: 0 0 16px; color: #F2EBD8; }
  .gate-box input { width: 100%; padding: 10px 12px; font-size: 14px; border: 1px solid rgba(242,235,216,0.14); margin-bottom: 10px; background: __THEME__; color: #F2EBD8; border-radius: 4px; }
  .gate-box button { width: 100%; padding: 10px 12px; font-size: 14px; border: none; border-radius: 4px; background: #F2EBD8; color: __THEME__; font-weight: 800; cursor: pointer; }
  .gate-box button:disabled { opacity: 0.6; cursor: default; }
  .gate-error { margin-top: 10px; font-size: 12.5px; color: #E4572E; min-height: 1em; }
""".replace("__THEME__", THEME_COLOR)


def render_html(body_html, password):
    if not password:
        raise RuntimeError(".env에 DASHBOARD_PASSWORD가 없습니다.")

    encrypted = encrypt_payload({"html": body_html}, password)
    data_json = json.dumps(encrypted, ensure_ascii=False)
    gate_js = GATE_JS.replace("__DATA_JSON__", data_json)

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
<meta name="theme-color" content="{THEME_COLOR}" />
<title>R10 드라이빙레인지 대시보드</title>
<style>{PAGE_CSS}</style>
</head>
<body>
{GATE_HTML}
<div id="root" class="hidden viz-root"></div>
<script>
{gate_js}
</script>
</body>
</html>
"""


def main():
    env = load_env()
    password = env.get("DASHBOARD_PASSWORD", "").strip()

    if not HISTORY_FILE.exists():
        print("[오류] data/r10_history.json이 없습니다. update_data.py를 먼저 실행하세요.")
        sys.exit(1)

    history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    if not history.get("sessions"):
        print("[오류] 누적된 세션이 없습니다.")
        sys.exit(1)

    body_html = build_body_html(history)
    html = render_html(body_html, password)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"[생성] {OUTPUT_HTML} ({len(history['sessions'])}개 세션 반영)")


if __name__ == "__main__":
    main()
