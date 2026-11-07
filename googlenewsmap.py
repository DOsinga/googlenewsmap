"""Render: read recent geolocated entries from news.db, draw Plotly map."""
import re
import textwrap
import time

import plotly.graph_objects as go

from projects.common import Project

from . import store

WINDOW_HOURS = 24
WRAP_WIDTH = 42


def _strip_publisher(title: str) -> str:
    """Google News RSS titles always end with ' - Publisher'."""
    m = re.match(r"^(.*) - ([^-]+)$", title)
    return m.group(1).strip() if m else title.strip()


def _safe(text: str) -> str:
    """Plotly hover labels parse <br>/<b>/<i> tags but do NOT decode
    HTML entities, so escaping breaks rather than helps. Strip the
    only characters that could confuse the tag parser."""
    return text.replace("<", "").replace(">", "")


def _wrap_with_breaks(text: str) -> str:
    parts = textwrap.wrap(text, WRAP_WIDTH) or [text]
    return "<br>".join(_safe(p) for p in parts)


def _figure(rows, height=520):
    if not rows:
        fig = go.Figure(go.Scattergeo())
    else:
        hover = []
        custom = []
        for r in rows:
            headline = _strip_publisher(r["title"])
            line = f"<b>{_safe(r['display_name'])}</b><br>{_wrap_with_breaks(headline)}"
            if r["publisher"]:
                line += f"<br><i>{_safe(r['publisher'])}</i>"
            hover.append(line)
            custom.append(r["url"])
        fig = go.Figure(go.Scattergeo(
            lat=[r["lat"] for r in rows],
            lon=[r["lon"] for r in rows],
            text=hover,
            customdata=custom,
            hovertemplate="%{text}<extra></extra>",
            mode="markers",
            marker={"size": 9, "color": "#ab0012",
                    "line": {"width": 0.5, "color": "#fff"}},
        ))
    fig.update_layout(
        geo={
            "projection_type": "natural earth",
            "showcountries": True,
            "showcoastlines": True,
            "countrycolor": "#888",
            "coastlinecolor": "#888",
            "landcolor": "#f4f1ea",
            "oceancolor": "#cfe2f3",
            "showocean": True,
            "bgcolor": "rgba(0,0,0,0)",
        },
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _human_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} d ago"


class GoogleNewsMap(Project):
    def fill_dict(self, request, d):
        embed = bool(request.GET.get("embed"))
        cutoff = int(time.time()) - WINDOW_HOURS * 3600
        with store.connect() as conn:
            rows = store.recent_entries_with_location(conn, cutoff)
            last_fetched = store.get_meta(conn, "last_fetched_at")

        fig = _figure(rows, height=320 if embed else 520)
        d["embed"] = embed
        d["plot_html"] = fig.to_html(
            include_plotlyjs="cdn",
            full_html=False,
            div_id="googlenewsmap_plot",
            config={"displayModeBar": False, "scrollZoom": False},
        )
        d["plotted_count"] = len(rows)
        d["window_hours"] = WINDOW_HOURS
        if last_fetched:
            d["last_fetched_human"] = _human_age(int(time.time()) - int(last_fetched))
        else:
            d["last_fetched_human"] = "never (run `python manage.py refresh_news`)"
