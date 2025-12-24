#%% [markdown]
# Hugging Face grass widget generator.
#
# - Fetches activity via the public /api/recent-activity endpoint.
# - Aggregates daily counts for the last N days.
# - Renders an SVG heatmap suitable for GitHub README embedding.

#%%
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = "https://huggingface.co/api/recent-activity"  # Same endpoint as the profile feed.
DEFAULT_COLORS = ["#ebedf0", "#ffe2b3", "#ffc266", "#ff9d00", "#ff7a00"]
REACTION_COLORS = ["#ebedf0", "#ffd6d6", "#ffb3b3", "#ff7a7a", "#ff4d4d"]
GITHUB_DARK_COLORS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
GITHUB_DARK_REACTION_COLORS = [
    "#161b22",
    "#3b1d1f",
    "#5b1e23",
    "#8b1d26",
    "#f85149",
]
THEMES = {
    "light": {
        "background": "#ffffff",
        "text": "#57606a",
        "colors": DEFAULT_COLORS,
        "reaction_colors": REACTION_COLORS,
    },
    "github-dark": {
        "background": "#0d1117",
        "text": "#8b949e",
        "colors": GITHUB_DARK_COLORS,
        "reaction_colors": GITHUB_DARK_REACTION_COLORS,
    },
}
REACTION_TYPES = {"upvote", "like"}
VALID_ACTIVITY_TYPES = {"all", "discussion", "upvote", "like"}


#%%

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Hugging Face activity heatmap SVG.",
    )
    parser.add_argument("--user", help="Hugging Face username (or set HF_USERNAME)")
    parser.add_argument(
        "--out",
        default="assets/hf-grass.svg",
        help="Output SVG path",
    )
    parser.add_argument("--days", type=int, default=365, help="Days to show")
    parser.add_argument(
        "--activity-type",
        default="all",
        choices=sorted(VALID_ACTIVITY_TYPES),
        help="Activity type filter",
    )
    parser.add_argument(
        "--week-start",
        default="sunday",
        choices=["sunday", "monday"],
        help="Week start day for the grid",
    )
    parser.add_argument("--cell-size", type=int, default=11, help="Cell size in px")
    parser.add_argument("--cell-gap", type=int, default=2, help="Cell gap in px")
    parser.add_argument(
        "--max-requests",
        type=int,
        default=200,
        help="Safety cap for pagination requests",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Sleep seconds between requests",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Optional title text at the top of the SVG",
    )
    parser.add_argument(
        "--show-legend",
        action="store_true",
        help="Include a small Less/More legend",
    )
    parser.add_argument(
        "--theme",
        default="light",
        choices=sorted(THEMES.keys()),
        help="Color theme for background and palette",
    )
    parser.add_argument(
        "--tz-offset",
        type=int,
        default=0,
        help="Timezone offset hours from UTC for daily buckets (e.g., 9 for KST)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Save a matplotlib preview plot (optional dependency)",
    )
    return parser.parse_args()


#%%

def build_url(user: str, activity_type: str, limit: int, cursor: Optional[str]) -> str:
    params = {
        "limit": limit,
        "activityType": activity_type,
        "feedType": "user",
        "entity": user,
    }
    if cursor:
        params["cursor"] = cursor
    return f"{API_BASE}?{urlencode(params)}"


def fetch_recent_activity(
    user: str,
    activity_type: str,
    limit: int,
    cursor: Optional[str],
) -> Dict[str, object]:
    url = build_url(user, activity_type, limit, cursor)
    request = Request(url, headers={"User-Agent": "hf-grass-widget"})
    with urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def parse_time(value: str, tz: dt.tzinfo) -> dt.date:
    # ISO 8601 with Z suffix is the common case in the activity feed.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    timestamp = dt.datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    return timestamp.astimezone(tz).date()


def dedupe_key(item: Dict[str, object]) -> str:
    # eventId is ideal when available; otherwise fall back to a stable tuple.
    event_id = item.get("eventId")
    if event_id:
        return f"event:{event_id}"
    return "|".join(
        str(
            item.get(field, "")
        )
        for field in ("time", "type", "repoId", "targetType")
    )


#%%

