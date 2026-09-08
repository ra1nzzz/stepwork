"""Fake ffmpeg for Playwright renderer tests: drains piped JPEG frames.

Invoked as:  python fake_ffmpeg_pipe.py <ffmpeg-like-args...>
The LAST argument is the output path.

Unlike ``fake_ffmpeg.py`` (which ignores stdin), this one reads stdin to EOF
— that is where the renderer writes the per-frame JPEGs. It counts JPEG SOI
markers and writes a small JSON report to the output path::

    {"frames": <int>, "bytes": <int>}

So a test can assert "the frames really reached ffmpeg" instead of just
"a file appeared" (which is exactly the kind of fake-success we want to
avoid — see docs/COMPLETED.md §3).

- With ``STEPWORK_FAKE_FFMPEG_SLEEP=1``: drain stdin, then sleep 30s
  **without** touching the output. A cancel test can then prove the child
  was terminated (the renderer must not sit and wait 30s).
"""

import json
import os
import sys
import time

ARGS = sys.argv[1:]
OUT = ARGS[-1] if ARGS else "out.mp4"

data = b""
try:
    data = sys.stdin.buffer.read()
except (OSError, ValueError):
    data = b""
frames = data.count(b"\xff\xd8\xff")  # JPEG SOI

if os.environ.get("STEPWORK_FAKE_FFMPEG_SLEEP") == "1":
    time.sleep(30)
    sys.exit(0)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"frames": frames, "bytes": len(data)}, f)
sys.exit(0)
