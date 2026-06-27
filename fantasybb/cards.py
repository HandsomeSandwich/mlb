"""Shareable trade-verdict cards.

Renders the /trade analyzer result as a self-contained SVG (no external CSS,
fonts, or images) so it can be rasterized to PNG in the browser and copied to
the clipboard — something you can paste to a trade partner. Pure string
templating: no third-party dependencies, works fully offline.
"""
from xml.sax.saxutils import escape

# Theme — mirrors static/style.css :root so the card matches the dashboard.
_BG, _PANEL, _LINE = "#0d1117", "#161b22", "#2a3140"
_TEXT, _MUTED = "#e6edf3", "#8b949e"
_ACCENT, _GREEN, _RED, _HL, _LINK = "#f0883e", "#3fb950", "#f85149", "#ffd966", "#58a6ff"

_W, _PAD, _COLGAP, _ROW_H = 760, 28, 24, 30
_VERDICT_BORDER = {
    "Even trade": _GREEN, "Slight edge": _HL, "Clear edge": _ACCENT, "Lopsided": _RED,
}


def _trunc(s: str, n: int = 28) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _val_color(v: float) -> str:
    return _GREEN if v >= 0 else _RED


def _side_rows(side: dict) -> list[tuple[str, str, str]]:
    """(name, value_str, color) per player on a side, plus unmatched as muted."""
    rows = []
    for pl in side.get("players", []):
        v = pl["value"]
        rows.append((_trunc(pl["row"]["full_name"]), f"{v:+.1f}", _val_color(v)))
    for nm in side.get("unmatched", []):
        rows.append((_trunc(f"{nm} (not found)"), "—", _MUTED))
    return rows or [("—", "", _MUTED)]


def trade_card_svg(result: dict, season) -> str:
    """Build an SVG string for a completed evaluate_text_trade() result."""
    a, b = result["a"], result["b"]
    a_rows, b_rows = _side_rows(a), _side_rows(b)
    max_rows = max(len(a_rows), len(b_rows))

    has_bar = result.get("share_a") is not None
    cols_top = 150 if has_bar else 124
    col_h = 34 + max_rows * _ROW_H
    foot_y = cols_top + col_h + 26
    height = foot_y + 30

    inner_w = _W - 2 * _PAD
    col_w = (inner_w - _COLGAP) / 2
    a_x, b_x = _PAD, _PAD + col_w + _COLGAP

    border = _VERDICT_BORDER.get(result["verdict"], _LINE)
    if result["winner"]:
        sub = f"favors Side {result['winner']} by {abs(result['delta']):.1f} value"
    else:
        sub = f"within {abs(result['delta']):.1f} value — call it square"

    p = []  # svg fragments
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{height:.0f}" '
        f'viewBox="0 0 {_W} {height:.0f}" font-family="-apple-system,Segoe UI,Roboto,'
        f'Helvetica,Arial,sans-serif">')
    p.append(f'<rect width="{_W}" height="{height:.0f}" rx="14" fill="{_BG}"/>')
    p.append(f'<rect x="6" y="6" width="{_W-12}" height="{height-12:.0f}" rx="11" '
             f'fill="none" stroke="{_LINE}"/>')

    # Header
    p.append(f'<text x="{_PAD}" y="44" font-size="24" font-weight="800" '
             f'fill="{_ACCENT}">⚾ Trade Analyzer</text>')
    p.append(f'<text x="{_W-_PAD}" y="44" font-size="13" fill="{_MUTED}" '
             f'text-anchor="end">{escape(str(season))} · 5×5 roto z-score value</text>')
    p.append(f'<line x1="{_PAD}" y1="60" x2="{_W-_PAD}" y2="60" stroke="{_LINE}"/>')

    # Verdict
    p.append(f'<rect x="{_PAD}" y="74" width="6" height="34" rx="3" fill="{border}"/>')
    p.append(f'<text x="{_PAD+18}" y="100" font-size="22" font-weight="800" '
             f'fill="{_TEXT}">{escape(result["verdict"])}</text>')
    vlen = 13 * len(result["verdict"]) + 30
    p.append(f'<text x="{_PAD+18+vlen}" y="100" font-size="14" fill="{_MUTED}">'
             f'{escape(sub)}</text>')

    # Balance bar
    if has_bar:
        share_a = result["share_a"]
        aw = inner_w * share_a / 100.0
        p.append(f'<clipPath id="bclip"><rect x="{_PAD}" y="118" width="{inner_w:.1f}" '
                 f'height="24" rx="6"/></clipPath>')
        p.append(f'<g clip-path="url(#bclip)">')
        p.append(f'<rect x="{_PAD}" y="118" width="{aw:.1f}" height="24" fill="{_ACCENT}"/>')
        p.append(f'<rect x="{_PAD+aw:.1f}" y="118" width="{inner_w-aw:.1f}" height="24" '
                 f'fill="{_LINK}"/></g>')
        p.append(f'<rect x="{_PAD}" y="118" width="{inner_w:.1f}" height="24" rx="6" '
                 f'fill="none" stroke="{_LINE}"/>')
        p.append(f'<text x="{_PAD+8}" y="134" font-size="12" font-weight="700" '
                 f'fill="#1a1205">A {share_a:.0f}%</text>')
        p.append(f'<text x="{_W-_PAD-8}" y="134" font-size="12" font-weight="700" '
                 f'fill="#06121f" text-anchor="end">B {100-share_a:.0f}%</text>')

    # Two side columns
    def column(x, label, side, rows):
        out = [f'<rect x="{x:.1f}" y="{cols_top}" width="{col_w:.1f}" height="{col_h}" '
               f'rx="10" fill="{_PANEL}" stroke="{_LINE}"/>']
        out.append(f'<text x="{x+14:.1f}" y="{cols_top+24}" font-size="14" '
                   f'font-weight="700" fill="{_TEXT}">Side {label} '
                   f'<tspan fill="{_MUTED}" font-weight="400">receives</tspan></text>')
        tot = side["total"]
        out.append(f'<text x="{x+col_w-14:.1f}" y="{cols_top+24}" font-size="18" '
                   f'font-weight="800" fill="{_val_color(tot)}" text-anchor="end">'
                   f'{tot:+.1f}</text>')
        ry = cols_top + 34 + 20
        for name, vstr, color in rows:
            out.append(f'<text x="{x+14:.1f}" y="{ry}" font-size="14" fill="{_TEXT}">'
                       f'{escape(name)}</text>')
            out.append(f'<text x="{x+col_w-14:.1f}" y="{ry}" font-size="14" '
                       f'font-weight="700" fill="{color}" text-anchor="end">'
                       f'{escape(vstr)}</text>')
            ry += _ROW_H
        return out

    p += column(a_x, "A", a, a_rows)
    p += column(b_x, "B", b, b_rows)

    # Footer
    pool = result.get("pool", {})
    note = (f"League pool: {pool.get('hitters','?')} hitters / "
            f"{pool.get('pitchers','?')} pitchers define average · "
            f"realized {season} results, not projections")
    p.append(f'<text x="{_PAD}" y="{foot_y}" font-size="12" fill="{_MUTED}">'
             f'{escape(note)}</text>')
    p.append(f'<text x="{_W-_PAD}" y="{foot_y}" font-size="12" font-weight="700" '
             f'fill="{_ACCENT}" text-anchor="end">DiamondDB</text>')

    p.append("</svg>")
    return "".join(p)
