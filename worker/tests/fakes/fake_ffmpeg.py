"""Fake ffmpeg for W6 tests (no real ffmpeg binary needed).

Invoked as:  python fake_ffmpeg.py <ffmpeg-like-args...>
The LAST argument is the output path.

- Default: print fake progress to stderr, touch the output file, exit 0.
- With ``--sleep`` in argv: sleep 30s (so a cancel test can fire
  the cancel event mid-run); does NOT touch output.
"""

import sys
import time

ARGS = sys.argv[1:]
OUT = ARGS[-1] if ARGS else "out.mp4"

if "--sleep" in ARGS:
    time.sleep(30)
    sys.exit(0)

sys.stderr.write("Duration: 00:00:10.00\n")
sys.stderr.write("frame=50\n")
sys.stderr.write("time=00:00:05.00\n")
sys.stderr.flush()
with open(OUT, "wb") as f:
    f.close()
sys.exit(0)
