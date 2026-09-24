"""세션 수가 늘어날 때마다 재생성되는 인라인 SVG 추이 차트. 외부 라이브러리 없음.

팔레트/마크 스펙은 dataviz 스킬 기준(단일 계열 blue #2a78d6/#3987e5, 목표구간 음영,
2px 라인, 4px 데이터 포인트, 라이트/다크 CSS 변수)을 따른다. 포인트 수가 늘어나면
차트 폭을 넓히고(가로 스크롤), 값 라벨은 첫/마지막/이상치만 선택 표시한다
(dataviz 스킬: "선택적 직접 라벨 - 모든 점에 숫자를 달지 않는다").
"""

PAD_LEFT = 60
PAD_RIGHT = 40
PAD_TOP = 24
PAD_BOTTOM = 46
SPACING = 46  # 포인트 간 최소 간격(px)
MIN_WIDTH = 640
HEIGHT = 190


def _width(n):
    return max(MIN_WIDTH, PAD_LEFT + PAD_RIGHT + max(n - 1, 0) * SPACING)


def _x_positions(n, width):
    plot_w = width - PAD_LEFT - PAD_RIGHT
    if n <= 1:
        return [PAD_LEFT + plot_w / 2]
    return [PAD_LEFT + plot_w * i / (n - 1) for i in range(n)]


def _y(value, y_min, y_max, height=HEIGHT, pad_top=PAD_TOP, pad_bottom=PAD_BOTTOM):
    plot_h = height - pad_top - pad_bottom
    frac = (y_max - value) / (y_max - y_min)
    return pad_top + frac * plot_h


def _select_label_indices(pts, band):
    """첫/마지막/목표구간 이탈점(밴드 없으면 최대·최소)만 선택."""
    n = len(pts)
    idx = {0, n - 1}
    if band is not None:
        lo, hi = band
        idx |= {i for i, (_, v, _) in enumerate(pts) if v < lo or v > hi}
    else:
        vals = [v for _, v, _ in pts]
        idx.add(vals.index(max(vals)))
        idx.add(vals.index(min(vals)))
    return idx


def _fmt(value_fmt, value):
    """value_fmt는 format 문자열 또는 값을 받아 문자열을 돌려주는 함수(예: 경로 L/R 표기)."""
    return value_fmt(value) if callable(value_fmt) else value_fmt.format(value)


def _date_label(svg, x, y, text, rotate):
    if rotate:
        svg.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="end" class="point-date" '
            f'transform="rotate(-35 {x:.1f} {y:.1f})">{text}</text>'
        )
    else:
        svg.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" class="point-date">{text}</text>')


def _wrap_scroll(svg_markup, width):
    if width > MIN_WIDTH:
        return f'<div style="overflow-x:auto"><div style="width:{width}px">{svg_markup}</div></div>'
    return svg_markup


def line_chart(points, y_min, y_max, band=None, band_label="", value_fmt="{:.1f}", aria_label=""):
    """points: [(date_label, value, note)] value must not be None. note is optional small text under date."""
    pts = [p for p in points if p[1] is not None]
    if not pts:
        return "<p style='color:var(--text-muted);font-size:13px'>데이터 없음</p>"

    n = len(pts)
    width = _width(n)
    rotate = n > 8
    xs = _x_positions(n, width)
    ys = [_y(v, y_min, y_max) for _, v, _ in pts]
    label_idx = _select_label_indices(pts, band)

    svg = [
        f'<svg viewBox="0 0 {width} {HEIGHT}" width="{width}" height="{HEIGHT}" role="img" aria-label="{aria_label}">'
    ]

    if band is not None:
        b_lo, b_hi = band
        y_top = _y(b_hi, y_min, y_max)
        y_bot = _y(b_lo, y_min, y_max)
        svg.append(
            f'<rect x="{PAD_LEFT}" y="{y_top:.1f}" width="{width-PAD_LEFT-PAD_RIGHT}" '
            f'height="{y_bot-y_top:.1f}" fill="var(--band)" opacity="0.5" />'
        )
        svg.append(
            f'<text x="{width-PAD_RIGHT-5}" y="{y_top+14:.1f}" text-anchor="end" class="band-label">{band_label}</text>'
        )

    svg.append(f'<line x1="{PAD_LEFT}" y1="{PAD_TOP}" x2="{PAD_LEFT}" y2="{HEIGHT-PAD_BOTTOM}" class="baseline" />')
    svg.append(f'<line x1="{PAD_LEFT}" y1="{HEIGHT-PAD_BOTTOM}" x2="{width-PAD_RIGHT}" y2="{HEIGHT-PAD_BOTTOM}" class="baseline" />')

    if n > 1:
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
        svg.append(f'<polyline points="{poly}" fill="none" stroke="var(--series-1)" stroke-width="2" />')

    for i, ((label, value, note), x, y) in enumerate(zip(pts, xs, ys)):
        r = 4 if i in label_idx else 3
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="var(--series-1)" />')
        if i in label_idx:
            label_y = y - 10 if y > PAD_TOP + 20 else y + 18
            svg.append(f'<text x="{x:.1f}" y="{label_y:.1f}" text-anchor="middle" class="value-label">{_fmt(value_fmt, value)}</text>')
        date_text = label if not note else f"{label}({note})"
        date_y = HEIGHT - PAD_BOTTOM + (14 if not rotate else 12)
        _date_label(svg, x, date_y, date_text, rotate)

    svg.append("</svg>")
    return _wrap_scroll("\n".join(svg), width)