def collect_activity(
    user: str,
    activity_type: str,
    days: int,
    tz: dt.tzinfo,
    max_requests: int,
    sleep_seconds: float,
) -> List[Dict[str, object]]:
    today = dt.datetime.now(dt.timezone.utc).date()
    earliest_date = today - dt.timedelta(days=days - 1)

    items: List[Dict[str, object]] = []
    seen: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: Optional[str] = None

    for _ in range(max_requests):
        data = fetch_recent_activity(user, activity_type, limit=50, cursor=cursor)
        batch = data.get("recentActivity", []) or []
        if not batch:
            break

        for entry in batch:
            if not isinstance(entry, dict):
                continue
            key = dedupe_key(entry)
            if key in seen:
                continue
            seen.add(key)
            items.append(entry)

        cursor = data.get("cursor")
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)

        oldest_time = batch[-1].get("time")
        if oldest_time:
            oldest_date = parse_time(str(oldest_time), tz)
            if oldest_date < earliest_date:
                break

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return items


def aggregate_stats(
    items: Iterable[Dict[str, object]],
    start_date: dt.date,
    end_date: dt.date,
    tz: dt.tzinfo,
) -> Dict[dt.date, Dict[str, object]]:
    stats: Dict[dt.date, Dict[str, object]] = {}
    for entry in items:
        time_value = entry.get("time")
        if not time_value:
            continue
        date_value = parse_time(str(time_value), tz)
        if date_value < start_date or date_value > end_date:
            continue
        if date_value not in stats:
            stats[date_value] = {"count": 0, "types": set()}
        stats[date_value]["count"] += 1
        event_type = entry.get("type")
        if event_type:
            stats[date_value]["types"].add(str(event_type))
    return stats


#%%

def grid_start_date(start_date: dt.date, week_start: str) -> dt.date:
    if week_start == "sunday":
        # Sunday index for Python weekday() is 6, so offset accordingly.
        offset = (start_date.weekday() + 1) % 7
    else:
        offset = start_date.weekday()
    return start_date - dt.timedelta(days=offset)


def color_index(count: int, max_count: int, levels: int) -> int:
    if count <= 0 or max_count <= 0:
        return 0
    # Scale to discrete bins; non-zero counts should never map to 0.
    ratio = count / max_count
    idx = int(math.ceil(ratio * (levels - 1)))
    return max(1, min(levels - 1, idx))


