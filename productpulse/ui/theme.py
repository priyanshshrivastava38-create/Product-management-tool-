"""Design system: tokens, global CSS, page chrome, cards and chart defaults.

User-supplied text is never passed through ``unsafe_allow_html`` without
``html.escape``; most user content is rendered with native Streamlit elements.
Icons in custom HTML use Google's Material Symbols font (`icon()` helper);
Streamlit widgets use the built-in ``:material/name:`` syntax.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from productpulse import TAGLINE

ASSETS = Path(__file__).resolve().parents[2] / "assets"

# ---------------------------------------------------------------- design tokens
ACCENT = "#4F46E5"
INK = "#0F172A"
MUTED = "#64748B"
BORDER = "#E2E8F0"
SURFACE = "#F8FAFC"
SENTIMENT_COLORS = {"negative": "#E5484D", "neutral": "#A0AEC0", "positive": "#12A594", "unscored": "#CBD5E1"}
SEQUENCE = ["#4F46E5", "#12A594", "#F5A524", "#E5484D", "#8E7DBE", "#3A86FF", "#64748B", "#F76B15"]
TREND_LABELS = {
    "emerging": "Rising fast",
    "declining": "Falling",
    "stable": "Steady",
    "insufficient_volume": "Few recent mentions",
    "insufficient_history": "Not enough history",
}
TREND_CLASS = {"emerging": "up", "declining": "down", "stable": "flat"}
TREND_ICON = {"emerging": "trending_up", "declining": "trending_down", "stable": "trending_flat"}
FEELING_LABELS = {
    "negative": ":material/sentiment_dissatisfied: Negative",
    "neutral": ":material/sentiment_neutral: Neutral",
    "positive": ":material/sentiment_satisfied: Positive",
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,400,0..1,0&display=swap');

:root {
  --pp-accent:#4F46E5; --pp-accent-soft:rgba(79,70,229,.08); --pp-ink:#0F172A; --pp-muted:#64748B;
  --pp-border:#E2E8F0; --pp-surface:#F8FAFC; --pp-radius:14px;
  --pp-shadow:0 1px 2px rgba(15,23,42,.04), 0 1px 3px rgba(15,23,42,.06);
  --pp-shadow-hover:0 8px 24px rgba(15,23,42,.08);
}

/* ---------- Typography ---------- */
html, body, .stApp, .stMarkdown, p, li, label, input, textarea, button, h1, h2, h3, h4, h5, h6,
div[data-testid="stMarkdownContainer"], div[data-testid="stCaptionContainer"] {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
}
[data-testid="stIconMaterial"], .msr {
  font-family: 'Material Symbols Rounded' !important; font-weight: normal; font-style: normal;
  line-height: 1; letter-spacing: normal; text-transform: none; white-space: nowrap; direction: ltr;
  -webkit-font-smoothing: antialiased; font-feature-settings: 'liga';
}
.msr { font-size: 1.15em; vertical-align: -0.2em; }
.stApp { color: var(--pp-ink); background: #FFFFFF; }
div[data-testid="stSliderThumbValue"], div[data-testid="stSliderTickBarMin"], div[data-testid="stSliderTickBarMax"],
div[data-testid="stTickBarMin"], div[data-testid="stTickBarMax"], div[data-baseweb="slider"] div {
  font-family: 'Inter', -apple-system, sans-serif !important; }
h1, h2, h3, h4 { letter-spacing: -0.02em; color: var(--pp-ink); font-weight: 650 !important; }
h4 { font-size: 1.12rem !important; margin-top: .4rem !important; }
h5 { font-size: .95rem !important; font-weight: 600 !important; }
div[data-testid="stCaptionContainer"] p { color: var(--pp-muted); }

/* ---------- Streamlit chrome ---------- */
[data-testid="stDecoration"], [data-testid="stStatusWidget"] .stDeployButton, .stDeployButton,
footer, #MainMenu { display: none !important; }
header[data-testid="stHeader"] { background: rgba(255,255,255,.85); backdrop-filter: blur(6px);
  border-bottom: 1px solid transparent; }
.block-container { padding-top: 2.4rem; padding-bottom: 4rem; max-width: 1240px; }

/* ---------- Sidebar ---------- */
section[data-testid="stSidebar"] { background: var(--pp-surface); border-right: 1px solid var(--pp-border); }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] { padding: 0 10px; }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] a {
  border-radius: 9px; margin: 1px 0; padding-left: 12px; }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] a span { font-weight: 500; color: #334155; }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] a[aria-current="page"] {
  background: var(--pp-accent-soft); }
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] a[aria-current="page"] span {
  color: var(--pp-accent); font-weight: 600; }
section[data-testid="stSidebar"] [data-testid="stNavSectionHeader"] {
  font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; color: #94A3B8; font-weight: 600; }
.pp-tagline { color: var(--pp-muted); font-size: .8rem; margin: -6px 0 12px 2px; }

/* ---------- Page header ---------- */
.pp-eyebrow { font-size: .75rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--pp-accent); margin-bottom: 4px; display: flex; align-items: center; gap: 6px; }
.pp-header h1 { font-size: 1.85rem; margin: 0 !important; padding: 0 !important; line-height: 1.2; }
.pp-sub { color: var(--pp-muted); margin: 6px 0 22px 0; font-size: .98rem; max-width: 760px; }

/* ---------- Widgets ---------- */
.stButton > button, .stDownloadButton > button {
  border-radius: 10px; font-weight: 550; border: 1px solid var(--pp-border); box-shadow: var(--pp-shadow);
  transition: transform .12s ease, box-shadow .15s ease, border-color .15s ease; }
.stButton > button:hover, .stDownloadButton > button:hover {
  border-color: rgba(79,70,229,.45); box-shadow: 0 4px 14px rgba(79,70,229,.14); transform: translateY(-1px); }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
  background: linear-gradient(180deg, #5B53F0, #4F46E5); border-color: #4338CA; color: #fff; }
.stButton > button[kind="primary"]:hover { box-shadow: 0 6px 18px rgba(79,70,229,.35); }
div[data-baseweb="input"], div[data-baseweb="select"] > div, div[data-baseweb="textarea"] {
  border-radius: 10px !important; }
div[data-testid="stExpander"] details { border-radius: var(--pp-radius); border-color: var(--pp-border); }
div[data-testid="stExpander"] summary p { font-weight: 550; }
div[data-testid="stDataFrame"] { border: 1px solid var(--pp-border); border-radius: 12px; overflow: hidden; }
div[data-testid="stPlotlyChart"] { border: 1px solid var(--pp-border); border-radius: var(--pp-radius);
  background: #fff; overflow: hidden; }
button[data-baseweb="tab"] p { font-weight: 550; }
div[data-baseweb="tab-highlight"] { background: var(--pp-accent); }
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[data-testid="stVerticalBlock"]) { border-radius: 16px; }
div[data-testid="stAlert"] { border-radius: 12px; }

/* ---------- Cards ---------- */
.pp-card { border: 1px solid var(--pp-border); border-radius: var(--pp-radius); padding: 16px 18px;
  min-height: 116px; background: #fff; box-shadow: var(--pp-shadow);
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
.pp-card:hover { transform: translateY(-2px); box-shadow: var(--pp-shadow-hover); border-color: #CBD5E1; }
.pp-card .top { display: flex; align-items: center; justify-content: space-between; }
.pp-card .lbl { font-size: .8rem; font-weight: 550; color: var(--pp-muted); }
.pp-card .ic { width: 30px; height: 30px; border-radius: 9px; display: inline-flex; align-items: center;
  justify-content: center; background: var(--pp-accent-soft); color: var(--pp-accent); }
.pp-card .val { font-size: 1.75rem; font-weight: 700; line-height: 1.2; margin-top: 8px; letter-spacing: -.02em; }
.pp-card .hint { font-size: .8rem; color: var(--pp-muted); margin-top: 3px; }
.pp-card.red .ic { background: rgba(229,72,77,.10); color: #CE2C31; }
.pp-card.green .ic { background: rgba(18,165,148,.10); color: #0D8476; }
.pp-card.amber .ic { background: rgba(245,165,36,.14); color: #AD6800; }

.pp-badge { display: inline-flex; align-items: center; gap: 3px; padding: 2px 9px; border-radius: 999px;
  font-size: .72rem; font-weight: 600; border: 1px solid var(--pp-border); margin-right: 6px; }
.pp-badge.observed { background: rgba(18,165,148,.08); color: #0D8476; border-color: rgba(18,165,148,.3); }
.pp-badge.calculated { background: var(--pp-accent-soft); color: #4338CA; border-color: rgba(79,70,229,.25); }
.pp-badge.ai { background: rgba(245,165,36,.12); color: #AD6800; border-color: rgba(245,165,36,.4); }
.pp-badge.assumption { background: rgba(247,107,21,.10); color: #BD4B00; border-color: rgba(247,107,21,.35); }

.pp-quote { border-left: 3px solid var(--pp-accent); background: var(--pp-surface); border-radius: 0 10px 10px 0;
  padding: 10px 14px; margin: 10px 0 4px 0; font-size: .97rem; line-height: 1.5; }
.pp-meta { color: var(--pp-muted); font-size: .78rem; margin-bottom: 4px; }
.pp-empty { border: 1px dashed #CBD5E1; border-radius: 16px; padding: 36px; text-align: center;
  background: var(--pp-surface); }
.pp-empty .msr { font-size: 2rem; color: #94A3B8; }
.pp-empty h3 { margin: 8px 0 4px 0; font-size: 1.15rem; }
.pp-empty p { color: var(--pp-muted); margin: 0; }

/* Hero */
.pp-hero { border-radius: 20px; padding: 44px 44px 40px 44px; margin-bottom: 22px; border: 1px solid #E0E7FF;
  background: radial-gradient(1200px 300px at 0% 0%, rgba(79,70,229,.12), transparent 60%),
              radial-gradient(800px 260px at 100% 100%, rgba(18,165,148,.10), transparent 60%), #FFFFFF; }
.pp-hero .pill { display: inline-flex; align-items: center; gap: 6px; font-size: .78rem; font-weight: 600;
  color: var(--pp-accent); background: #fff; border: 1px solid #E0E7FF; border-radius: 999px; padding: 4px 12px; }
.pp-hero h2 { font-size: 2.3rem; line-height: 1.15; margin: 14px 0 10px 0; max-width: 820px; letter-spacing: -.03em; }
.pp-hero p { font-size: 1.08rem; color: #475569; margin: 0; max-width: 720px; line-height: 1.55; }

/* Steps */
.pp-step { border: 1px solid var(--pp-border); border-radius: var(--pp-radius); padding: 18px; height: 100%;
  background: #fff; box-shadow: var(--pp-shadow); transition: transform .15s ease, box-shadow .15s ease; }
.pp-step:hover { transform: translateY(-2px); box-shadow: var(--pp-shadow-hover); }
.pp-step .n { display: inline-flex; width: 34px; height: 34px; border-radius: 10px; align-items: center;
  justify-content: center; background: var(--pp-accent-soft); color: var(--pp-accent); }
.pp-step .k { font-size: .72rem; font-weight: 600; color: #94A3B8; letter-spacing: .06em; margin-top: 12px; }
.pp-step .t { font-weight: 650; margin-top: 2px; }
.pp-step .d { color: var(--pp-muted); font-size: .88rem; margin-top: 4px; line-height: 1.45; }

/* Next-step banner */
.pp-next { border-radius: var(--pp-radius); padding: 16px 20px; display: flex; gap: 14px; align-items: flex-start;
  background: linear-gradient(90deg, rgba(79,70,229,.07), rgba(79,70,229,.02)); border: 1px solid #E0E7FF; }
.pp-next .ic { width: 36px; height: 36px; border-radius: 10px; flex: none; display: inline-flex;
  align-items: center; justify-content: center; background: var(--pp-accent); color: #fff; }
.pp-next .k { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: #4338CA; font-weight: 700; }
.pp-next .t { font-size: 1.02rem; font-weight: 650; margin-top: 1px; }
.pp-next .d { color: var(--pp-muted); font-size: .9rem; }

/* Theme cards */
.pp-theme { border: 1px solid var(--pp-border); border-radius: var(--pp-radius); padding: 16px 18px 14px 18px;
  min-height: 176px; margin-bottom: 8px; background: #fff; box-shadow: var(--pp-shadow);
  transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
.pp-theme:hover { transform: translateY(-2px); box-shadow: var(--pp-shadow-hover); border-color: #C7D2FE; }
.pp-theme .t { font-weight: 650; font-size: 1.02rem; line-height: 1.3; }
.pp-theme .q { color: #475569; font-size: .88rem; margin: 8px 0 12px 0; line-height: 1.45;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.pp-theme .m { font-size: .8rem; color: var(--pp-muted); display: flex; gap: 12px; align-items: center;
  flex-wrap: wrap; }
.pp-theme .m b, .pp-theme .m .pp-count { color: var(--pp-ink); font-weight: 700; }
.pp-bar { display: flex; height: 6px; border-radius: 999px; overflow: hidden; background: #EEF2F7; margin-top: 12px; }
.pp-bar span { display: block; height: 100%; }
.pp-chip { display: inline-flex; align-items: center; gap: 3px; padding: 2px 9px 2px 7px; border-radius: 999px;
  font-size: .74rem; font-weight: 600; background: #F1F5F9; color: #475569; }
.pp-chip .msr { font-size: 1.05rem; }
.pp-chip.up { background: rgba(229,72,77,.10); color: #CE2C31; }
.pp-chip.down { background: rgba(18,165,148,.10); color: #0D8476; }
.pp-chip.flat { background: var(--pp-accent-soft); color: #4338CA; }

/* Digest (since last visit) */
.pp-digest { border: 1px solid var(--pp-border); border-radius: var(--pp-radius); background: #fff;
  box-shadow: var(--pp-shadow); overflow: hidden; }
.pp-digest .item { display: flex; gap: 12px; align-items: center; padding: 12px 16px; border-top: 1px solid #F1F5F9; }
.pp-digest .item:first-child { border-top: none; }
.pp-digest .ic { width: 32px; height: 32px; border-radius: 9px; flex: none; display: inline-flex; align-items: center;
  justify-content: center; background: var(--pp-accent-soft); color: var(--pp-accent); }
.pp-digest .ic.hot { background: rgba(229,72,77,.10); color: #CE2C31; }
.pp-digest .t { font-weight: 600; font-size: .93rem; }
.pp-digest .d { color: var(--pp-muted); font-size: .82rem; }

/* Sidebar progress */
.pp-progress-title { font-size: .7rem; text-transform: uppercase; letter-spacing: .08em; color: #94A3B8;
  font-weight: 600; margin: 4px 0 8px 2px; }
.pp-progress .row { display: flex; align-items: center; gap: 8px; font-size: .85rem; padding: 4px 2px; color: #475569; }
.pp-progress .row .dot { width: 20px; height: 20px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; border: 1.5px solid #CBD5E1; color: transparent; font-size: .9rem; }
.pp-progress .row.done { color: var(--pp-ink); }
.pp-progress .row.done .dot { background: #12A594; border-color: #12A594; color: #fff; }
.pp-progress .bar { height: 4px; border-radius: 999px; background: #E2E8F0; margin: 10px 2px 2px 2px;
  overflow: hidden; }
.pp-progress .bar span { display: block; height: 100%; background: #12A594; }

/* ---------- Motion ---------- */
@property --pp-n { syntax: '<integer>'; initial-value: 0; inherits: false; }
@keyframes pp-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes pp-fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes pp-grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes pp-count { from { --pp-n: 0; } }
@keyframes pp-pulse { 0% { box-shadow: 0 0 0 0 rgba(229,72,77,.55); } 70% { box-shadow: 0 0 0 7px rgba(229,72,77,0); }
  100% { box-shadow: 0 0 0 0 rgba(229,72,77,0); } }
@keyframes pp-shine { from { background-position: -200% 0; } to { background-position: 200% 0; } }

.pp-card, .pp-theme, .pp-step, .pp-next, .pp-digest .item, .pp-empty {
  animation: pp-rise .55s cubic-bezier(.2,.75,.2,1) both; }
.pp-hero { animation: pp-fade .7s ease-out both; }
.pp-hero h2, .pp-hero p, .pp-hero .pill { animation: pp-rise .7s cubic-bezier(.2,.75,.2,1) both; }
.pp-hero h2 { animation-delay: 80ms; } .pp-hero p { animation-delay: 160ms; }
.pp-hero h2 { background: linear-gradient(90deg, #0F172A 0%, #0F172A 40%, #4F46E5 50%, #0F172A 60%, #0F172A 100%);
  background-size: 200% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: pp-rise .7s cubic-bezier(.2,.75,.2,1) both 80ms, pp-shine 3.5s ease-in-out 1s 1 both; }
.pp-digest .item:nth-child(1) { animation-delay: 40ms; } .pp-digest .item:nth-child(2) { animation-delay: 100ms; }
.pp-digest .item:nth-child(3) { animation-delay: 160ms; } .pp-digest .item:nth-child(4) { animation-delay: 220ms; }
.pp-digest .item:nth-child(5) { animation-delay: 280ms; } .pp-digest .item:nth-child(6) { animation-delay: 340ms; }
.pp-bar span { transform-origin: left center; animation: pp-grow .9s cubic-bezier(.2,.75,.2,1) both;
  animation-delay: 300ms; }
.pp-progress .bar span { transform-origin: left center; animation: pp-grow 1s cubic-bezier(.2,.75,.2,1) both .2s; }
.pp-count { animation: pp-count 1.2s cubic-bezier(.2,.75,.2,1) both;
  animation-delay: 150ms;
  counter-reset: ppn var(--pp-n); }
.pp-count::after { content: counter(ppn); }
.pp-chip.up::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: #E5484D; margin-right: 3px;
  animation: pp-pulse 1.8s ease-out infinite; }
.pp-digest .ic.hot .msr { display: inline-block; }
.pp-card .ic, .pp-step .n { transition: transform .25s cubic-bezier(.2,.75,.2,1); }
.pp-card:hover .ic, .pp-step:hover .n { transform: scale(1.12) rotate(-4deg); }
div[data-testid="stPlotlyChart"] { animation: pp-fade .6s ease-out both .1s; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
  .pp-hero h2 { color: var(--pp-ink); background: none; }
}
</style>
"""


