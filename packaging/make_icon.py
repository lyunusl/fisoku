"""Draw packaging/fisoku.ico (a receipt on a blue tile) so the repo has no binary art."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(37, 99, 235))
# receipt with a zig-zag bottom edge
x0, y0, x1, y1 = 70, 44, 186, 206
teeth = [(x0, y1)]
for i in range(8):
    teeth += [(x0 + (i + 0.5) * (x1 - x0) / 8, y1 - 12), (x0 + (i + 1) * (x1 - x0) / 8, y1)]
d.polygon([(x0, y0), (x1, y0), *reversed(teeth[1:]), teeth[0]], fill=(255, 255, 255))
for k, w in enumerate([80, 60, 70, 50]):
    y = 72 + k * 24
    d.rounded_rectangle((x0 + 16, y, x0 + 16 + w, y + 8), radius=4, fill=(148, 163, 184))
d.rounded_rectangle((x0 + 16, 172, x1 - 16, 182), radius=5, fill=(37, 99, 235))

out = Path(__file__).with_name("fisoku.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)
