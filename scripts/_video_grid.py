"""把一段视频按指定时刻抽帧并拼成带时间标注的网格图（人工核验画面内容用）。

为什么不用 ffmpeg 的 tile 滤镜：fps=X + tile 在帧数不整除时会错位叠画（实测 3x3
输出成了糊在一起的画面），而人工核验恰恰要求"这一格到底是第几秒"可靠可读。

用法：
  python scripts/_video_grid.py 资料/_构建/usage/write.mp4 --step 3 --cols 4
输出：同目录 <name>_grid.png
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

FF = (r"C:/Users/Administrator/anaconda3/envs/ai_research/Lib/site-packages/"
      "imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")
CELL_W = 460
PAD = 6
BAR = 26


def dur(path):
    r = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    import re
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--step", type=float, default=3.0)
    ap.add_argument("--cols", type=int, default=4)
    a = ap.parse_args()

    src = Path(a.video)
    total = dur(src)
    times = []
    t = a.step / 2.0
    while t < total:
        times.append(round(t, 2))
        t += a.step

    tmp = Path(tempfile.mkdtemp())
    thumbs = []
    for i, ts in enumerate(times):
        p = tmp / ("f%02d.png" % i)
        subprocess.run([FF, "-v", "error", "-y", "-ss", "%.2f" % ts, "-i", str(src),
                        "-frames:v", "1", str(p)], capture_output=True)
        if p.exists():
            thumbs.append((ts, p))

    if not thumbs:
        print("!! 没有抽到帧")
        return 1
    im0 = Image.open(thumbs[0][1])
    cell_h = int(im0.height * CELL_W / im0.width)
    rows = (len(thumbs) + a.cols - 1) // a.cols
    sheet = Image.new("RGB", (a.cols * (CELL_W + PAD) + PAD,
                              rows * (cell_h + BAR + PAD) + PAD), (245, 245, 247))
    dr = ImageDraw.Draw(sheet)
    for k, (ts, p) in enumerate(thumbs):
        r, c = divmod(k, a.cols)
        x = PAD + c * (CELL_W + PAD)
        y = PAD + r * (cell_h + BAR + PAD)
        dr.rectangle([x, y, x + CELL_W, y + BAR - 2], fill=(31, 95, 174))
        dr.text((x + 6, y + 5), "t = %.1fs" % ts, fill=(255, 255, 255))
        sheet.paste(Image.open(p).resize((CELL_W, cell_h), Image.LANCZOS), (x, y + BAR))
    out = src.with_name(src.stem + "_grid.png")
    sheet.save(out)
    print("%s  %d 格（间隔 %.1fs / 共 %.1fs）" % (out, len(thumbs), a.step, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
