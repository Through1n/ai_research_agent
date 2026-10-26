# -*- coding: utf-8 -*-
r"""项目介绍文档一键构建：DOCX 修补 → Word COM 导出 PDF → 自检。

一次跑完（幂等，可重复执行）：
    python scripts/_build_intro_docx.py \
        --docx 资料/项目介绍文档.docx \
        --pdf  资料/项目介绍文档.pdf

做三件事：
  1. 调 scripts/_fix_docx_pagesize.py 补齐各节 pgSz/pgMar（否则 A4/Letter 混排）
  2. 调 scripts/_fix_docx_toc.py 修目录域范围与制表位（否则目录页「未找到目录项。」）
  3. 调 scripts/_docx_to_pdf.ps1 用 Word COM 导出 PDF（目录页码/页码域/图片最保真），
     然后自检：页数、页面尺寸是否全 A4、图片数、目录页是否有条目。

设计要点：
  - 修补全部落在脚本里（不手改 docx 二进制），任何人都能复现同一份交付物。
  - 自检是**硬门禁**：目录页出现「未找到目录项。」或尺寸非全 A4 直接 exit 非 0。
  - Word COM 只能由 PowerShell 调，故本脚本用 subprocess 调 powershell.exe。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def run(cmd, cwd=ROOT):
    print("[cmd ] %s" % " ".join(cmd))
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.strip().splitlines():
        print("      %s" % line)
    if p.returncode != 0:
        print("[err ] 命令失败，退出码 %d" % p.returncode)
        sys.exit(p.returncode)
    return out


def _toc_page_check(d):
    """核对目录页声明的页码与章节实际所在页是否一致。

    页面结构：封面=P1、目录=P2、正文从 P3 起且页码重起为 1
    ⇒ 正文页码 = PDF 页序 − 2。
    返回 (条目数, 不一致条数)。
    """
    import re
    toc = {}
    for line in d[1].get_text().split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(.+?)\.{3,}\s*(\d+)$", line) or \
            re.match(r"^(.+?)\s{2,}(\d+)$", line)
        if m:
            toc[m.group(1).strip()] = int(m.group(2))
    bad = 0
    for title, claimed in toc.items():
        body = None
        for i in range(2, d.page_count):
            if title in d[i].get_text():
                body = i - 1          # i 是 0 基页序，正文页码 = i - 1
                break
        if body is None or body != claimed:
            bad += 1
    return len(toc), bad


def verify(docx, pdf):
    import pymupdf
    import zipfile

    ok = True
    z = zipfile.ZipFile(docx)
    doc = z.read("word/document.xml").decode("utf-8")
    media = [n for n in z.namelist() if n.startswith("word/media/") and n.endswith(".png")]
    n_draw = doc.count("<w:drawing>")
    n_sect = doc.count("<w:sectPr")
    n_a4 = doc.count('w:w="11906" w:h="16838"')
    n_pageref = doc.count("PAGEREF")
    styles = z.read("word/styles.xml").decode("utf-8")
    has_h1 = 'w:name w:val="heading 1"' in styles
    has_h2 = 'w:name w:val="heading 2"' in styles
    lvl1 = len(re.findall(r'<w:outlineLvl w:val="0"/>', doc))
    z.close()

    d = pymupdf.open(pdf)
    sizes = {}
    for pg in d:
        k = (round(pg.rect.width), round(pg.rect.height))
        sizes[k] = sizes.get(k, 0) + 1
    toc_txt = d[1].get_text() if d.page_count > 1 else ""
    toc_broken = "未找到目录项" in toc_txt
    n_img = sum(len(pg.get_images(full=True)) for pg in d)
    pages = d.page_count
    n_toc, n_bad = _toc_page_check(d)
    d.close()

    print("[chk ] DOCX: 图片 %d 张 / drawing %d / 节 %d / A4 节 %d / PAGEREF %d"
          % (len(media), n_draw, n_sect, n_a4, n_pageref))
    print("[chk ] DOCX: 样式 heading1=%s heading2=%s，段落级 outlineLvl=0 共 %d 个"
          % (has_h1, has_h2, lvl1))
    print("[chk ] PDF : %d 页，页面尺寸 %s，图片 %d 张" % (pages, sizes, n_img))
    print("[chk ] PDF : 目录页 %s；条目 %d 条，页码与实际位置不符 %d 条"
          % ("正常（有条目）" if not toc_broken else "异常：未找到目录项", n_toc, n_bad))

    if toc_broken:
        print("[FAIL] 目录域收不到条目（TOC \\o 范围与标题层级不符）")
        ok = False
    if n_toc != 10:
        print("[FAIL] 目录条目数 %d ≠ 10" % n_toc)
        ok = False
    if n_bad:
        print("[FAIL] 目录页码与实际位置不符 %d 条" % n_bad)
        ok = False
    if sizes != {(595, 842): pages}:
        print("[FAIL] 页面尺寸非全 A4：%s" % sizes)
        ok = False
    if n_draw != len(media) or n_img != len(media):
        print("[FAIL] 图片数不一致：media %d / drawing %d / pdf %d"
              % (len(media), n_draw, n_img))
        ok = False
    if not (has_h1 and has_h2 and lvl1 == 10):
        print("[FAIL] 标题层级不自洽（heading1/heading2/outlineLvl=0）")
        ok = False
    if n_a4 != n_sect:
        print("[FAIL] 有 %d 个节缺 A4 pgSz（%d/%d）" % (n_sect - n_a4, n_a4, n_sect))
        ok = False
    return ok


def main():
    ap = argparse.ArgumentParser(description="项目介绍文档 DOCX+PDF 一键构建")
    ap.add_argument("--docx", default="资料/项目介绍文档.docx")
    ap.add_argument("--pdf", default="资料/项目介绍文档.pdf")
    ap.add_argument("--skip-verify", action="store_true")
    a = ap.parse_args()

    docx = os.path.abspath(a.docx)
    pdf = os.path.abspath(a.pdf)
    for p in (docx,):
        if not os.path.exists(p):
            print("[err ] 找不到 %s（先跑 Office 流水线 S3 生成）" % p)
            return 1

    # 1) 页面尺寸
    run([PY, os.path.join("scripts", "_fix_docx_pagesize.py"), "--docx", docx])
    # 2) 目录域
    run([PY, os.path.join("scripts", "_fix_docx_toc.py"), "--docx", docx])
    # 3) Word COM 导出 PDF
    ps = os.path.join(ROOT, "scripts", "_docx_to_pdf.ps1")
    run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", ps, "-Docx", docx, "-Pdf", pdf])

    if a.skip_verify:
        print("[done ] 已跳过自检")
        return 0
    if not verify(docx, pdf):
        print("[FAIL] 自检未通过")
        return 1
    print("[done ] DOCX + PDF 构建完成并通过自检")
    return 0


if __name__ == "__main__":
    sys.exit(main())