def render_svg(
    stats: Dict[dt.date, Dict[str, object]],
    start_date: dt.date,
    end_date: dt.date,
    week_start: str,
    cell_size: int,
    cell_gap: int,
    colors: List[str],
    reaction_colors: List[str],
    background_color: str,
    text_color: str,
    title: Optional[str],
    show_legend: bool,
) -> str:
    grid_start = grid_start_date(start_date, week_start)
    total_days = (end_date - grid_start).days + 1
    weeks = int(math.ceil(total_days / 7))

    assert weeks > 0, "Weeks must be positive"
    assert len(colors) >= 2, "Palette must contain at least 2 colors"
    assert len(reaction_colors) >= 2, "Reaction palette must contain at least 2 colors"

    def is_reaction_only(stat: Dict[str, object]) -> bool:
        types = stat.get("types", set())
        return bool(types) and set(types).issubset(REACTION_TYPES)

    max_default = max(
        (int(stat["count"]) for stat in stats.values() if not is_reaction_only(stat)),
        default=0,
    )
    max_reaction = max(
        (int(stat["count"]) for stat in stats.values() if is_reaction_only(stat)),
        default=0,
    )

    padding_x = 12
    padding_top = 20 if title else 10
    padding_bottom = 34 if show_legend else 10

    grid_width = weeks * (cell_size + cell_gap) - cell_gap
    grid_height = 7 * (cell_size + cell_gap) - cell_gap

    width = padding_x * 2 + grid_width
    height = padding_top + grid_height + padding_bottom

    def day_index(date_value: dt.date) -> int:
        if week_start == "sunday":
            return (date_value.weekday() + 1) % 7
        return date_value.weekday()

    parts: List[str] = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(
        f'<svg width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Hugging Face activity for {start_date} to {end_date}">'
    )
    parts.append("<style>")
    parts.append(
        f".legend{{font:11px 'IBM Plex Mono', ui-monospace, monospace;fill:{text_color}}}"
    )
    parts.append("</style>")

    parts.append(
        f"<rect width=\"{width}\" height=\"{height}\" fill=\"{background_color}\"/>"
    )

    if title:
        parts.append(
            f"<text x=\"{padding_x}\" y=\"14\" class=\"legend\">{title}</text>"
        )

    parts.append("<g>")
    for day_offset in range(total_days):
        current = grid_start + dt.timedelta(days=day_offset)
        week = day_offset // 7
        row = day_index(current)

        stat = stats.get(current)
        if stat and start_date <= current <= end_date:
            count = int(stat["count"])
            reaction_only = is_reaction_only(stat)
        else:
            count = 0
            reaction_only = False

        palette = reaction_colors if reaction_only else colors
        max_count = max_reaction if reaction_only else max_default
        color = palette[color_index(count, max_count, len(palette))]

        x = padding_x + week * (cell_size + cell_gap)
        y = padding_top + row * (cell_size + cell_gap)

        date_label = current.isoformat()
        title_text = f"{date_label}: {count} activity"

        parts.append(
            f"<rect x=\"{x}\" y=\"{y}\" width=\"{cell_size}\" "
            f"height=\"{cell_size}\" rx=\"2\" ry=\"2\" fill=\"{color}\">"
            f"<title>{title_text}</title></rect>"
        )
    parts.append("</g>")

    if show_legend:
        legend_y = padding_top + grid_height + 14
        legend_x = padding_x + grid_width - (len(colors) * (cell_size + 2) + 40)
        parts.append(
            f"<text x=\"{legend_x - 36}\" y=\"{legend_y + 9}\" class=\"legend\">Less</text>"
        )
        for idx, color in enumerate(colors):
            lx = legend_x + idx * (cell_size + 2)
            parts.append(
                f"<rect x=\"{lx}\" y=\"{legend_y}\" width=\"{cell_size}\" "
                f"height=\"{cell_size}\" rx=\"2\" ry=\"2\" fill=\"{color}\"/>"
            )
        parts.append(
            f"<text x=\"{legend_x + len(colors) * (cell_size + 2) + 6}\" "
            f"y=\"{legend_y + 9}\" class=\"legend\">More</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


#%%

def maybe_save_plot(counts: Dict[dt.date, int], out_path: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        print("matplotlib not available; skipping plot", file=sys.stderr)
        return

    dates = sorted(counts.keys())
    values = [counts[d] for d in dates]

    if not dates:
        print("No activity data to plot", file=sys.stderr)
        return

    plt.figure(figsize=(10, 3))
    plt.plot(dates, values, color="#ff9d00", linewidth=1)
    plt.title("HF activity per day")
    plt.xlabel("Date")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


#%%

def main() -> int:
    args = parse_args()
    user = args.user or os.getenv("HF_USERNAME")
    if not user:
        print("Missing --user (or set HF_USERNAME)", file=sys.stderr)
        return 2

    if args.days < 1:
        print("--days must be >= 1", file=sys.stderr)
        return 2

    if args.activity_type not in VALID_ACTIVITY_TYPES:
        print(f"Unsupported activity type: {args.activity_type}", file=sys.stderr)
        return 2

    tz = dt.timezone(dt.timedelta(hours=args.tz_offset))
    today = dt.datetime.now(tz).date()
    start_date = today - dt.timedelta(days=args.days - 1)

    items = collect_activity(
        user=user,
        activity_type=args.activity_type,
        days=args.days,
        tz=tz,
        max_requests=args.max_requests,
        sleep_seconds=args.sleep,
    )

    stats = aggregate_stats(items, start_date, today, tz)

    theme = THEMES.get(args.theme)
    if not theme:
        print(f"Unsupported theme: {args.theme}", file=sys.stderr)
        return 2

    title = args.title or f"Hugging Face activity ({user})"
    svg = render_svg(
        stats=stats,
        start_date=start_date,
        end_date=today,
        week_start=args.week_start,
        cell_size=args.cell_size,
        cell_gap=args.cell_gap,
        colors=theme["colors"],
        reaction_colors=theme["reaction_colors"],
        background_color=theme["background"],
        text_color=theme["text"],
        title=title,
        show_legend=args.show_legend,
    )

    out_path = args.out
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)

    if args.plot:
        plot_path = os.path.splitext(out_path)[0] + "-preview.png"
        counts = {date: int(stat["count"]) for date, stat in stats.items()}
        maybe_save_plot(counts, plot_path)

    total = sum(int(stat["count"]) for stat in stats.values())
    print(f"Saved {out_path} with {total} activities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
