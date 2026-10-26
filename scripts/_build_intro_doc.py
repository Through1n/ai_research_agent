# -*- coding: utf-8 -*-
"""项目介绍文档（DOCX）的图片管线：裁剪 → 缩放 → data URI 内嵌。

为什么必须内嵌成 data URI：
    转换层 `doc_insert_html_content` 只接收 `html_text` 字符串，没有 base path 参数，
    引擎无法解析 `<img src="images/xxx.png">` 这类相对路径 ⇒ 图片被静默丢弃
    （document.xml 里 drawing/blip 数为 0，只剩图注文字）。engine-capabilities
    明确支持 dataURI，所以这是正解。

流程：
    1. 从 `资料/截图/p*.png`（2880 宽全页图）顶部裁 2880×2000 视口 → 缩放到 1600×1111
    2. 品牌徽标从 `品牌资源/logo.png` 缩到 256×256
    3. 把 HTML 里所有 `<img src="images/NAME.png">` 换成 `data:image/png;base64,...`

用法：
    python scripts/_build_intro_doc.py --html <stage2 html>
"""
import argparse
import base64
import io
import os
import re
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 裁剪来源：交付图名 → 源截图（素材名与页面顺序不同，按内容对齐）
CROP_SRC = {
    "fig01_p1.png": "资料/截图/p1.png",   # 智能总助手
    "fig02_p0.png": "资料/截图/p0.png",   # 文献库管理
    "fig03_p2.png": "资料/截图/p2.png",   # 跨文献问答与溯源
    "fig04_p3.png": "资料/截图/p3.png",   # 版式阅读与智能翻译
    "fig05_p6.png": "资料/截图/p6.png",   # 论文检索与追踪
    "fig06_p4.png": "资料/截图/p4.png",   # 引用图谱与综述
    "fig07_p5.png": "资料/截图/p5.png",   # 写作辅助与可视化
}
LOGO_SRC = "品牌资源/logo.png"

VIEWPORT_W = 2880
VIEWPORT_H = 2000
OUT_W = 1600
OUT_H = 1111
LOGO_OUT = 256


def _crop_resize(src_path, out_path, w, h):
    im = Image.open(src_path).convert("RGB")
    if im.width != VIEWPORT_W:
        # 视口宽度变了就按比例取顶部区域，避免拉伸变形
        scale = VIEWPORT_W / float(im.width)
        vh = min(im.height, int(VIEWPORT_H * scale))
        box = im.crop((0, 0, im.width, vh))
    else:
        box = im.crop((0, 0, VIEWPORT_W, min(VIEWPORT_H, im.height)))
    box = box.resize((w, h), Image.LANCZOS)
    box.save(out_path, format="PNG", optimize=True)
    return out_path


def _build_images(images_dir):
    os.makedirs(images_dir, exist_ok=True)
    made = {}
    for name, rel in CROP_SRC.items():
        src = os.path.join(ROOT, rel)
        if not os.path.exists(src):
            print("[skip] 源截图不存在: %s" % rel)
            continue
        out = os.path.join(images_dir, name)
        _crop_resize(src, out, OUT_W, OUT_H)
        made[name] = out
        print("[crop ] %-14s <- %s  (%d KB)" % (name, rel, os.path.getsize(out) // 1024))
    logo = os.path.join(ROOT, LOGO_SRC)
    if os.path.exists(logo):
        out = os.path.join(images_dir, "cover_logo.png")
        im = Image.open(logo).convert("RGBA").resize((LOGO_OUT, LOGO_OUT), Image.LANCZOS)
        im.save(out, format="PNG", optimize=True)
        made["cover_logo.png"] = out
        print("[logo ] cover_logo.png <- %s  (%d KB)" % (LOGO_SRC, os.path.getsize(out) // 1024))
    return made


def _inline(html_path, images_dir, made, template_path=None):
    src_path = template_path or html_path
    with io.open(src_path, encoding="utf-8") as f:
        html = f.read()

    missing = []
    total_bytes = [0]

    def repl(m):
        name = m.group(1)
        path = os.path.join(images_dir, name)
        if not os.path.exists(path):
            missing.append(name)
            return m.group(0)
        with open(path, "rb") as fh:
            raw = fh.read()
        total_bytes[0] += len(raw)
        b64 = base64.b64encode(raw).decode("ascii")
        return 'src="data:image/png;base64,%s"' % b64

    html2 = re.sub(r'src="images/([^"]+\.png)"', repl, html)
    if missing:
        print("[warn ] 未找到图片: %s" % ", ".join(sorted(set(missing))))
        return False, 0
    with io.open(html_path, "w", encoding="utf-8", newline="") as f:
        f.write(html2)
    print("[inline] %d 张图片已内联，原始字节 %d KB，HTML 现 %d KB"
          % (len(made), total_bytes[0] // 1024, os.path.getsize(html_path) // 1024))
    return True, len(made)


def main():
    ap = argparse.ArgumentParser(description="项目介绍文档图片管线")
    ap.add_argument("--html", required=True, help="stage2 的 HTML 路径（输出）")
    ap.add_argument("--template", default=None,
                    help="模板 HTML（输入）。默认 <html 同名>.template.html；"
                         "模板里用相对路径 images/x.png，脚本内联后写回 --html")
    ap.add_argument("--images-dir", default=None, help="图片目录（默认取 HTML 同级的 images/）")
    ap.add_argument("--no-crop", action="store_true", help="跳过裁剪，直接用现有 images/ 里的图")
    a = ap.parse_args()

    html_path = os.path.abspath(a.html)
    images_dir = a.images_dir or os.path.join(os.path.dirname(html_path), "images")
    template_path = a.template
    if template_path is None:
        cand = html_path[:-5] + ".template.html" if html_path.endswith(".html") else html_path + ".template.html"
        template_path = cand if os.path.exists(cand) else None
    else:
        template_path = os.path.abspath(template_path)

    made = {}
    if not a.no_crop:
        made = _build_images(images_dir)
    else:
        for n in os.listdir(images_dir):
            if n.endswith(".png"):
                made[n] = os.path.join(images_dir, n)

    ok, n = _inline(html_path, images_dir, made, template_path)
    if not ok:
        return 1
    print("[done ] %d 张图已内联到 %s" % (n, html_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
