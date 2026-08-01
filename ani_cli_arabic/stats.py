from typing import Dict, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import COLOR_BORDER
from .history import HistoryManager
from .monitoring import monitor


def fetch_remote_stats(limit: int = 500):
    """Fetch aggregated streaming history from the remote telemetry endpoint."""
    try:
        return monitor.fetch_stats(limit)
    except Exception:
        return None


def build_local_stats() -> Dict:
    """Build a stats summary from the local last-watched history file."""
    items = HistoryManager().get_history()
    total = len(items)
    unique_titles = len({it['title'] for it in items})
    top_titles = [
        {"title": it['title'], "count": 1, "episode": it['episode']}
        for it in items[:10]
    ]
    return {
        "source": "local",
        "total_plays": total,
        "unique_titles": unique_titles,
        "recent_7d": 0,
        "last_played": items[0]['last_updated'] if items else None,
        "top_titles": top_titles,
        "by_player": {},
        "by_provider": {},
        "by_quality": {},
        "note": "Local history tracks the last watched episode per anime. Enable analytics in settings for full playback stats (every video play, player, provider and quality).",
    }


def get_stats() -> Tuple[str, Dict]:
    """Return (source, stats) preferring remote telemetry, falling back to local history."""
    remote = fetch_remote_stats()
    if remote is not None and remote.get("total_plays", 0) > 0:
        return "remote", remote
    return "local", build_local_stats()


def _fmt_count(breakdown: Dict) -> str:
    if not breakdown:
        return "—"
    items = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)
    return ", ".join(f"{k} ({v})" for k, v in items[:5])


def render_stats() -> None:
    source, stats = get_stats()
    console = Console()

    header = "Your Streaming History"

    summary = Table.grid(expand=False)
    summary.add_column(style="bold", justify="left")
    summary.add_column(justify="left")
    summary.add_row("Source:", "Remote telemetry" if source == "remote" else "Local history")
    summary.add_row("Total plays:", str(stats.get("total_plays", 0)))
    summary.add_row("Unique titles:", str(stats.get("unique_titles", 0)))
    summary.add_row("Last 7 days:", str(stats.get("recent_7d", 0)))
    last = stats.get("last_played")
    if last:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            last = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    last_title = stats.get("last_title")
    if last_title:
        last = f"{last_title} (ep {stats.get('last_episode') or '?'}) — {last}"
    summary.add_row("Last played:", last or "—")

    table = Table(title="Most Watched", box=None, show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Anime")
    table.add_column("Plays", justify="right")
    table.add_column("Last episode", justify="right")
    for idx, item in enumerate(stats.get("top_titles", []), start=1):
        table.add_row(
            str(idx),
            str(item.get("title", "Unknown")),
            str(item.get("count", 0)),
            str(item.get("episode", "—")),
        )

    breakdown = Panel(
        Text.from_markup(
            "[bold]Player:[/] " + _fmt_count(stats.get("by_player", {})) + "\n"
            "[bold]Provider:[/] " + _fmt_count(stats.get("by_provider", {})) + "\n"
            "[bold]Quality:[/] " + _fmt_count(stats.get("by_quality", {}))
        ),
        title="Breakdown",
        border_style=COLOR_BORDER,
        padding=(1, 2),
    )

    note = stats.get("note")
    if note:
        console.print(Panel(note, border_style="dim", padding=(0, 2)))
    console.print(Panel(summary, title=header, border_style=COLOR_BORDER, padding=(1, 2)))
    console.print(table)
    console.print(breakdown)
