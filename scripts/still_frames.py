"""抽帧目检：在指定时间点截图，供人眼确认画面与字幕。

从本 workspace 已验证的 ``*/scripts/still_*.py`` 搬运并泛化（原脚本写死了
``design_illust.html`` 与 ``timeline.json``，这里改成命令行参数，与
:mod:`worker.runtime.providers.renderer.playwright` 共用同一份逐帧契约）。

用法::

    python scripts/still_frames.py --html design.html --durations timeline.json 1.5 8 20
    python scripts/still_frames.py --html out/doc.html 0 3 6         # 不注入幕时长

两个原脚本踩过的坑（照搬，别改）：

1. ``__setTime`` 是**纯时间函数**，直接跳到 t 时淡入动画还没发生 →
   先回退 0.8s 再跳到目标，让动画跑完。
2. 撞上切句瞬间会取到**空字幕** → 取样前若 ``__getSentBorn() > t - 0.45``
   则把取样点前移 0.6s（页面若没暴露 ``__getSentBorn`` 则跳过该修正）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def _load_durations(path: str | None) -> list[float]:
    """从 timeline.json（``{"scenes": [{"duration": x}]}``）读幕时长。"""
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [float(s["duration"]) for s in data.get("scenes", [])]


def main() -> int:
    ap = argparse.ArgumentParser(description="Playwright 抽帧目检")
    ap.add_argument("--html", required=True, help="视觉稿 HTML（本地路径或 file:// URI）")
    ap.add_argument("--durations", default=None, help="timeline.json（提供幕时长）")
    ap.add_argument("--out-dir", default=None, help="输出目录，默认 <html 同目录>/stills")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("times", nargs="+", type=float, help="取样秒（可多个）")
    args = ap.parse_args()

    html = Path(args.html.replace("file://", "")).resolve()
    if not html.is_file():
        print(f"html not found: {html}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) if args.out_dir else html.parent / "stills"
    out_dir.mkdir(parents=True, exist_ok=True)
    durations = _load_durations(args.durations)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=1,
        )
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(
            script="window.SCENE_DURATIONS=" + json.dumps(durations) + ";"
        )
        page.goto(html.as_uri())
        page.wait_for_timeout(1200)
        if not page.evaluate("typeof window.__setTime === 'function'"):
            print("document does not define window.__setTime", file=sys.stderr)
            browser.close()
            return 3

        for t in args.times:
            # 坑 1：先回退 0.8s，让淡入动画完成
            page.evaluate(f"window.__setTime({max(t - 0.8, 0.0):.3f})")
            page.evaluate(f"window.__setTime({t:.3f})")
            # 坑 2：撞上切句瞬间 → 前移取样
            born = page.evaluate("window.__getSentBorn ? window.__getSentBorn() : -1")
            shot = t
            if isinstance(born, (int, float)) and born > t - 0.45:
                shot = t + 0.6
                page.evaluate(f"window.__setTime({shot:.3f})")
            page.wait_for_timeout(120)
            dest = out_dir / f"t{t:08.2f}.png"
            page.screenshot(path=str(dest))
            print(f"saved {dest.name}" + (f"  (shifted -> {shot:.2f}s)" if shot != t else ""))
        print("pageerrors:", errors if errors else "none")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
