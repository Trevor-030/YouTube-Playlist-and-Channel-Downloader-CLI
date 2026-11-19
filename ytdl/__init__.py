"""Building blocks of the `yt` command. The command line itself lives in yt_downloader.py."""

import importlib.util
import sys

__version__ = "1.2.0"

_missing = [m for m in ("yt_dlp", "rich", "click", "youtube_transcript_api") if importlib.util.find_spec(m) is None]
if _missing:
    print('Missing dependencies. Run:  pip install "yt-dlp[default]" rich click youtube-transcript-api')
    sys.exit(1)
