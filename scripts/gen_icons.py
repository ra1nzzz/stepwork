"""从透明品牌母版生成 Tauri PNG 与 Windows ICO。

用法：``python scripts/gen_icons.py``

母版由 ``brand/stepwork-icon-master.png`` 提供。脚本会按非透明边界裁切，
补 5% 安全留白并缩放到正方形，确保任务栏、资源管理器和安装包中的图标
使用同一套构图。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "brand" / "stepwork-icon-master.png"
ICON_DIR = ROOT / "apps" / "desktop" / "src-tauri" / "icons"

PNG_SIZES = (32, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
MASTER_SIZE = 1024
PADDING_RATIO = 0.05


def normalized_master() -> Image.Image:
    """裁切透明边缘，并置于带安全留白的 1024px 透明画布中央。"""
    source = Image.open(SOURCE).convert("RGBA")
    bbox = source.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError(f"品牌母版完全透明：{SOURCE}")

    subject = source.crop(bbox)
    padding = round(MASTER_SIZE * PADDING_RATIO)
    available = MASTER_SIZE - 2 * padding
    subject.thumbnail((available, available), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE), (0, 0, 0, 0))
    offset = ((MASTER_SIZE - subject.width) // 2, (MASTER_SIZE - subject.height) // 2)
    canvas.alpha_composite(subject, offset)
    return canvas


def main() -> int:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    master = normalized_master()

    for size in PNG_SIZES:
        output = master.resize((size, size), Image.Resampling.LANCZOS)
        output.save(ICON_DIR / f"{size}x{size}.png", optimize=True)

    master.resize((512, 512), Image.Resampling.LANCZOS).save(
        ICON_DIR / "icon.png", optimize=True
    )
    master.resize((256, 256), Image.Resampling.LANCZOS).save(
        ICON_DIR / "icon.ico",
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
    )

    print(f"图标已从 {SOURCE} 生成到 {ICON_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