def icon(name: str) -> str:
    """Material Symbols icon for custom HTML (name like 'trending_up')."""
    return f'<span class="msr">{html.escape(name)}</span>'


def setup_page() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    template = go.layout.Template(pio.templates["plotly_white"])
    template.layout.colorway = SEQUENCE
    template.layout.font = dict(family="Inter, -apple-system, Segoe UI, sans-serif", size=12.5, color="#334155")
    template.layout.title = dict(font=dict(size=14, color=INK), x=0.01, xanchor="left")
    template.layout.margin = dict(l=8, r=8, t=44, b=8)
    template.layout.paper_bgcolor = "rgba(0,0,0,0)"
    template.layout.plot_bgcolor = "rgba(0,0,0,0)"
    template.layout.xaxis = dict(gridcolor="#EEF2F7", linecolor=BORDER, zeroline=False, ticks="")
    template.layout.yaxis = dict(gridcolor="#EEF2F7", linecolor=BORDER, zeroline=False, ticks="")
    template.layout.hoverlabel = dict(bgcolor="#FFFFFF", bordercolor=BORDER, font=dict(color=INK, size=12))
    template.layout.legend = dict(font=dict(size=12), bgcolor="rgba(0,0,0,0)")
    pio.templates["productpulse"] = template
    pio.templates.default = "productpulse"


