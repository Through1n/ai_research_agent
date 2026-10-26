"""MoreHand 品牌徽标生成器（Pillow 版，可重复运行）。

设计概念「MoreHand = 多一只手的 AI 帮手」：
    圆角方形（squircle）底 → 渐变品牌蓝；中央一只**抽象的向上手掌**，
    五根手指用圆头粗线表示，指节处是浅色节点——手掌既是 MoreHand 的 Hand，
    也是 AI agent 的神经网络（节点 = 能力，连线 = 编排）；右上角一枚四角星
    spark 表示「智能/生成」。整体只用品牌强调色系 + 白/浅蓝白，不引第三色相。

小尺寸可读性（16×16 任务栏）：
    线条/节点/圆角半径不按线性缩放，而是按「光学尺寸」单独调参——小尺寸
    走粗模（去掉 spark 与掌缘弧、笔画加粗），大尺寸走细模（全细节）。
    否则 1024 的图缩到 16 会糊成一团蓝方块。

运行（项目 conda 环境）：
    & "C:\\Users\\Administrator\\anaconda3\\envs\\ai_research\\python.exe" 品牌资源\\make_logo.py

输出（本文件同级）：
    logo.svg   设计源文件（与下面 Pillow 几何一致，供矢量复用/设计稿）
    logo.png   1024x1024 全细节主图（界面里的大图/预览用）
    icon.png   256x256（Streamlit 页面图标 / 侧栏用）
    icon.ico   多尺寸 16/24/32/48/64/128/256（任务栏、标题栏、Alt+Tab）
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------- 设计令牌
# 与 ui/design.py 的强调色系保持一致（改这里必须同步改 design.py）。
BLUE_LIGHT = (47, 124, 201)      # #2F7CC9 渐变起点（左上）
BLUE_DEEP = (23, 78, 142)        # #174E8E 渐变终点（右下）
WHITE = (255, 255, 255)
NODE_TINT = (214, 230, 250)      # #D6E6FA 次级节点（关节）

CANVAS = 1024
CORNER_RATIO = 0.219             # 224/1024，squircle 圆角比例
SPARK = (0.80, 0.24)             # spark 中心（归一化）

# 五根手指：归一化 (起点, 终点)，起点在掌缘、终点是指尖
FINGERS = (
    ((0.300, 0.715), (0.418, 0.552)),   # 拇指（向左外张开）
    ((0.412, 0.742), (0.412, 0.360)),   # 食指
    ((0.505, 0.762), (0.520, 0.302)),   # 中指（最长）
    ((0.596, 0.742), (0.628, 0.368)),   # 无名指
    ((0.668, 0.712), (0.732, 0.502)),   # 小指
)
PALM_ARC = ((0.285, 0.752), (0.492, 0.900), (0.700, 0.742))  # 掌缘二次贝塞尔控制点


# ---------------------------------------------------------------- 工具
def _squircle_mask(size: int) -> Image.Image:
    r = int(round(size * CORNER_RATIO))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    return mask


def _gradient_bg(size: int) -> Image.Image:
    """左上 → 右下的品牌蓝渐变（numpy 一次算完，比逐像素快两个数量级）。"""
    ys, xs = np.mgrid[0:size, 0:size]
    t = (xs + ys) / max(1, 2 * (size - 1))
    a = np.array(BLUE_LIGHT, dtype=float)
    b = np.array(BLUE_DEEP, dtype=float)
    arr = (a[None, None, :] * (1 - t[..., None]) + b[None, None, :] * t[..., None])
    img = Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
    img.putalpha(_squircle_mask(size))
    return img


def _round_line(d: ImageDraw.ImageDraw, p0, p1, width: int, color) -> None:
    """圆头线段：line + 两端等径圆，避免小尺寸下方头被切断。"""
    d.line([p0, p1], fill=color, width=width)
    r = width / 2.0
    for p in (p0, p1):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=color)


def _quad_bezier(p0, ctrl, p1, steps: int = 48):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append((u * u * p0[0] + 2 * u * t * ctrl[0] + t * t * p1[0],
                    u * u * p0[1] + 2 * u * t * ctrl[1] + t * t * p1[1]))
    return pts


def _spark(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color) -> None:
    """四角星 spark（AI「生成」的通用记号），中缝收窄。"""
    k = 0.20
    pts = [(cx, cy - r), (cx + r * k, cy - r * k), (cx + r, cy),
           (cx + r * k, cy + r * k), (cx, cy + r), (cx - r * k, cy + r * k),
           (cx - r, cy), (cx - r * k, cy - r * k)]
    d.polygon(pts, fill=color)


# ---------------------------------------------------------------- 主体绘制
def draw_logo(size: int) -> Image.Image:
    """按目标尺寸绘制。三档「光学尺寸」，见模块 docstring：
        >= 64   细模：掌缘弧 + 关节节点 + spark（全细节）
        32–63   中模：掌缘弧 + spark，去关节节点
        <  32   剪影模：只留五根加粗圆头线，去弧/节点/spark——16px 下任何
               内部细节都会糊成蓝底上一团白，宁可只要「一只手」的剪影。
    """
    if size >= 64:
        detail, mid = True, True
    elif size >= 32:
        detail, mid = False, True
    else:
        detail, mid = False, False

    stroke = max(3, int(round(size * (0.055 if mid else 0.105))))
    node_r = max(2, int(round(size * (0.021 if detail else 0.030))))
    tip_r = max(2, int(round(size * (0.016 if detail else 0.024))))

    img = _gradient_bg(size)
    d = ImageDraw.Draw(img)

    def P(pt):
        return (pt[0] * size, pt[1] * size)

    # 掌缘弧（仅细/中模）：把手从「五根散线」收成一只手掌的轮廓
    if mid:
        arc = _quad_bezier(P(PALM_ARC[0]), P(PALM_ARC[1]), P(PALM_ARC[2]))
        for i in range(len(arc) - 1):
            _round_line(d, arc[i], arc[i + 1], int(stroke * 0.82), WHITE)

    # 五根手指：白线 + 关节浅色节点 + 指尖白点
    for (p0, p1) in FINGERS:
        a, b = P(p0), P(p1)
        _round_line(d, a, b, stroke, WHITE)
        if detail:
            jx = a[0] + (b[0] - a[0]) * 0.42
            jy = a[1] + (b[1] - a[1]) * 0.42
            d.ellipse([jx - node_r, jy - node_r, jx + node_r, jy + node_r], fill=NODE_TINT)
        d.ellipse([b[0] - tip_r, b[1] - tip_r, b[0] + tip_r, b[1] + tip_r], fill=WHITE)

    # spark：右上角，不压手
    if detail:
        _spark(d, SPARK[0] * size, SPARK[1] * size, size * 0.062, WHITE)

    return img


# ---------------------------------------------------------------- SVG 源
def _svg() -> str:
    """与 Pillow 几何一一对应的矢量源（设计稿/后续矢量复用）。"""
    fingers = "\n".join(
        f'      <line x1="{p0[0]:.3f}" y1="{p0[1]:.3f}" x2="{p1[0]:.3f}" y2="{p1[1]:.3f}" />'
        for p0, p1 in FINGERS)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" width="1024" height="1024">
  <!-- MoreHand 品牌徽标：抽象手掌 = 神经网络。令牌见 ui/design.py（BLUE_LIGHT/BLUE_DEEP）。 -->
  <defs>
    <linearGradient id="mh-bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="rgb{BLUE_LIGHT}" />
      <stop offset="1" stop-color="rgb{BLUE_DEEP}" />
    </linearGradient>
    <clipPath id="mh-squircle">
      <rect x="0" y="0" width="1" height="1" rx="{CORNER_RATIO:.3f}" ry="{CORNER_RATIO:.3f}" />
    </clipPath>
  </defs>
  <g clip-path="url(#mh-squircle)">
    <rect x="0" y="0" width="1" height="1" fill="url(#mh-bg)" />
    <g stroke="#ffffff" stroke-width="0.055" stroke-linecap="round" fill="none">
{fingers}
    </g>
    <path d="M {PALM_ARC[0][0]:.3f} {PALM_ARC[0][1]:.3f} Q {PALM_ARC[1][0]:.3f} {PALM_ARC[1][1]:.3f} {PALM_ARC[2][0]:.3f} {PALM_ARC[2][1]:.3f}"
          stroke="#ffffff" stroke-width="0.045" stroke-linecap="round" fill="none" />
    <g fill="#ffffff">
      <circle cx="{SPARK[0]:.3f}" cy="{SPARK[1]:.3f}" r="0.062" />
    </g>
  </g>
</svg>
'''


ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _write_ico(path: Path, frames: list[Image.Image]) -> None:
    """写多尺寸 ICO（PNG 条目，Vista+ 通用）。

    不用 Pillow 的 `save(..., sizes=[...])`：那条路是把同一张大图**缩**到各尺寸，
    16×16 会糊成一团；也不用 `save_all=True`——本机 Pillow 的 ICO 编码器没注册
    SAVE_ALL，直接 KeyError。所以按 ICO 容器格式手写：
        ICONDIR(6) + N×ICONDIRENTRY(16) + N×PNG 数据
    """
    import io
    import struct

    blobs = []
    for img in frames:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        blobs.append(buf.getvalue())

    offset = 6 + 16 * len(blobs)
    header = struct.pack("<HHH", 0, 1, len(blobs))
    entries = b""
    for size, blob in zip(ICO_SIZES, blobs):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                               len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def main() -> None:
    svg_path = ROOT / "logo.svg"
    svg_path.write_text(_svg(), encoding="utf-8")
    print("saved:", svg_path)

    master = draw_logo(CANVAS)
    logo_png = ROOT / "logo.png"
    master.save(logo_png)
    print("saved:", logo_png)

    icon_png = ROOT / "icon.png"
    draw_logo(256).save(icon_png)
    print("saved:", icon_png)

    # ICO：每个尺寸单独绘制（不是把大图缩一缩），保证任务栏 16px 也清晰。
    frames = [draw_logo(s) for s in ICO_SIZES]
    ico_path = ROOT / "icon.ico"
    _write_ico(ico_path, frames)
    print("saved:", ico_path, "sizes =", ICO_SIZES)


if __name__ == "__main__":
    main()
