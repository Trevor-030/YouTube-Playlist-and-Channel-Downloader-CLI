"""Reading playlist and channel information from YouTube."""

import re
import yt_dlp

from ytdl.common import console


def fetch_playlist_info(url: str) -> dict | None:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except Exception as e:
            console.print(f"[red]Error fetching info: {e}[/red]")
            return None


CHANNEL_TABS = ("videos", "shorts", "streams")


_CHANNEL_RE = re.compile(r"youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)", re.I)


_TAB_RE = re.compile(r"/(videos|shorts|streams|live|featured|playlists|releases|podcasts|community|about)$", re.I)


def is_channel_url(url: str) -> bool:
    """True for @handle, /channel/UC…, /c/name and /user/name URLs (not playlists or single videos)."""
    return bool(_CHANNEL_RE.search(url)) and "list=" not in url and "watch?v=" not in url


def channel_base(url: str) -> str:
    """Strip query string and any trailing tab (/videos, /shorts, …) from a channel URL."""
    url = url.split("?")[0].split("#")[0].rstrip("/")
    return _TAB_RE.sub("", url)


def resolve_url(url: str) -> str:
    """Point a bare channel URL at its Videos tab so yt-dlp returns videos, not tab playlists."""
    return f"{channel_base(url)}/videos" if is_channel_url(url) else url
