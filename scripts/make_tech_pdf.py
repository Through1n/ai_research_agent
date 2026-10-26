# -*- coding: utf-8 -*-
"""把 资料/technical_doc.md 渲染成 A4 PDF，用于**实测页数**（不是比值估算）。

用法：
    python scripts/make_tech_pdf.py                 # 默认 A4 / 图高上限 115mm
    python scripts/make_tech_pdf.py --img-max 90    # 改图高上限（mm）
    python scripts/make_tech_pdf.py --no-img        # 完全不计图（量纯文本页数）
    python scripts/make_tech_pdf.py --max-pages 0   # 关掉页数硬门禁（仅页数敏感性实验用）

输出：
    资料/_构建/technical_doc.html   中间 HTML（放在 资料/ 下，截图/ 相对路径才成立）
    资料/_构建/technical_doc.pdf    成品
    控制台打印：页数 / 字符数 / 图数 / 每页字符密度 / 版式缩放自检

设计说明（为什么要自己写这条链）：
技术文档要求 25–30 页，此前所有页数都是"去空白字符 ÷ 常数"的比值估算，
从未真正出过 PDF。本脚本给出可复跑的实测口径。
- 打印引擎：本机 Edge/Chrome headless（--print-to-pdf），与 Typora/VSCode 导出同属浏览器排版；
- 图片：`--img-max` 限制高度。截图是全页滚动长图（最高 12000px），
  不限制的话单张就占 2.85 页，必须显式约束；
- Mermaid：无渲染引擎时按 `--mermaid-h` 高度的占位框计，避免把 DOT 源码当正文码。

**为什么必须有 `--zoom`（2026-10-26 实测定案，别删）**：
Chrome 打印时对"宽于纸张"的元素会悄悄做 shrink-to-fit，把**整版**按比例缩小
（实测旧版交付物：正文 10.2pt 被印成 9.27pt、图片 94mm 变 85.3mm、页数 30）。
这个比例取决于**表格 min-content 宽度**——改个路径长度就可能让某张表不再超宽，
于是 Chrome 不再缩放，页数从 30 跳到 35（实测）。**页面数因此不可复现**。
现在把缩放写成 CSS `zoom` 显式参数：缩放因子固定、页数固定，且脚本会自检
"PDF 里有没有出现预期字号"来发现浏览器又叠了一层自动缩放。
    zoom 0.895–0.900 → 30 页（官方取值 0.900）
    zoom ≤ 0.890    → 29 页
    zoom ≥ 0.905    → 31 页
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "资料" / "technical_doc.md"
OUT_DIR = ROOT / "资料" / "_构建"

# 说明：默认档（10.2pt / 1.55 / 17mm）比 Word 默认（五号 10.5pt / 单倍行距）还松，
# 用它量出来的页数会系统性偏高。`--compact` 给出一档"紧凑但仍可读"的排版，
# 用于区分「内容真的太多」与「版式太松」这两种完全不同的病因。
CSS = """
@page {{ size: A4; margin: {margin}; }}
* {{ box-sizing: border-box; }}
body {{
  font-family: "Microsoft YaHei", "SimSun", "Segoe UI", sans-serif;
  font-size: {font}pt; line-height: {leading}; color: #111;
  /* 整版缩放：显式固化版式比例，不依赖 Chrome 的 shrink-to-fit 启发式 */
  zoom: {zoom};
}}
h1 {{ font-size: {h1}pt;  margin: 0 0 7pt; page-break-before: always; }}
h1:first-of-type {{ page-break-before: avoid; }}
h2 {{ font-size: {h2}pt; margin: 11pt 0 4pt; }}
h3 {{ font-size: {h3}pt; margin: 9pt 0 3pt; }}
h4 {{ font-size: {h4}pt; margin: 7pt 0 3pt; }}
p  {{ margin: 0 0 4pt; }}
ul, ol {{ margin: 0 0 5pt; padding-left: 15pt; }}
li {{ margin: 0 0 1pt; }}
table {{ border-collapse: collapse; width: 100%; font-size: {tfont}pt;
        margin: 3pt 0 6pt; page-break-inside: auto; }}
