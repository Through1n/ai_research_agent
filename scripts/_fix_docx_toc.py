# -*- coding: utf-8 -*-
r"""修正 DOCX 的目录：让 TOC 域真正收得到条目，并修正目录制表位。

两个实测缺陷（2026-10-26，Word COM 导出 PDF 后目录页显示「未找到目录项。」）：

1) TOC 域范围与标题层级错位
   引擎把 HTML 的 `<h2>` 映射成样式 "heading 2"（outlineLvl=1，即 Word 第 2 级），
   `<h3>` 映射成 "heading 3"（outlineLvl=2）。但它生成的目录域是
       TOC \o "1-1" \h \z \u
   `\o "1-1"` 只收第 1 级，而文档里一个第 1 级都没有 ⇒ Word 更新域后目录被清空，
   PDF 目录页只剩一行「未找到目录项。」。
   修法：把章节标题（h2）提到 outlineLvl=0、子节（h3）提到 outlineLvl=1，
   并把样式 id 换成内置 Heading1/Heading2、样式名同步改成 "heading 1"/"heading 2"
   ——「styleId ↔ 样式名 ↔ outline 级别 ↔ 段落级 outlineLvl」四者自洽，
   Word 打开时不会把级别拉回去，导航窗格与大纲也正常。
   效果：目录域 `\o "1-1"` 正好收 10 个章节标题（一级）；
   30 个子节（level 2）不进目录 —— 与设计口径「目录只列一级」一致。
   （实测：只改 `\o "1-2"` 也能出条目，但条目会落到 TOC 2 样式，
     缩进与点线长度都变，与版式设计不符，故不采用。）

2) 目录制表位按 Letter 推导，与 A4 版心不符
   引擎写的右对齐点线制表位是 w:pos="9072"（Letter 正文宽推导值），
   而本文档页宽 11906、左右边距各 1800 ⇒ 版心宽仅 8306 twips，
   页码会被顶到右边距之外。
   修法：按最后一个 sectPr 的 pgSz/pgMar 实算版心宽，
   把 toc 1 样式与缓存目录条目里的制表位改成该值。

用法：
    python scripts/_fix_docx_toc.py --docx 资料/项目介绍文档.docx
"""
import argparse
import os
import re
import shutil
import sys
import zipfile

# styleId → (旧样式名, 新样式名, 新 styleId, 新 outlineLvl)
# 000004 = 章节标题（HTML h2）；000005 = 子节（HTML h3）；000003 = toc 1
STYLE_MAP = {
    "000004": ("heading 2", "heading 1", "Heading1", "0"),
    "000005": ("heading 3", "heading 2", "Heading2", "1"),
}
# 章节标题段落级 outlineLvl 1→0；子节 2→1
PARA_OUTLINE = {"000004": ("1", "0"), "000005": ("2", "1")}


def _text_width(doc_xml):
    """从最后一个 sectPr 取 pgSz/pgMar，算版心宽（twips）。"""
    sects = re.findall(r"<w:sectPr\b[^>]*>.*?</w:sectPr>", doc_xml, re.S)
    if not sects:
        return None
    seg = sects[-1]
    w = re.search(r'<w:pgSz[^>]*w:w="(\d+)"', seg)
    left = re.search(r'<w:pgMar[^>]*w:left="(\d+)"', seg)
    right = re.search(r'<w:pgMar[^>]*w:right="(\d+)"', seg)
    if not (w and left and right):
        return None
    return int(w.group(1)) - int(left.group(1)) - int(right.group(1))


def _fix_styles(styles_xml, tab_pos):
    """改 styleId / 样式名 / outlineLvl / toc 制表位。"""
    out = []
    pos = 0
    changed = []
    for m in re.finditer(r"<w:style\b[^>]*>.*?</w:style>", styles_xml, re.S):
        out.append(styles_xml[pos:m.start()])
        seg = m.group(0)
        pos = m.end()
        sid_m = re.search(r'w:styleId="([^"]+)"', seg)
        sid = sid_m.group(1) if sid_m else ""
        if sid in STYLE_MAP:
            old_name, new_name, new_id, lvl = STYLE_MAP[sid]
            if '<w:name w:val="%s"/>' % old_name in seg:
                seg = seg.replace('<w:name w:val="%s"/>' % old_name,
                                  '<w:name w:val="%s"/>' % new_name, 1)
                changed.append("style %s: 名 %s → %s" % (sid, old_name, new_name))
            seg = seg.replace('w:styleId="%s"' % sid, 'w:styleId="%s"' % new_id, 1)
            changed.append("style %s: styleId → %s" % (sid, new_id))
            old_lvl = "1" if lvl == "0" else "2"
            if '<w:outlineLvl w:val="%s"/>' % old_lvl in seg:
                seg = seg.replace('<w:outlineLvl w:val="%s"/>' % old_lvl,
                                  '<w:outlineLvl w:val="%s"/>' % lvl, 1)
                changed.append("style %s: outlineLvl %s → %s" % (sid, old_lvl, lvl))
        if tab_pos and '<w:name w:val="toc 1"/>' in seg:
            seg2 = re.sub(r'(<w:tab w:val="right" w:leader="dot" w:pos=")\d+(")',
                          lambda mm: mm.group(1) + str(tab_pos) + mm.group(2), seg)
            if seg2 != seg:
                changed.append("toc 1 样式: 制表位 → %d" % tab_pos)
            seg = seg2
        out.append(seg)
    out.append(styles_xml[pos:])
    return "".join(out), changed