def bar_chart(points, y_min, y_max, value_fmt="{:.1f}", aria_label="", ref_line=None,
              label_all=False, overlay_fmt=None):
    """points: [(date_label, value, note)] 또는 (date_label, value, note, overlay_value).
    ref_line: optional (value, label) for a dashed threshold line.
    label_all: 모든 막대에 값 라벨 표시(막대마다 표본 수가 달라 개별 값을 읽어야 하는 차트용).
    overlay_value가 있으면 막대 위에 보조 라인(예: 최근 3세션 합산 재현율)을 겹쳐 그린다."""
    pts = [p for p in points if p[1] is not None]
    if not pts:
        return "<p style='color:var(--text-muted);font-size:13px'>데이터 없음</p>"

    n = len(pts)
    height = 140
    pad_top, pad_bottom = 10, 46
    width = _width(n)
    rotate = n > 8
    plot_h = height - pad_top - pad_bottom
    plot_w = width - PAD_LEFT - PAD_RIGHT
    slot_w = plot_w / n
    bar_w = min(60, slot_w * 0.55)
    label_idx = set(range(n)) if label_all else _select_label_indices([p[:3] for p in pts], None)

    def y_of(v):
        frac = (y_max - v) / (y_max - y_min)
        return pad_top + frac * plot_h

    svg = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{aria_label}">']

    if ref_line is not None:
        ref_v, ref_label = ref_line
        ref_y = y_of(ref_v)
        svg.append(
            f'<line x1="{PAD_LEFT}" y1="{ref_y:.1f}" x2="{width-PAD_RIGHT}" y2="{ref_y:.1f}" '
            f'stroke="var(--warning)" stroke-width="1.5" stroke-dasharray="4,3" />'
        )
        svg.append(f'<text x="{width-PAD_RIGHT-5}" y="{ref_y-5:.1f}" text-anchor="end" class="band-label">{ref_label}</text>')

    svg.append(f'<line x1="{PAD_LEFT}" y1="{pad_top}" x2="{PAD_LEFT}" y2="{height-pad_bottom}" class="baseline" />')
    svg.append(f'<line x1="{PAD_LEFT}" y1="{height-pad_bottom}" x2="{width-PAD_RIGHT}" y2="{height-pad_bottom}" class="baseline" />')

    overlay = []
    for i, p in enumerate(pts):
        label, value, note = p[:3]
        cx = PAD_LEFT + slot_w * (i + 0.5)
        y_top = y_of(value)
        bar_h = (height - pad_bottom) - y_top
        svg.append(
            f'<rect x="{cx-bar_w/2:.1f}" y="{y_top:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
            f'fill="var(--series-1)" rx="3" />'
        )
        if i in label_idx:
            svg.append(f'<text x="{cx:.1f}" y="{y_top-6:.1f}" text-anchor="middle" class="value-label">{_fmt(value_fmt, value)}</text>')
        date_text = label if not note else f"{label}({note})"
        date_y = height - pad_bottom + (14 if not rotate else 12)
        _date_label(svg, cx, date_y, date_text, rotate)
        if len(p) > 3 and p[3] is not None:
            overlay.append((cx, y_of(p[3]), p[3]))

    if overlay:
        if len(overlay) > 1:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in overlay)
            svg.append(f'<polyline points="{poly}" fill="none" stroke="var(--series-2)" stroke-width="2" />')
        for x, y, _ in overlay:
            svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="var(--series-2)" />')
        x, y, v = overlay[-1]
        svg.append(
            f'<text x="{x+8:.1f}" y="{y+4:.1f}" class="value-label" style="fill:var(--series-2)">'
            f'{_fmt(overlay_fmt or value_fmt, v)}</text>'
        )

    svg.append("</svg>")
    return _wrap_scroll("\n".join(svg), width)