tr {{ page-break-inside: avoid; }}
th, td {{ border: 1px solid #999; padding: {tpad}; vertical-align: top; }}
th {{ background: #f0f0f0; font-weight: 600; }}
code {{ font-family: Consolas, "Courier New", monospace; font-size: {cfont}pt; }}
pre {{ background: #f6f6f6; border: 1px solid #ddd; padding: 3pt 5pt;
      font-size: {pfont}pt; line-height: 1.28; margin: 2pt 0 5pt;
      white-space: pre-wrap; word-break: break-all; page-break-inside: avoid; }}
blockquote {{ margin: 3pt 0; padding: 2pt 7pt; border-left: 3px solid #bbb; color: #444; }}
hr {{ border: 0; border-top: 1px solid #ccc; margin: 6pt 0; }}
img {{ display: block; margin: 5pt auto; max-width: 100%;
      max-height: {img_max}mm; width: auto; height: auto; }}
.mermaid-box {{ border: 1px dashed #888; height: {mermaid_h}mm; margin: 5pt auto;
      display: flex; align-items: center; justify-content: center;
      font-size: 8.5pt; color: #666; background: #fafafa; }}
"""

PRESET = {
    "normal": dict(margin="17mm 15mm 16mm 15mm", font=10.2, leading=1.55,
                   h1=17, h2=13.5, h3=11.5, h4=10.6, tfont=8.6, tpad="2.5pt 4pt",
                   cfont=8.8, pfont=8.2),
    # 紧凑档：正文 10pt / 行距 1.38（≈ Word 五号 1.15 倍行距，仍在正常阅读区间）
    "compact": dict(margin="14mm 13mm 13mm 13mm", font=10.0, leading=1.38,
                    h1=15.5, h2=12.5, h3=11.0, h4=10.2, tfont=8.2, tpad="2pt 3.5pt",
                    cfont=8.3, pfont=7.8),
}

MERMAID_RE = re.compile(r"<pre><code[^>]*class=\"[^\"]*mermaid[^\"]*\"[^>]*>.*?</code></pre>", re.S)


def find_browser():
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def render_html(md_text: str) -> str:
    from markdown_it import MarkdownIt
    # gfm-like 默认开了 linkify，本机没装 linkify-it-py 会在 parse 期抛异常 → 显式关掉
    try:
        md = MarkdownIt("gfm-like")
    except Exception:
        md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    try:
        md.disable("linkify")
    except Exception:
        md.options["linkify"] = False
    return md.render(md_text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img-max", type=float, default=115.0, help="图片高度上限(mm)")
    ap.add_argument("--mermaid-h", type=float, default=55.0, help="Mermaid 占位框高度(mm)")
    ap.add_argument("--no-img", action="store_true", help="移除所有图片，量纯文本页数")
    ap.add_argument("--drop", default="", help="逗号分隔的图文件名，从文档中剔除（如 p7.png,p8.png）")
    ap.add_argument("--tag", default="", help="输出文件名后缀")
    ap.add_argument("--pad", type=int, default=0,
                    help="在文末追加 N 个中文字符的占位正文，用于测「新增内容超估」的页数敏感性")
    ap.add_argument("--preset", choices=sorted(PRESET), default="normal",
                    help="排版档：normal=10.2pt/1.55/17mm，compact=10pt/1.38/14mm")
    ap.add_argument("--margin", default=None, help="覆盖页边距，如 '14mm 13mm'")
    ap.add_argument("--font", type=float, default=None, help="覆盖正文字号(pt)")
    ap.add_argument("--leading", type=float, default=None, help="覆盖行距倍数")
    ap.add_argument("--no-chapbreak", action="store_true",
                    help="取消 h1 强制另起一页（用于量化「每章另起页」到底吃掉几页）")
    ap.add_argument("--zoom", type=float, default=0.90,
                    help="整版缩放系数（写入 body 的 CSS zoom）：固化版式比例，"
                         "0.895–0.900 ⇒ 30 页；≤0.890 ⇒ 29 页；≥0.905 ⇒ 31 页")
    ap.add_argument("--max-pages", type=int, default=30,
                    help="页数硬上限（赛题 25–30 页）；0 = 不检查（页数敏感性实验用）")
    a = ap.parse_args()
    css_vars = dict(PRESET[a.preset])
    if a.margin:
        css_vars["margin"] = a.margin
    if a.font:
        css_vars["font"] = a.font
        css_vars["h1"] = round(a.font * 1.62, 1)
        css_vars["h2"] = round(a.font * 1.30, 1)
        css_vars["h3"] = round(a.font * 1.13, 1)
        css_vars["h4"] = round(a.font * 1.05, 1)
        css_vars["tfont"] = round(a.font * 0.84, 1)
        css_vars["cfont"] = round(a.font * 0.86, 1)
        css_vars["pfont"] = round(a.font * 0.80, 1)
    if a.leading:
        css_vars["leading"] = a.leading

    src = DOC.read_text(encoding="utf-8")
    if a.pad > 0:
        filler = "本节内容为占位正文，用于页数敏感性实测。"
        tail = (filler * (a.pad // len(filler) + 2))[: a.pad]
        src = src.rstrip() + "\n\n" + tail + "\n"
    if a.drop:
        names = [n.strip() for n in a.drop.split(",") if n.strip()]
        for n in names:
            src = re.sub(r"!\[[^\]]*\]\([^)]*%s\)" % re.escape(n), "", src)
    if a.no_img:
        src = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", src)

    body = render_html(src)
    n_mermaid = len(MERMAID_RE.findall(body))
    body = MERMAID_RE.sub(
        '<div class="mermaid-box">[Mermaid 图：PDF 引擎未渲染，按占位框计高度]</div>', body)
    n_img = len(re.findall(r"<img ", body))

    # 关键：把图片内联成 base64 data URI。
    # 用相对路径（file:// 子资源）时实测 7 张图只进去 4 张 —— 大图/长图会被静默丢弃，
    # 页数因此偏少，测量不可信。内联后「HTML 里有几张图，PDF 里就有几张」。
    n_img_inlined = [0]

    def _inline(m):
        # Markdown→HTML 会把非 ASCII 路径 URL 编码（`截图/p1.png` → `%E6%88%AA%E5%9B%BE/p1.png`），
        # 直接拿它拼文件系统路径必然找不到图 ⇒ 图片全被丢弃、页数测量失真。
        # 落盘前必须 unquote 回原名（浏览器侧同样按解码后的路径去取，属标准行为）。
        rel = urllib.parse.unquote(m.group(1))
        fp = (ROOT / "资料" / rel) if not os.path.isabs(rel) else Path(rel)
        if not fp.exists():
            return m.group(0)
        import base64
        b64 = base64.b64encode(fp.read_bytes()).decode("ascii")
        n_img_inlined[0] += 1
        return 'src="data:image/png;base64,%s"' % b64

    body = re.sub(r'src="([^"]+\.(?:png|jpg|jpeg))"', _inline, body)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = ("_" + a.tag) if a.tag else ""
    html_path = OUT_DIR / ("technical_doc%s.html" % tag)
    pdf_path = OUT_DIR / ("technical_doc%s.pdf" % tag)
    css = CSS.format(zoom=a.zoom, img_max=a.img_max, mermaid_h=a.mermaid_h, **css_vars)
    if a.no_chapbreak:
        css = css.replace("page-break-before: always", "page-break-before: auto")
    html = ("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><style>%s</style></head>"
            "<body>%s</body></html>" % (css, body))
    html_path.write_text(html, encoding="utf-8")

    browser = find_browser()
    if not browser:
        print("未找到 Edge/Chrome，无法打印 PDF。HTML 已生成：%s" % html_path)
        return 2
    if pdf_path.exists():
        pdf_path.unlink()
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           "--print-to-pdf=%s" % str(pdf_path), html_path.as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not pdf_path.exists():
        # 回退旧版 headless
        cmd = [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
               "--print-to-pdf=%s" % str(pdf_path), html_path.as_uri()]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not pdf_path.exists():
        print("打印失败。stdout=%s stderr=%s" % (r.stdout[-800:], r.stderr[-800:]))
        return 3

    import pymupdf
    d = pymupdf.open(str(pdf_path))
    pages = d.page_count
    chars = sum(len(p.get_text()) for p in d)
    sizes = set()
    for p in d:
        for b in p.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    sizes.add(round(s["size"], 2))
    d.close()

    # 版式缩放自检（2026-10-26）：PDF 里必须出现「CSS 字号 × zoom」这组字号。
    # 一个都没命中 ⇒ 浏览器又叠了一层 shrink-to-fit ⇒ 页数会随内容漂移，必须报错。
    expect = {k: round(css_vars[k] * a.zoom, 2)
              for k in ("font", "h1", "h2", "h3", "h4", "tfont", "cfont", "pfont")}
    hit = [k for k, v in expect.items() if any(abs(o - v) <= 0.06 for o in sizes)]

    nows = len(re.sub(r"\s+", "", src))
    print("=" * 62)
    print("输出      : %s" % pdf_path.relative_to(ROOT))
    print("实测页数  : %d 页" % pages)
    print("PDF 字符  : %s" % format(chars, ","))
    print("Markdown  : %d 行 / %s 去空白字符" % (src.count("\n") + 1, format(nows, ",")))
    # 复核 PDF 里到底落了几张图：HTML 计数与 PDF 内嵌计数必须相等，否则测量作废
    n_pdf_img = sum(len(p.get_images(full=True)) for p in pymupdf.open(str(pdf_path)))
    print("图片      : HTML %d 张 / 内联 %d 张 / PDF 内嵌 %d 张（高上限 %.0fmm×zoom %.3f）%s"
          % (n_img, n_img_inlined[0], n_pdf_img, a.img_max, a.zoom,
             "" if n_pdf_img >= n_img else "  ⚠️ 有图未渲染，本测量不可信"))
    print("Mermaid   : %d 块（占位 %.0fmm）" % (n_mermaid, a.mermaid_h))
    print("版式缩放  : zoom %.3f（正文预期 %.2fpt / 表格预期 %.2fpt）%s"
          % (a.zoom, expect["font"], expect["tfont"],
             ("✅ 命中 %s" % ",".join(hit)) if hit
             else "❌ PDF 实测字号 %s 未命中预期 ⇒ 浏览器二次缩放，页数不可复现"
                  % sorted(sizes)[:6]))
    print("密度      : %.0f 去空白字符/页" % (nows / pages))
    print("=" * 62)
    if not hit:
        print("❌ 版式缩放自检失败：Chrome 又叠了一层自动缩放，本次页数不可信，请修溢出元素或重跑")
        return 5
    if a.max_pages and pages > a.max_pages:
        print("❌ 页数 %d 超上限 %d —— 交付物不合规（需删减正文或调 --zoom）" % (pages, a.max_pages))
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