def header(title: str, subtitle: str = "", eyebrow: str = "", eyebrow_icon: str = "") -> None:
    top = (
        f'<div class="pp-eyebrow">{icon(eyebrow_icon) if eyebrow_icon else ""}{html.escape(eyebrow)}</div>'
        if eyebrow
        else ""
    )
    st.markdown(f'<div class="pp-header">{top}<h1>{html.escape(title)}</h1></div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(f'<p class="pp-sub">{html.escape(subtitle)}</p>', unsafe_allow_html=True)


_COUNTABLE = re.compile(r"(\d{1,5})(%?)")


def animated_value(value: str) -> str:
    """Integers (and percentages) count up from zero; other values render as-is.

    The final number comes from a per-value class rule, so browsers without @property support show it statically.
    """
    m = _COUNTABLE.fullmatch(value)
    if not m:
        return html.escape(value)
    n = int(m.group(1))
    # Streamlit's HTML sanitiser drops custom properties from inline styles, so the target value is delivered
    # through a tiny class rule instead.
    return (
        f"<style>.ppn-{n}{{--pp-n:{n}}}</style>"
        f'<span class="pp-count ppn-{n}" role="text" aria-label="{html.escape(value)}"></span>{m.group(2)}'
    )


def card(label: str, value: str, hint: str = "", ic: str = "", tone: str = "", delay_ms: int = 0) -> None:
    st.markdown(
        f'<div class="pp-card {html.escape(tone)}" style="animation-delay:{delay_ms}ms"><div class="top">'
        f'<span class="lbl">{html.escape(label)}</span>'
        + (f'<span class="ic">{icon(ic)}</span>' if ic else "")
        + f'</div><div class="val">{animated_value(value)}</div>'
        + (f'<div class="hint">{html.escape(hint)}</div>' if hint else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def cards(items: list[tuple]) -> None:
    """Row of KPI cards. Each item: (label, value, hint[, icon[, tone]])."""
    cols = st.columns(len(items))
    for i, (col, item) in enumerate(zip(cols, items, strict=True)):
        padded = (*item, *([""] * (5 - len(item))))[:5]
        with col:
            card(*padded, delay_ms=70 * i)


def badge(kind: str, text: str | None = None) -> str:
    labels = {"observed": "Observed", "calculated": "Calculated", "ai": "AI-generated", "assumption": "Assumption"}
    return f'<span class="pp-badge {kind}">{html.escape(text or labels[kind])}</span>'


def badges(*kinds: str) -> None:
    st.markdown(" ".join(badge(k) for k in kinds), unsafe_allow_html=True)


def quote(text: str, meta: str) -> None:
    st.markdown(
        f'<div class="pp-quote">“{html.escape(text)}”</div><div class="pp-meta">{html.escape(meta)}</div>',
        unsafe_allow_html=True,
    )


def empty_state(title: str, body: str, ic: str = "inbox") -> None:
    st.markdown(
        f'<div class="pp-empty">{icon(ic)}<h3>{html.escape(title)}</h3><p>{html.escape(body)}</p></div>',
        unsafe_allow_html=True,
    )


def sidebar_brand() -> None:
    st.logo(str(ASSETS / "logo.svg"), icon_image=str(ASSETS / "icon.svg"), size="large")
    st.sidebar.markdown(f'<div class="pp-tagline">{html.escape(TAGLINE)}</div>', unsafe_allow_html=True)


def pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "—"


def hero(title: str, body: str, kicker: str = "Feedback intelligence for product teams") -> None:
    st.markdown(
        f'<div class="pp-hero"><span class="pill">{icon("auto_awesome")}{html.escape(kicker)}</span>'
        f"<h2>{html.escape(title)}</h2><p>{html.escape(body)}</p></div>",
        unsafe_allow_html=True,
    )


def step_card(n: int, title: str, body: str, ic: str = "check") -> None:
    st.markdown(
        f'<div class="pp-step"><span class="n">{icon(ic)}</span><div class="k">STEP {n}</div>'
        f'<div class="t">{html.escape(title)}</div><div class="d">{html.escape(body)}</div></div>',
        unsafe_allow_html=True,
    )


def next_step(title: str, body: str, ic: str = "arrow_forward") -> None:
    st.markdown(
        f'<div class="pp-next"><span class="ic">{icon(ic)}</span><div><div class="k">Suggested next step</div>'
        f'<div class="t">{html.escape(title)}</div><div class="d">{html.escape(body)}</div></div></div>',
        unsafe_allow_html=True,
    )


def trend_chip(status: str | None) -> str:
    s = status or ""
    label = TREND_LABELS.get(s, "—")
    ic = icon(TREND_ICON[s]) if s in TREND_ICON else ""
    return f'<span class="pp-chip {TREND_CLASS.get(s, "")}">{ic}{html.escape(label)}</span>'


def sentiment_bar(neg: int, neu: int, pos: int) -> str:
    total = max(neg + neu + pos, 1)
    parts = [
        (neg, SENTIMENT_COLORS["negative"]),
        (neu, SENTIMENT_COLORS["neutral"]),
        (pos, SENTIMENT_COLORS["positive"]),
    ]
    spans = "".join(f'<span style="width:{100 * n / total:.1f}%;background:{c}"></span>' for n, c in parts if n)
    return f'<div class="pp-bar" title="negative {neg} · neutral {neu} · positive {pos}">{spans}</div>'


def theme_card(
    label: str,
    example: str,
    n: int,
    customers: int,
    neg: int,
    neu: int,
    pos: int,
    trend: str | None,
    delay_ms: int = 0,
) -> None:
    total = max(neg + neu + pos, 1)
    who = f"<span>{icon('group')} <b>{customers}</b> customers</span>" if customers else ""
    example = example if len(example) <= 160 else example[:157].rstrip() + "…"
    st.markdown(
        f'<div class="pp-theme" style="animation-delay:{delay_ms}ms"><div class="t">{html.escape(label)}</div>'
        f'<div class="q">“{html.escape(example)}”</div>'
        f'<div class="m"><span>{icon("forum")} {animated_value(str(n))} mentions</span>{who}'
        f"<span><b>{round(100 * neg / total)}%</b> unhappy</span>{trend_chip(trend)}</div>"
        f"{sentiment_bar(neg, neu, pos)}</div>",
        unsafe_allow_html=True,
    )


def sidebar_progress(steps: list[tuple[str, bool]]) -> None:
    done = sum(d for _, d in steps)
    rows = "".join(
        f'<div class="row{" done" if d else ""}"><span class="dot">{icon("check")}</span>{html.escape(label)}</div>'
        for label, d in steps
    )
    st.sidebar.markdown(
        f'<div class="pp-progress-title">Getting started · {done}/{len(steps)}</div>'
        f'<div class="pp-progress">{rows}<div class="bar"><span style="width:{100 * done / len(steps):.0f}%">'
        "</span></div></div>",
        unsafe_allow_html=True,
    )


def digest(items: list[tuple[str, str, str]]) -> None:
    """Compact list of (icon, title, detail) rows for the 'since your last visit' panel."""
    hot = {"local_fire_department", "trending_up", "fiber_new"}
    rows = "".join(
        f'<div class="item"><span class="ic{" hot" if ic in hot else ""}">{icon(ic)}</span>'
        f'<div><div class="t">{html.escape(t)}</div><div class="d">{html.escape(d)}</div></div></div>'
        for ic, t, d in items
    )
    st.markdown(f'<div class="pp-digest">{rows}</div>', unsafe_allow_html=True)
