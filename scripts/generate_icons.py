"""Rebuild bundled PNG app icons from the project's own geometric mark.

Optional development dependency: Pillow. The generated icons are committed,
so this command is not needed to run the application.
"""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parent.parent / "static"
for size in (192, 512):
    scale = size / 64
    xy = lambda points: [(round(x*scale), round(y*scale)) for x,y in points]
    image = Image.new("RGB", (size,size), "#164d45")
    draw = ImageDraw.Draw(image)
    draw.polygon(xy([(14,23),(32,13),(50,23),(50,45),(32,55),(14,45)]), fill="#f5e8cc")
    for points in [[(14,23),(32,34),(50,23)], [(32,34),(32,55)], [(23,18),(41,29)]]:
        draw.line(xy(points), fill="#164d45", width=max(1,round(3*scale)), joint="curve")
    image.save(root / f"icon-{size}.png")
print("Generated app icons.")
