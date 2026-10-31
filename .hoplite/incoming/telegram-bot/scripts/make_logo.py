"""Generates the TEMPORARY SpirittVPN logo (assets/logo.png). Replace the file with the real logo when available."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 1280, 640


def font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default(size=size)


def main() -> None:
    img = Image.new("RGB", (W, H), "#0b1020")
    px = img.load()
    for y in range(H):  # diagonal violet -> cyan glow
        for x in range(0, W, 2):
            t = (x / W + y / H) / 2
            r, g, b = int(20 + 60 * (1 - t)), int(18 + 40 * t), int(48 + 60 * t)
            px[x, y] = (r, g, b)
            if x + 1 < W:
                px[x + 1, y] = (r, g, b)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((140, 140, 500, 500), fill=(124, 92, 255, 140))
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    img.paste(glow, (0, 0), glow)

    d = ImageDraw.Draw(img)
    # Shield
    cx, top = 320, 170
    shield = [(cx, top), (cx + 130, top + 50), (cx + 120, top + 200), (cx, top + 300), (cx - 120, top + 200), (cx - 130, top + 50)]
    d.polygon(shield, fill="#7c5cff")
    inner = [(cx, top + 30), (cx + 100, top + 70), (cx + 92, top + 190), (cx, top + 265), (cx - 92, top + 190), (cx - 100, top + 70)]
    d.polygon(inner, fill="#22d3ee")
    d.line([(cx - 45, top + 150), (cx - 10, top + 190), (cx + 55, top + 110)], fill="#0b1020", width=22, joint="curve")

    d.text((520, 220), "SpirittVPN", font=font(118), fill="#eef1ff")
    d.text((526, 370), "Твоя свобода в сети", font=font(52, bold=False), fill="#9aa5cf")
    out = ROOT / "assets" / "logo.png"
    out.parent.mkdir(exist_ok=True)
    img.save(out, optimize=True)
    print(f"saved {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
