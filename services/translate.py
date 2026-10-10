"""版式阅读与翻译呈现：原生坐标渲染、中英同步滚动（JS 联动）、原位排版 PDF、引用双向跳转 HTML。"""
import pymupdf as fitz


def native_page_html(doc, page_num):
    """文本块+坐标→HTML，文字可选中复制，尽量还原双栏版式。"""
    page = doc[page_num - 1]
    pw, ph = page.rect.width, page.rect.height
    spans_html = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = span.get("text", "")
                if not t.strip():
                    continue
                x, y, _, _ = span.get("bbox", [0, 0, 0, 0])
                fs = span.get("size", 10)
                color = span.get("color", 0)
                rgb = f"#{color:06x}" if color else "#000"
                text = (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                spans_html.append(
                    f'<span style="position:absolute;left:{x:.1f}px;top:{y:.1f}px;font-size:{fs:.1f}px;'
                    f'color:{rgb};font-family:Times New Roman,serif;white-space:pre;">{text}</span>')
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#e9edf2;}}
#page{{position:relative;width:{pw}px;height:{ph}px;background:#fff;margin:14px auto;
box-shadow:0 2px 10px rgba(0,0,0,.15);overflow:hidden;}}
#page span{{user-select:text;}}#page span:hover{{background:#fff3b0;}}
</style></head><body><div id="page">{''.join(spans_html)}</div></body></html>""", pw, ph


def bilingual_html(pairs):
    """中英对比：两栏独立滚动，JS 按比例 + 段落对齐实现真正同步滚动与联动高亮。"""
    import json
    left, right = [], []
    for i, (en, zh) in enumerate(pairs):
        left.append(f'<p data-i="{i}">{en}</p>')
        right.append(f'<p data-i="{i}">{zh or "<em>（待翻译）</em>"}</p>')
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;}} body{{margin:0;font-family:"Microsoft YaHei",sans-serif;height:100%;}}
#wrap{{display:flex;height:100vh;}} .pane{{flex:1;overflow-y:auto;padding:18px 22px;scroll-behavior:smooth;}}
#l{{background:#fbfcfe;border-right:2px solid #e3e8ef;}} #r{{background:#f4fbf6;}}
p{{font-size:13.5px;line-height:1.85;margin:0 0 12px;padding:8px 10px;border-radius:6px;border-bottom:1px solid #eef1f5;}}
p.active{{background:#fff2bf;box-shadow:inset 3px 0 #f0ad2e;}}
#bar{{position:fixed;top:8px;left:50%;transform:translateX(-50%);background:rgba(30,60,110,.9);
color:#fff;padding:5px 14px;border-radius:14px;font-size:12px;z-index:9;}}
</style></head><body><div id="bar">中英同步滚动 · 悬停/点击段落联动</div><div id="wrap">
<div class="pane" id="l">{''.join(left)}</div><div class="pane" id="r">{''.join(right)}</div></div>
<script>
const l=document.getElementById('l'),r=document.getElementById('r');
let syncing=false;
function link(src,dst){{src.addEventListener('scroll',()=>{{if(syncing)return;syncing=true;
  const s=src.scrollTop/(src.scrollHeight-src.clientHeight||1);
  dst.scrollTop=s*(dst.scrollHeight-dst.clientHeight);requestAnimationFrame(()=>syncing=false);}});}}
link(l,r);link(r,l);
function bindPane(a,b){{a.querySelectorAll('p').forEach(p=>p.addEventListener('mouseenter',()=>{{
  const i=p.dataset.i;[a,b].forEach(pn=>pn.querySelectorAll('p').forEach(q=>q.classList.toggle('active',q.dataset.i===i)));
  const t=b.querySelector(`p[data-i="${{i}}"]`);if(t)t.scrollIntoView({{block:'nearest'}});}}));}}
bindPane(l,r);bindPane(r,l);
</script></body></html>"""


def in_place_pdf(doc, page_num, zh_text, out_path):
    """原页做底，底部 1/4 区域叠加中文译文，保持原版式。"""
    pw, ph = doc[page_num - 1].rect.width, doc[page_num - 1].rect.height
    nd = fitz.open()
    np = nd.new_page(width=pw, height=ph)
    np.show_pdf_page(np.rect, doc, page_num - 1)
    top = ph * 0.74
    np.draw_rect(fitz.Rect(18, top, pw - 18, ph - 18), color=(1, 1, 1), fill=(1, 1, 1), width=0.5)
    max_chars = int((pw - 40) / 6.5)
    lines = []
    for para in zh_text.split("\n"):
        while len(para) > max_chars:
            lines.append(para[:max_chars]); para = para[max_chars:]
        if para:
            lines.append(para)
    y = top + 14
    for line in lines[:17]:
        if y > ph - 28:
            break
        try:
            np.insert_text((24, y), line, fontsize=8, fontname="china-s", color=(0, 0, 0))
        except Exception:
            np.insert_text((24, y), line, fontsize=8, color=(0, 0, 0))
        y += 12
    nd.save(out_path); nd.close()
    return out_path


def citation_jump_html(forward_ctx, backward_ctx):
    fw = forward_ctx.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    bw = backward_ctx.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{font-family:"Microsoft YaHei",sans-serif;padding:16px;}} .s{{margin-bottom:14px;padding:14px 16px;border-radius:8px;font-size:13.5px;line-height:1.8;}}
.f{{background:#eef5ff;border-left:4px solid #4A90E2;}} .b{{background:#eefbf1;border-left:4px solid #2e9d57;}}
.btn{{display:inline-block;margin-top:10px;padding:6px 14px;background:#4A90E2;color:#fff;border-radius:6px;cursor:pointer;font-size:13px;}}
</style></head><body>
<div class="s f" id="fw"><b>📘 正文引用位置</b><div>...{fw}...</div><a class="btn" href="#bw">⬇ 跳转参考文献</a></div>
<div class="s b" id="bw"><b>📗 参考文献条目</b><div>{bw}</div><a class="btn" href="#fw">⬆ 跳回正文</a></div>
<script>document.querySelectorAll('.btn').forEach(b=>b.onclick=e=>{{e.preventDefault();
document.getElementById(b.getAttribute('href').slice(1)).scrollIntoView({{behavior:'smooth'}});}});</script>
</body></html>"""
