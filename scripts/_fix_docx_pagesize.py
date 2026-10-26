# -*- coding: utf-8 -*-
"""修正 DOCX 里缺 pgSz / pgMar 的节，统一为 A4。

背景（实测 2026-10-26）：转换引擎给前两个节（封面、目录）写了
`<w:pgSz w:w="11906" w:h="16838"/>`（A4），但正文节末尾那个 body 级 sectPr
**漏写 pgSz** ⇒ Word 回退到默认纸张 Letter（612×792pt）⇒
PDF 前 2 页 A4、后 28 页 Letter，纸张混排。

修法：解包 docx，给所有缺 pgSz 的 sectPr 补上 A4 尺寸（并补 pgMar，
与已有节一致），再重新打包。不走 python-docx（避免重排内容）。

用法：
    python scripts/_fix_docx_pagesize.py --docx 资料/项目介绍文档.docx
"""
import argparse
import os
import re
import shutil
import sys
import zipfile

A4_PGSZ = '<w:pgSz w:w="11906" w:h="16838"/>'
DEFAULT_PGMAR = ('<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" '
                 'w:left="1800" w:header="851" w:footer="992" w:gutter="0"/>')


def fix_document_xml(xml):
    out = []
    pos = 0
    changed = 0
    for m in re.finditer(r"<w:sectPr\b[^>]*>.*?</w:sectPr>|<w:sectPr\b[^>]*/>", xml, re.S):
        out.append(xml[pos:m.start()])
        seg = m.group(0)
        if "<w:pgSz" not in seg:
            # 补 pgSz：schema 顺序要求它在 pgMar / pgNumType / cols 之前
            insert_at = len(seg)
            for tag in ("<w:pgMar", "<w:paperSrc", "<w:pgBorders", "<w:lnNumType",
                        "<w:pgNumType", "<w:cols", "<w:docGrid"):
                i = seg.find(tag)
                if i != -1:
                    insert_at = min(insert_at, i)
            seg = seg[:insert_at] + A4_PGSZ + seg[insert_at:]
            changed += 1
        if "<w:pgMar" not in seg:
            i = seg.find("<w:pgSz")
            j = seg.find("/>", i) + 2 if i != -1 else len(seg)
            seg = seg[:j] + DEFAULT_PGMAR + seg[j:]
            changed += 1
        out.append(seg)
        pos = m.end()
    out.append(xml[pos:])
    return "".join(out), changed


def main():
    ap = argparse.ArgumentParser(description="统一 DOCX 各节页面尺寸为 A4")
    ap.add_argument("--docx", required=True, help="目标 .docx（原地修改）")
    a = ap.parse_args()

    path = os.path.abspath(a.docx)
    if not os.path.exists(path):
        print("[err ] 文件不存在: %s" % path)
        return 1

    backup = path + ".bak"
    shutil.copy2(path, backup)

    zin = zipfile.ZipFile(path, "r")
    items = zin.infolist()
    data = {i.filename: zin.read(i.filename) for i in items}
    zin.close()

    doc = data["word/document.xml"].decode("utf-8")
    new_doc, changed = fix_document_xml(doc)
    if changed == 0:
        print("[ok  ] 所有节已有 pgSz/pgMar，无需修改")
        os.remove(backup)
        return 0
    data["word/document.xml"] = new_doc.encode("utf-8")

    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for i in items:
            zout.writestr(i, data[i.filename])
    os.replace(tmp, path)

    print("[fix ] %d 处节属性已补齐为 A4，备份: %s" % (changed, backup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