def _fix_document(doc_xml, tab_pos):
    """改 pStyle 引用的 styleId、段落级 outlineLvl、缓存目录条目的制表位。"""
    out = []
    pos = 0
    changed = []
    for m in re.finditer(r"<w:p\b[^>]*>.*?</w:p>|<w:p\b[^>]*/>", doc_xml, re.S):
        out.append(doc_xml[pos:m.start()])
        seg = m.group(0)
        pos = m.end()
        for sid, (old_name, new_name, new_id, lvl) in STYLE_MAP.items():
            needle = '<w:pStyle w:val="%s"/>' % sid
            if needle in seg:
                seg = seg.replace(needle, '<w:pStyle w:val="%s"/>' % new_id)
        sm = re.search(r'<w:pStyle w:val="([^"]+)"/>', seg)
        sid = sm.group(1) if sm else ""
        # 段落级 outlineLvl 用旧 styleId 判定（此时已被替换成新 id）
        for old_sid, (old_lvl, new_lvl) in PARA_OUTLINE.items():
            if '<w:outlineLvl w:val="%s"/>' % old_lvl in seg:
                # 该段落 pStyle 属于此标题层级才改
                new_id = STYLE_MAP[old_sid][2]
                if sid == new_id:
                    seg = seg.replace('<w:outlineLvl w:val="%s"/>' % old_lvl,
                                      '<w:outlineLvl w:val="%s"/>' % new_lvl, 1)
                    changed.append("段落 %s: outlineLvl %s → %s" % (sid, old_lvl, new_lvl))
        if tab_pos and '<w:tab w:val="right" w:leader="dot" w:pos="' in seg:
            seg2 = re.sub(r'(<w:tab w:val="right" w:leader="dot" w:pos=")\d+(")',
                          lambda mm: mm.group(1) + str(tab_pos) + mm.group(2), seg)
            if seg2 != seg:
                changed.append("目录条目: 制表位 → %d" % tab_pos)
            seg = seg2
        out.append(seg)
    out.append(doc_xml[pos:])
    return "".join(out), changed


def main():
    ap = argparse.ArgumentParser(description="修正 DOCX 目录域范围与制表位")
    ap.add_argument("--docx", required=True, help="目标 .docx（原地修改）")
    a = ap.parse_args()

    path = os.path.abspath(a.docx)
    if not os.path.exists(path):
        print("[err ] 文件不存在: %s" % path)
        return 1

    zin = zipfile.ZipFile(path, "r")
    items = zin.infolist()
    data = {i.filename: zin.read(i.filename) for i in items}
    zin.close()

    doc = data["word/document.xml"].decode("utf-8")
    styles = data["word/styles.xml"].decode("utf-8")

    tab_pos = _text_width(doc)
    print("[info ] 版心宽 %s twips ⇒ 目录右对齐制表位" % (tab_pos if tab_pos else "未知"))

    new_doc, c1 = _fix_document(doc, tab_pos)
    new_styles, c2 = _fix_styles(styles, tab_pos)
    data["word/document.xml"] = new_doc.encode("utf-8")
    data["word/styles.xml"] = new_styles.encode("utf-8")

    backup = path + ".bak"
    shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in items:
            zout.writestr(i, data[i.filename])
    os.replace(tmp, path)

    for line in c2 + c1:
        print("[fix ] %s" % line)
    print("[fix ] 共 %d 处，备份: %s" % (len(c1) + len(c2), backup))
    print("[done] 重新导出 PDF：powershell -File scripts/_docx_to_pdf.ps1 "
          "-Docx <docx> -Pdf <pdf>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
