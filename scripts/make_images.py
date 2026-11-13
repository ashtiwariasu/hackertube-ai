"""Render og.png (1200x630) and favicon.png from scratch with Pillow."""
import json, sys
from PIL import Image, ImageDraw, ImageFont

def font(size, bold=True):
    for p in (["/System/Library/Fonts/Supplemental/Arial Bold.ttf"] if bold else []) + \
             ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()

total = len(json.load(open("app/data/videos.json")))
W, H = 1200, 630
img = Image.new("RGB", (W, H), "#0f0f10")
d = ImageDraw.Draw(img)
d.rounded_rectangle((80, 90, 160, 170), 18, fill="#ff6600")
d.polygon([(108, 108), (108, 152), (144, 130)], fill="white")
d.text((185, 98), "Hacker", font=font(64), fill="white")
w = d.textlength("Hacker", font=font(64))
d.rounded_rectangle((185 + w + 6, 96, 185 + w + 6 + d.textlength("Tube", font=font(64)) + 20, 172), 12, fill="#ff6600")
d.text((185 + w + 16, 98), "Tube", font=font(64), fill="white")
d.text((185 + w + 40 + d.textlength("Tube", font=font(64)), 112), "AI", font=font(40), fill="#a3a3ab")
d.text((80, 240), "The YouTube videos", font=font(58), fill="white")
d.text((80, 310), "Hacker News keeps recommending.", font=font(58), fill="white")
d.text((80, 430), f"{total:,} picks from 19 years of HN comments. Now personalised:", font=font(30, False), fill="#c8c8cf")
d.text((80, 472), "connect YouTube, see the HN essentials you've missed.", font=font(30, False), fill="#c8c8cf")
d.text((80, 552), "hackertube.ai", font=font(30), fill="#ff6600")
img.save("app/static/og.png", optimize=True)
fav = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
fd = ImageDraw.Draw(fav)
fd.rounded_rectangle((0, 0, 63, 63), 14, fill="#ff6600")
fd.polygon([(24, 18), (24, 46), (46, 32)], fill="white")
fav.resize((32, 32), Image.LANCZOS).save("app/static/favicon.png")
print("ok")
