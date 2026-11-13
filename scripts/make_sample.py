"""Build a realistic sample 'YouTube history' screenshot from 7 catalogue videos and 3 made-up ones."""
import io, json, random, urllib.request
from PIL import Image, ImageDraw, ImageFont

videos = json.load(open("app/data/videos.json"))
random.seed(11)
pool = [v for v in videos if v["views"] > 800_000 and 20 < len(v["title"]) < 72 and v["shelf"] == "evergreen"][:120]
real = random.sample(pool, 7)
fake = [("Repotting a monstera the right way", "Leaf & Pot", "41K views", "2 days ago"),
        ("I tried every budget espresso machine so you don't have to", "Brew Review", "312K views", "5 days ago"),
        ("Brisbane to Sydney in a 2005 Corolla", "Road Trip Dan", "9.8K views", "1 week ago")]
rows = []
for v in real:
    rows.append((v["title"], v["channel"], f"{v['views_s'] if 'views_s' in v else v['views']//1000}K views".replace("KK", "K"), f"{random.randint(1, 11)} days ago", v["vid"]))
for t in fake:
    rows.append((t[0], t[1], t[2], t[3], None))
random.shuffle(rows)

def font(size, bold=False):
    for p in ([f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf"]):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()

W, H = 1280, 120 + 150 * len(rows) + 40
img = Image.new("RGB", (W, H), "#0f0f0f")
d = ImageDraw.Draw(img)
d.text((40, 38), "History", font=font(34, True), fill="#f1f1f1")
d.text((1010, 48), "Search watch history", font=font(16), fill="#aaaaaa")
d.rounded_rectangle((990, 40, 1240, 80), 20, outline="#3f3f3f")
y = 120
for title, channel, views, ago, vid in rows:
    box = (40, y, 286, y + 138)
    if vid:
        try:
            raw = urllib.request.urlopen(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", timeout=20).read()
            th = Image.open(io.BytesIO(raw)).convert("RGB")
            th = th.crop((0, 45, 480, 315)).resize((246, 138))
            img.paste(th, (40, y))
        except Exception:
            d.rounded_rectangle(box, 8, fill="#2a2a2a")
    else:
        d.rounded_rectangle(box, 8, fill="#2a2a2a")
        d.polygon([(150, y + 52), (150, y + 86), (178, y + 69)], fill="#777777")
    d.rounded_rectangle((238, y + 110, 280, y + 130), 4, fill="#000000")
    d.text((244, y + 112), f"{random.randint(4, 58)}:{random.randint(10, 59)}", font=font(13, True), fill="#ffffff")
    d.text((310, y + 4), title, font=font(19, True), fill="#f1f1f1")
    d.text((310, y + 36), f"{channel}  ·  {views}  ·  {ago}", font=font(14), fill="#aaaaaa")
    d.text((1230, y + 2), "⋮", font=font(22), fill="#aaaaaa")
    y += 150
img.save("app/static/sample-history.png", optimize=True)
json.dump({"real": [v["vid"] for v in real], "titles": [r[0] for r in rows]}, open("bench/sample_truth.json", "w"), indent=1)
print("sample rows:", len(rows), "real:", [v["title"][:40] for v in real])
