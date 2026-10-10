"""Generate the AI4SResearchAgent app icon.

Repeatable Pillow script. Run with the project conda env interpreter:
    & "C:\\Users\\Administrator\\anaconda3\\envs\\ai_research\\python.exe" assets\\make_icon.py

Outputs (next to this file):
    icon.png  1024x1024 RGBA
    icon.ico  multi-size (16/32/48/64/128/256)

Design: #1f5fae rounded-square background + white academic cap
(mortarboard diamond, trapezoid crown, button, tassel cord and drop).
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent

SIZE = 1024
CORNER_RADIUS = 180
BG = (31, 95, 174)      # #1f5fae theme blue
FG = (255, 255, 255)    # white foreground


def rounded_alpha_mask(size: int, radius: int) -> "Image.Image":
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def draw_cap(d: "ImageDraw.ImageDraw") -> None:
    cx = SIZE // 2
    # Mortarboard: perspective diamond (top, right, bottom, left)
    board = [(cx, 320), (748, 430), (cx, 540), (276, 430)]
    d.polygon(board, fill=FG)
    # Cap crown: trapezoid hanging under the board
    crown = [(400, 532), (624, 532), (668, 688), (356, 688)]
    d.polygon(crown, fill=FG)
    # Tassel cord: from the button to the right board tip, then hangs down.
    # White cord is invisible on the white board but visible against the blue bg.
    d.line([(cx, 430), (748, 430)], fill=FG, width=18)
    d.line([(748, 430), (748, 620)], fill=FG, width=18)
    # Tassel drop
    d.ellipse([748 - 26, 620, 748 + 26, 682], fill=FG)
    # Button at the board center (drawn last so the cord does not cut it)
    d.ellipse([cx - 18, 430 - 18, cx + 18, 430 + 18], fill=BG)


def main() -> None:
    img = Image.new("RGBA", (SIZE, SIZE), BG + (255,))
    draw_cap(ImageDraw.Draw(img))
    img.putalpha(rounded_alpha_mask(SIZE, CORNER_RADIUS))

    png_path = ROOT / "icon.png"
    ico_path = ROOT / "icon.ico"
    img.save(png_path)
    img.save(
        ico_path,
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print("saved:", png_path)
    print("saved:", ico_path)


if __name__ == "__main__":
    main()
