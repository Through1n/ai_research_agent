import streamlit as st
import os, json, time, urllib.request, urllib.parse, difflib, base64, re, math, shutil, tempfile, io, gc
from dotenv import load_dotenv
import fitz
import chromadb
from openai import OpenAI
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components
import arxiv
from PIL import Image

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
load_dotenv()

st.set_page_config(page_title="AI4S 科研助手", page_icon="🔬", layout="wide")

# ================= 内置演示数据 =================
DEMO_PAPERS = {
    "1706.03762v7.pdf": {"version": "v7", "text": """Attention Is All You Need (v7 Latest)
Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Lukasz Kaiser, Illia Polosukhin
Google Brain, Google Research, University of Toronto

Abstract: We propose the Transformer, based solely on attention mechanisms. Our model achieves 28.4 BLEU on WMT 2014 English-to-German translation. On WMT 2014 English-to-French, we achieve 41.8 BLEU after training 3.5 days on eight GPUs.

1 Introduction
Recurrent neural networks, LSTM and gated recurrent neural networks have been firmly established as state of the art.

3 Model Architecture
Most competitive neural sequence transduction models have an encoder-decoder structure.

3.1 Encoder and Decoder Stacks
Encoder: stack of N=6 identical layers. Each layer has two sub-layers.

References
[1] Yoshua Bengio, et al. A neural probabilistic language model. JMLR, 2003.
[13] Sepp Hochreiter and Jürgen Schmidhuber. Long short-term memory. 1997.
[38] Ilya Sutskever, et al. Sequence to sequence learning with neural networks. NIPS, 2014."""},
    "1706.03762v1.pdf": {"version": "v1", "text": """Attention Is All You Need (v1 First Version)
Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit

Abstract: We propose the Transformer, based solely on attention mechanisms. We achieve 28.4 BLEU on WMT 2014 English-to-German.

1 Introduction
Recurrent neural networks have been established.

3 Model Architecture
Encoder-decoder structure.

References
[1] Yoshua Bengio, et al. A neural probabilistic language model. JMLR, 2003.
[13] Sepp Hochreiter and Jürgen Schmidhuber. Long short-term memory. 1997."""},
    "1810.04805v2.pdf": {"version": "v2", "text": """BERT: Pre-training of Deep Bidirectional Transformers
Jacob Devlin, Ming-Wei Chang, Kenton Lee, Kristina Toutanova
Google AI Language

Abstract: We introduce BERT. BERT obtains new state-of-the-art results on eleven NLP tasks.

1 Introduction
Language pre-training has proven effective.

3 BERT
Two steps: pre-training and fine-tuning.

References
[13] Ashish Vaswani, et al. Attention is all you need. NIPS, 2017.
[11] Sepp Hochreiter and Jürgen Schmidhuber. Long short-term memory. 1997."""}
}

# ================= 全局状态 =================
for key, default in [
    ("processed_files", []), ("saved_api_key", ""), ("likes", {}),
    ("user_like_matrix", {}), ("user_profile", {}), ("current_user", "user_001"),
    ("last_arxiv_refresh", 0), ("arxiv_results", []), ("pubmed_results", []),
    ("web_results", []), ("batch_fetch_results", []), ("refs_structured", ""),
    ("meta_json", ""), ("paper_style", ""), ("is_scanned", False),
    ("toc_tree", []), ("tables_html", []), ("formulas_latex", ""),
    ("appendix_text", ""), ("appendix_structured", ""), ("figures_list", []),
    ("qa_history", []), ("qa_question", ""), ("read_pdf_path", None),
    ("current_pdf_page", 1), ("paras", []), ("trans", []), ("full_trans", ""),
    ("zh_text", ""), ("en_trans_result", ""), ("graph_html", ""),
    ("review_text", ""), ("future_work_text", ""), ("refined_refs", ""),
    ("df", None), ("caption_text", ""), ("research_idea", ""), ("outline_text", ""),
    ("mermaid_code", ""), ("tikz_code", ""), ("graphviz_code", ""), ("matplotlib_code", ""),
]:
    if key not in st.session_state: st.session_state[key] = default

if "img_temp_dir" not in st.session_state:
    st.session_state.img_temp_dir = tempfile.mkdtemp(prefix="ai4s_figs_")
if "collection" not in st.session_state:
    st.session_state.collection = chromadb.Client().get_or_create_collection(name="paper_collection")

def save_json(fname, data):
    with open(fname, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False)

def call_llm(client, model, messages, temperature=0.1, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(model=model, messages=messages, temperature=temperature)
        except Exception as e:
            if attempt == max_retries - 1: raise e
            time.sleep(1.5 * (attempt + 1))

def get_memory():
    try:
        import psutil
        return f"{psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024:.1f} MB"
    except: return "—"

def cosine_sim(a, b):
    if not a or not b: return 0
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
    return dot/(na*nb) if na*nb > 0 else 0

def update_profile(user_id, paper_name):
    if user_id not in st.session_state.user_profile:
        st.session_state.user_profile[user_id] = {}
    d = "通用"
    pn = paper_name.lower()
    if any(k in pn for k in ["bert", "transformer", "gpt", "attention"]): d = "NLP/大模型"
    elif any(k in pn for k in ["image", "vision", "cnn"]): d = "计算机视觉"
    elif any(k in pn for k in ["bio", "medical", "protein"]): d = "生物医学"
    elif any(k in pn for k in ["graph", "gnn"]): d = "图神经网络"
    st.session_state.user_profile[user_id][d] = st.session_state.user_profile[user_id].get(d, 0) + 1
    save_json("user_profile.json", st.session_state.user_profile)

def ddg_search(query, max_results=5):
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": query}).encode()
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        results = []
        pattern = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
        for link, title in pattern.findall(html)[:max_results]:
            title = re.sub(r'<[^>]+>', '', title).strip()
            if "uddg=" in link:
                link = urllib.parse.unquote(link.split("uddg=")[1].split("&")[0])
            results.append({"title": title, "url": link})
        return results
    except Exception as e:
        return [{"error": str(e)}]

def batch_fetch(titles):
    results = []
    for title in titles:
        title = title.strip()
        if not title or len(title) < 5: continue
        try:
            s = arxiv.Search(query=title, max_results=1, sort_by=arxiv.SortCriterion.Relevance)
            c = arxiv.Client(page_size=100, delay_seconds=2, num_retries=3)
            papers = list(c.results(s))
            if papers:
                p = papers[0]
                sim = difflib.SequenceMatcher(None, title.lower(), p.title.lower()).ratio()
                if sim > 0.5:
                    results.append({"query": title, "matched": p.title, "pdf_url": p.pdf_url, "entry_id": p.entry_id, "similarity": sim})
                else:
                    results.append({"query": title, "matched": None, "error": f"相似度低({sim:.2f})"})
            else:
                results.append({"query": title, "matched": None, "error": "arXiv 未找到"})
        except Exception as e:
            results.append({"query": title, "matched": None, "error": str(e)[:50]})
    return results

def reset_pdf_state():
    """上传新PDF时重置所有相关状态（修复 45>15 报错）"""
    for key in ["paras", "trans", "full_trans", "toc_tree", "current_pdf_page", "hl_text"]:
        if key in st.session_state: st.session_state.pop(key)
    st.session_state.current_pdf_page = 1

def render_native_page(doc, page_num):
    """原生渲染：文本块+坐标→HTML，文字可选中"""
    page = doc[page_num - 1]
    pw, ph = page.rect.width, page.rect.height
    blocks = page.get_text("dict")["blocks"]
    html_blocks = []
    for block in blocks:
        if block.get("type") != 0: continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text: continue
                x, y, _, _ = span.get("bbox", [0, 0, 0, 0])
                fs = span.get("size", 10)
                color = span.get("color", 0)
                rgb = f"#{color:06x}" if color else "#000000"
                if y < 20 or y > ph - 20: continue
                html_blocks.append(
                    f'<span style="position:absolute;left:{x}px;top:{y}px;font-size:{fs}px;'
                    f'color:{rgb};font-family:Times New Roman,serif;white-space:pre;">{text}</span>'
                )
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
    body{{margin:0;padding:0;background:#f5f5f5;}}
    #page{{position:relative;width:{pw}px;height:{ph}px;background:white;margin:10px auto;
           box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;}}
    #page span{{user-select:text;cursor:text;}}
    #page span:hover{{background:#fffacd;}}
    </style></head><body><div id="page">{''.join(html_blocks)}</div></body></html>""", pw, ph

def generate_original_layout_pdf(doc, page_num, translated_text, output_path):
    """原位PDF：原页做底 + 逐行叠加中文译文（修复之前insert_textbox失败问题）"""
    page = doc[page_num - 1]
    pw, ph = page.rect.width, page.rect.height
    new_doc = fitz.open()
    new_page = new_doc.new_page(width=pw, height=ph)
    new_page.show_pdf_page(new_page.rect, doc, page_num - 1)
    
    # 译文区域：底部1/4，白底半透明
    box_top = ph * 0.75
    new_page.draw_rect(fitz.Rect(20, box_top, pw - 20, ph - 20), color=(1,1,1), fill=(1,1,1), width=0.5)
    
    # 逐行插入中文（fitz 用 "china-s" 支持简体中文）
    y = box_top + 15
    line_height = 12
    max_chars = int((pw - 40) / 6)  # 每行最多字符数
    lines = []
    for paragraph in translated_text.split("\n"):
        while len(paragraph) > max_chars:
            lines.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        if paragraph: lines.append(paragraph)
    
    for line in lines[:18]:  # 最多18行
        if y > ph - 30: break
        try:
            new_page.insert_text((25, y), line, fontsize=8, fontname="china-s", color=(0,0,0))
        except:
            new_page.insert_text((25, y), line, fontsize=8, color=(0,0,0))
        y += line_height
    
    new_doc.save(output_path); new_doc.close()
    return output_path

def render_highlight_translate(text, api_key):
    text_esc = text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('\n','<br>')
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
    body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;padding:16px;line-height:1.9;font-size:14px;}}
    #content{{background:#fafafa;padding:16px;border-radius:8px;border:1px solid #eee;user-select:text;}}
    #popup{{display:none;position:absolute;background:#4A90E2;color:white;padding:6px 12px;
           border-radius:6px;cursor:pointer;font-size:12px;z-index:1000;}}
    #result{{display:none;margin-top:16px;padding:16px;background:#fff9e6;border-left:4px solid #ffa500;border-radius:6px;}}
    </style></head><body>
    <div id="content">{text_esc}</div>
    <div id="popup">🔍 翻译选中内容</div>
    <div id="result"></div>
    <script>
    const content=document.getElementById('content'),popup=document.getElementById('popup'),result=document.getElementById('result');
    const API_KEY="{api_key}";let selectedText='';
    content.addEventListener('mouseup',()=>{{const sel=window.getSelection();const text=sel.toString().trim();
      if(text.length>2){{selectedText=text;const range=sel.getRangeAt(0);const rect=range.getBoundingClientRect();
        popup.style.left=(rect.left+window.scrollX)+'px';popup.style.top=(rect.top+window.scrollY-35)+'px';
        popup.style.display='block';}}else popup.style.display='none';}});
    popup.addEventListener('click',async()=>{{if(!selectedText)return;popup.style.display='none';
      result.style.display='block';result.textContent='🤖 正在翻译...';
      try{{const response=await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions',{{
        method:'POST',headers:{{'Content-Type':'application/json','Authorization':'Bearer '+API_KEY}},
        body:JSON.stringify({{model:'glm-4-flash',messages:[{{role:'user',content:'请将以下学术内容精准翻译为中文：\\n\\n'+selectedText}}],temperature:0.3}})}});
        const data=await response.json();
        if(data.choices&&data.choices[0]){{result.innerHTML='<strong>📝 原文：</strong>'+selectedText+'<br><br><strong>🇨🇳 译文：</strong>'+data.choices[0].message.content;}}
        else result.textContent='❌ 翻译失败';}}catch(err){{result.textContent='❌ 网络错误：'+err.message;}}}});
    document.addEventListener('mousedown',(e)=>{{if(e.target!==popup&&!content.contains(e.target))popup.style.display='none';}});
    </script></body></html>"""
    components.html(html, height=600, scrolling=True)

def pretty_html_table(df):
    """把 DataFrame 渲染成漂亮的 HTML 表格"""
    css = """<style>.ptable{border-collapse:collapse;width:100%;font-size:13px;font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:10px 0;}
    .ptable th{background:linear-gradient(135deg,#4A90E2,#357ABD);color:white;padding:10px 14px;text-align:left;font-weight:600;}
    .ptable td{padding:9px 14px;border-bottom:1px solid #e8e8e8;color:#333;line-height:1.6;}
    .ptable tr:nth-child(even){background:#f7faff;}
    .ptable tr:hover{background:#eef4fc;}</style>"""
    return css + f'<div style="overflow-x:auto;">{df.to_html(index=False, classes="ptable", border=0, escape=False)}</div>'

def render_source_card(source, page, text, snippet_len=600):
    """溯源卡片：统一美观样式"""
    text_esc = text[:snippet_len].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    return f"""<div style="background:#f7faff;border-left:4px solid #4A90E2;border-radius:8px;
    padding:14px 18px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
    <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
      <strong style="color:#1e5fa8;font-size:14px;">📄 {source}</strong>
      <span style="background:#4A90E2;color:white;padding:2px 10px;border-radius:12px;font-size:12px;">第 {page} 页</span>
    </div>
    <div style="color:#333;font-size:13px;line-height:1.8;white-space:pre-wrap;">{text_esc}...</div>
    </div>"""

# ================= 侧边栏 =================
with st.sidebar:
    st.title("🔬 AI4S 科研助手")
    api_key = st.text_input("请输入智谱 API Key", type="password", key="saved_api_key").strip()
    if api_key:
        client = OpenAI(api_key=api_key, base_url="https://open.bigmodel.cn/api/paas/v4/", timeout=90.0, max_retries=3)
        st.success("✅ API Key 已加载")
    else:
        st.warning("请输入 API Key"); client = None
    st.markdown("---")
    st.session_state.current_user = st.text_input("当前用户 ID", value=st.session_state.current_user)
    if st.session_state.current_user in st.session_state.user_profile:
        st.caption(f"📊 画像：{st.session_state.user_profile[st.session_state.current_user]}")
    st.markdown("---")
    menu = st.radio("导航菜单", ["📁 1. 文献库管理","💬 2. 跨文献问答与溯源","📖 3. 版式阅读与智能翻译","🕸️ 4. 引用图谱与综述","✍️ 5. 写作辅助与可视化","🔍 6. 论文自动检索与追踪","🏗️ 系统架构设计","💡 技术创新亮点"])
    st.markdown("---")
    if st.button("🚀 一键加载演示数据（含 v1/v7）"):
        with st.spinner("加载中..."):
            for fname, pinfo in DEMO_PAPERS.items():
                if fname not in st.session_state.processed_files:
                    full = pinfo["text"]
                    chunks = [p.strip() for p in full.split("\n\n") if p.strip()]
                    texts, metas, ids = [], [], []
                    for i, c in enumerate(chunks):
                        texts.append(c); metas.append({"page": i+1, "source": fname, "first_page_snippet": full[:2000]}); ids.append(f"{fname}_p{i+1}")
                    for i in range(0, len(texts), 100):
                        st.session_state.collection.add(documents=texts[i:i+100], metadatas=metas[i:i+100], ids=ids[i:i+100])
                    st.session_state.processed_files.append(fname)
            st.success("✅ 已加载 3 篇（v1、v7、BERT）！"); st.rerun()
    if st.button("🗑️ 清空文献库"):
        st.session_state.processed_files = []
        st.session_state.collection = chromadb.Client().get_or_create_collection(name="paper_collection")
        st.rerun()
    st.caption(f"💾 内存：{get_memory()}")
    if st.button("🧹 清理临时图片"):
        try:
            shutil.rmtree(st.session_state.img_temp_dir)
            st.session_state.img_temp_dir = tempfile.mkdtemp(prefix="ai4s_figs_"); gc.collect(); st.success("✅ 已清理")
        except Exception as e: st.error(f"{e}")

# ================= 页面 1：文献库管理 =================
if menu == "📁 1. 文献库管理":
    st.header("📁 文献库管理"); st.caption("语义去重、标题树、表格、公式、图表、附录、OCR、多排版、参考文献结构化、版本管理建议。")
    st.info(f"💾 内存：{get_memory()}")
    
    st.subheader("🔬 元数据抽取 + 排版识别")
    meta_file = st.file_uploader("上传 PDF", type=["pdf"], key="meta_up")
    if meta_file and client:
        if st.button("🤖 抽取元数据 + 识别排版"):
            with st.spinner("抽取中..."):
                doc = fitz.open(stream=meta_file.read(), filetype="pdf")
                text = doc[0].get_text()[:3000]
                pw, ph = doc[0].rect.width, doc[0].rect.height
                blocks = doc[0].get_text("blocks")
                lb = sum(1 for b in blocks if b[0] < pw/2); rb = sum(1 for b in blocks if b[0] > pw/2)
                tc = sum(len(doc[i].get_text().strip()) for i in range(min(3, doc.page_count)))
                st.session_state.is_scanned = tc < 100
                doc.close()
                sp = f"判断排版：IEEE、ACM、Springer、Nature 或 arXiv。页面 {pw:.0f}×{ph:.0f}，{'双栏' if lb>0 and rb>0 else '单栏'}。\n文本：{text}\n直接输出一个词。"
                sr = call_llm(client, "glm-4-flash", [{"role": "user", "content": sp}], 0.1)
                st.session_state.paper_style = sr.choices[0].message.content.strip()
                mp = f"从首页抽取标题、作者、机构、摘要，输出纯净 JSON。排版：{st.session_state.paper_style}\n文本：{text}"
                res = call_llm(client, "glm-4-flash", [{"role": "user", "content": mp}], 0.1)
                mr = res.choices[0].message.content.strip()
                for mark in ["```json", "```"]:
                    if mr.startswith(mark): mr = mr[len(mark):]
                    if mr.endswith(mark): mr = mr[:-len(mark)]
                st.session_state.meta_json = mr.strip()
    if st.session_state.paper_style: st.info(f"📐 排版：{st.session_state.paper_style}")
    if st.session_state.is_scanned: st.warning("⚠️ 检测到扫描件")
    if st.session_state.meta_json:
        try: st.json(st.session_state.meta_json)
        except: st.code(st.session_state.meta_json, language="json")

    st.markdown("---")
    st.subheader("📑 标题树 / 表格 / 公式 / 图表 / 附录 / 参考文献结构化")
    detail_file = st.file_uploader("上传 PDF", type=["pdf"], key="detail_up")
    if detail_file:
        if st.button("🔍 全方位解析"):
            with st.spinner("解析中..."):
                doc = fitz.open(stream=detail_file.read(), filetype="pdf")
                st.session_state.toc_tree = doc.get_toc()
                
                # ===== 表格过滤：更严格的规则（修复问题1） =====
                tables_data = []
                for page_idx, page in enumerate(doc):
                    for t_idx, tab in enumerate(page.find_tables().tables):
                        df_tab = tab.to_pandas()
                        col_names = [str(c).strip() for c in df_tab.columns]
                        # 严格过滤：列名有意义、非空单元格多、行数合理
                        has_col_prefix = any(re.match(r'^(Col\d+|None|\d+-|.*\d+-.*)$', c) for c in col_names)
                        nan_ratio = df_tab.isna().sum().sum() / max(len(df_tab)*len(df_tab.columns), 1)
                        total_cells = len(df_tab) * len(df_tab.columns)
                        non_empty = df_tab.notna().sum().sum()
                        # 只有当列名无 "Col\d" 或 "\d+-" 前缀，NaN 少于 40%，非空单元格 > 8，才保留
                        if has_col_prefix or nan_ratio > 0.4 or total_cells < 8 or non_empty < 8:
                            continue
                        # 额外检查：列数 ≥ 2 且行数 ≥ 2
                        if len(df_tab.columns) < 2 or len(df_tab) < 2:
                            continue
                        tables_data.append({"page": page_idx+1, "index": t_idx+1, "df": df_tab})
                
                # ===== 图表提取 =====
                figures = []; seen = set(); img_dir = st.session_state.img_temp_dir
                for page_idx, page in enumerate(doc):
                    for img_idx, img in enumerate(page.get_images(full=True)):
                        xref = img[0]
                        try:
                            img_data = doc.extract_image(xref)
                            if not img_data or "image" not in img_data: continue
                            image = Image.open(io.BytesIO(img_data["image"]))
                            if image.width < 150 or image.height < 150: continue
                            sk = (image.width, image.height)
                            if sk in seen: continue
                            seen.add(sk)
                            if image.mode in ("RGBA", "LA", "P"):
                                bg = Image.new("RGB", image.size, (255, 255, 255))
                                if image.mode == "P": image = image.convert("RGBA")
                                mask = image.split()[-1] if image.mode in ("RGBA", "LA") else None
                                bg.paste(image, mask=mask); image = bg
                            elif image.mode != "RGB": image = image.convert("RGB")
                            fname = os.path.join(img_dir, f"fig_p{page_idx+1}_{img_idx+1}.png")
                            image.save(fname, "PNG")
                            figures.append({"page": page_idx+1, "index": len(figures)+1, "path": fname, "description": ""})
                        except: continue
                
                if client and figures:
                    pb = st.progress(0); stx = st.empty()
                    for idx, fig in enumerate(figures):
                        stx.text(f"🤖 理解图表 {idx+1}/{len(figures)}...")
                        try:
                            with open(fig["path"], "rb") as f: ib = base64.b64encode(f.read()).decode('utf-8')
                            vr = call_llm(client, "glm-4v", [{"role": "user", "content": [
                                {"type": "text", "text": "请用一句话描述这张学术图表的内容。专业、简洁。"},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{ib}"}}
                            ]}], 0.1, 3)
                            fig["description"] = vr.choices[0].message.content
                        except Exception as e: fig["description"] = f"（失败：{str(e)[:50]}）"
                        pb.progress((idx+1)/len(figures))
                    pb.empty(); stx.empty()
                st.session_state.figures_list = figures
                
                full_text = "".join([p.get_text() for p in doc])
                am = re.search(r"\n(?:Appendix|APPENDIX|附录)\s*[A-Z0-9]?\s*\n(.+)", full_text, re.DOTALL)
                st.session_state.appendix_text = am.group(1)[:5000] if am else "未检测到附录。"
                doc.close()
                st.session_state.tables_html = tables_data
        
        # ===== 参考文献结构化（修复问题2：要求中文，不用 unknown） =====
        if st.button("📚 结构化提取参考文献列表"):
            if client:
                with st.spinner("提取参考文献..."):
                    doc = fitz.open(stream=detail_file.read(), filetype="pdf")
                    full_text = "".join([p.get_text() for p in doc])
                    doc.close()
                    ref_text = full_text[-20000:]
                    prompt = f"""从以下论文末尾提取完整参考文献列表，输出 JSON 数组，每条：
- id: 引用编号（如 [1]）
- authors: 作者姓名（**必须是真实英文姓名，不允许填 unknown 或 N/A；如果确实识别不到，填"未识别"**）
- title: 论文标题
- year: 年份
- venue: 期刊/会议名

**重要**：如果某字段无法从原文识别，填"未识别"，绝对不要填 "unknown"。
只输出 JSON 数组。

文本：
{ref_text[:8000]}"""
                    r = call_llm(client, "glm-4-flash", [{"role": "user", "content": prompt}], 0.1, 3)
                    res = r.choices[0].message.content.strip()
                    for mark in ["```json", "```"]:
                        if res.startswith(mark): res = res[len(mark):]
                        if res.endswith(mark): res = res[:-len(mark)]
                    st.session_state.refs_structured = res.strip()
                    st.success("✅ 参考文献结构化完成！")
        
        # ===== 公式提取（修复问题3：改用 KaTeX 渲染） =====
        if st.button("🤖 提取 LaTeX 公式"):
            if client:
                with st.spinner("提取公式..."):
                    doc = fitz.open(stream=detail_file.read(), filetype="pdf")
                    ft = "".join([p.get_text() for p in doc])[:6000]
                    doc.close()
                    fp = f"""从以下论文文本提取数学公式，输出 JSON 数组，每条：
- name: 公式名称/说明
- latex: LaTeX 代码（不含 $ 符号）
只输出 JSON 数组。\n文本：{ft}"""
                    r = call_llm(client, "glm-4-flash", [{"role": "user", "content": fp}], 0.1, 3)
                    st.session_state.formulas_latex = r.choices[0].message.content
        
        if st.button("🤖 结构化附录"):
            if client and st.session_state.appendix_text != "未检测到附录。":
                with st.spinner("结构化..."):
                    r = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"按层级结构化附录：\n{st.session_state.appendix_text[:4000]}"}], 0.2, 3)
                    st.session_state.appendix_structured = r.choices[0].message.content
    
    # ===== 展示：标题树 =====
    if st.session_state.toc_tree:
        st.markdown("### 📚 标题树")
        items = []
        for idx, (level, title, page) in enumerate(st.session_state.toc_tree):
            indent = (level - 1) * 24
            if level == 1: sz, wt, ic = "15px", "bold", "📄 "
            elif level == 2: sz, wt, ic = "14px", "normal", "└─ "
            else: sz, wt, ic = "13px", "normal", "　└─ "
            items.append(f'<div style="margin-left:{indent}px;padding:4px 0;border-bottom:1px dashed #eee;font-size:{sz};font-weight:{wt};">{ic}{title} <span style="color:#aaa;font-size:11px;">· 第 {page} 页</span></div>')
        st.markdown(f'<div style="max-height:400px;overflow-y:auto;padding:10px;background:#fafafa;border-radius:8px;">{"".join(items)}</div>', unsafe_allow_html=True)
    
    # ===== 展示：表格 =====
    if st.session_state.tables_html:
        st.markdown(f"### 📊 表格（{len(st.session_state.tables_html)} 个）")
        for t in st.session_state.tables_html:
            st.markdown(f"#### 📄 第 {t['page']} 页 · 表格 {t['index']}")
            st.markdown(pretty_html_table(t["df"]), unsafe_allow_html=True)
            st.markdown("")
    elif st.session_state.tables_html == [] and st.session_state.get("tables_html") is not None:
        st.info("💡 未检测到结构清晰的表格（已自动过滤复杂/残缺表格）")
    
    # ===== 展示：参考文献结构化 =====
    if st.session_state.refs_structured:
        st.markdown("### 📚 参考文献结构化列表")
        try:
            refs_list = json.loads(st.session_state.refs_structured)
            df_refs = pd.DataFrame(refs_list)
            st.markdown(pretty_html_table(df_refs), unsafe_allow_html=True)
        except:
            st.code(st.session_state.refs_structured, language="json")
    
    # ===== 展示：公式（KaTeX渲染，修复问题3） =====
    if st.session_state.formulas_latex:
        st.markdown("### 🧮 LaTeX 公式")
        try:
            formulas = json.loads(st.session_state.formulas_latex)
            for i, f in enumerate(formulas):
                st.markdown(f"**{f.get('name', f'公式{i+1}')}**")
                latex_code = f.get('latex', '').strip().strip('$')
                if latex_code:
                    st.latex(latex_code)
        except:
            st.markdown(st.session_state.formulas_latex)
    
    # ===== 展示：图表 =====
    if st.session_state.figures_list:
        st.markdown(f"### 🖼️ 图表（{len(st.session_state.figures_list)} 张）")
        for fig in st.session_state.figures_list:
            st.markdown(f"#### 📄 第 {fig['page']} 页 · 图 {fig['index']}")
            c1, c2 = st.columns([1, 1])
            with c1: st.image(fig["path"], use_container_width=True)
            with c2:
                if fig.get("description"):
                    st.markdown("**🤖 AI 语义理解**")
                    st.info(fig['description'])
                else: st.caption("（未进行 AI 理解）")
            st.markdown("")
    
    # ===== 展示：附录 =====
    if st.session_state.appendix_text and st.session_state.appendix_text != "未检测到附录。":
        st.markdown("### 📎 附录")
        if st.session_state.appendix_structured:
            st.markdown(st.session_state.appendix_structured)
            with st.expander("查看原文"): st.text_area("", st.session_state.appendix_text, height=200)
        else: st.text_area("", st.session_state.appendix_text, height=300)

    st.markdown("---")
    uploaded_files = st.file_uploader("上传 PDF（多选）", type=["pdf"], accept_multiple_files=True)
    if uploaded_files and client:
        for uf in uploaded_files:
            if uf.name not in st.session_state.processed_files:
                with st.spinner(f"解析：{uf.name}..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(uf.read()); tp = tmp.name
                    doc = fitz.open(tp); tps = doc.page_count; fpt = doc[0].get_text()[:2000] if tps > 0 else ""
                    pb = st.progress(0); stx = st.empty()
                    texts, metas, ids = [], [], []
                    for i, page in enumerate(doc):
                        t = page.get_text()
                        if t.strip():
                            texts.append(t); metas.append({"page": i+1, "source": uf.name, "first_page_snippet": fpt}); ids.append(f"{uf.name}_p{i+1}")
                        if i % 50 == 0 or i == tps-1:
                            pb.progress((i+1)/tps); stx.text(f"解析：{uf.name} ({i+1}/{tps})")
                    pb.empty(); stx.empty(); doc.close(); os.remove(tp)
                    for i in range(0, len(texts), 100): st.session_state.collection.add(documents=texts[i:i+100], metadatas=metas[i:i+100], ids=ids[i:i+100])
                    st.session_state.processed_files.append(uf.name); st.success(f"✅ {uf.name} 入库！")
                    st.rerun()

    st.markdown("---")
    st.subheader("📷 扫描件 OCR")
    ocr_file = st.file_uploader("上传图片", type=["jpg", "jpeg", "png"], key="ocr_img")
    if ocr_file and client:
        if st.button("🔍 GLM-4V 识别"):
            with st.spinner("OCR..."):
                ib = base64.b64encode(ocr_file.read()).decode('utf-8')
                try:
                    vr = call_llm(client, "glm-4v", [{"role": "user", "content": [
                        {"type": "text", "text": "识别学术论文图片中的文字，保留公式 LaTeX。"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ib}"}}
                    ]}], 0.1, 3)
                    ot = vr.choices[0].message.content
                    st.success("✅ 识别成功！"); st.text_area("", ot, height=200)
                    st.session_state.collection.add(documents=[ot], metadatas=[{"page": 1, "source": ocr_file.name, "first_page_snippet": ot[:2000]}], ids=[f"{ocr_file.name}_p1"])
                    st.session_state.processed_files.append(ocr_file.name); st.success("已入库！")
                except Exception as e: st.error(f"失败：{e}")

    if st.session_state.processed_files:
        st.markdown("---"); st.markdown("### 📚 当前文献库")
        for f in st.session_state.processed_files: st.write(f"- {f}")
        st.markdown("---"); st.markdown("### 📑 版本语义去重与差异总结")
        version_groups = {}
        for f in st.session_state.processed_files:
            m = re.match(r"([\d\.]+)(v\d+)?\.pdf", f)
            if m:
                base_id = m.group(1)
                if base_id not in version_groups: version_groups[base_id] = []
                version_groups[base_id].append(f)
        has_dups = False
        for base_id, files in version_groups.items():
            if len(files) > 1:
                has_dups = True
                st.warning(f"⚠️ 检测到同一论文的不同版本：`{base_id}`（共 {len(files)} 个版本）")
                st.write(f"涉及文件：{', '.join(files)}")
                
                def vnum(fn):
                    m = re.search(r'v(\d+)', fn)
                    return int(m.group(1)) if m else 0
                sorted_files = sorted(files, key=vnum, reverse=True)
                latest = sorted_files[0]; older = sorted_files[1:]
                st.success(f"📌 **版本管理策略建议**：建议保留最新版 **{latest}**，旧版 {', '.join(older)} 可归档。")
                
                if st.button(f"🔍 对比 {base_id} 各版本差异", key=f"diff_{base_id}"):
                    if client:
                        with st.spinner("分析..."):
                            vtexts = []
                            for f in files:
                                d = st.session_state.collection.get(where={"source": f}, include=['documents'])
                                if d['documents']: vtexts.append(f"【{f}】\n" + " ".join(d['documents'])[:3000])
                            if len(vtexts) >= 2:
                                # ===== 修复问题4：强制中文 + 表格结构 =====
                                dp = f"""请对比以下同一论文的两个版本，用 **中文 Markdown 表格** 总结差异。
表格列：**对比维度** | **版本A** | **版本B** | **差异说明**
对比维度需包含：摘要、方法、实验数据、参考文献、作者信息等。
所有内容必须用中文，不要用英文。

版本A内容：
{vtexts[0][:3000]}

版本B内容：
{vtexts[1][:3000]}

只输出 Markdown 表格，不要额外说明。"""
                                r = call_llm(client, "glm-4-flash", [{"role": "user", "content": dp}], 0.3, 3)
                                st.markdown("### 📊 版本差异对比")
                                st.markdown(r.choices[0].message.content)
        if not has_dups:
            st.info("💡 未检测到版本重复。")

# ================= 页面 2：跨文献问答 =================
elif menu == "💬 2. 跨文献问答与溯源":
    st.header("💬 跨文献问答与溯源"); st.caption("抗幻觉 RAG，带页码锚点。")
    ex1, ex2, ex3 = st.columns(3)
    with ex1:
        if st.button("📊 表格化对比"): st.session_state.qa_question = "请从模型架构、预训练任务、实验数据三个维度对比这些论文，用中文 Markdown 表格输出"; st.rerun()
    with ex2:
        if st.button("🎯 提出什么模型"): st.session_state.qa_question = "这篇论文提出了什么模型？"; st.rerun()
    with ex3:
        if st.button("🖥️ 训练硬件"): st.session_state.qa_question = "训练用了什么 GPU？"; st.rerun()
    with st.form(key='qa_form', clear_on_submit=True):
        q = st.text_input("请输入问题", value=st.session_state.get("qa_question", ""))
        sb = st.form_submit_button("提问")
    if (sb or st.session_state.get("qa_question")):
        if client is None: st.warning("请先输入 API Key")
        else:
            qt = st.session_state.qa_question if st.session_state.get("qa_question") else q
            if qt:
                with st.spinner("检索推理中..."):
                    try:
                        results = st.session_state.collection.query(query_texts=[qt], n_results=5)
                        docs, metas = results['documents'][0], results['metadatas'][0]
                        ctx = "".join([f"\n【{m['source']} 第{m['page']}页】\n{d}\n" for d, m in zip(docs, metas)])
                        r = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"严格根据片段用中文回答。如需对比用中文 Markdown 表格。注明来源页码。\n{ctx}\n问题：{qt}"}], 0.1, 3)
                        st.session_state.qa_history.append({"question": qt, "answer": r.choices[0].message.content, "contexts": list(zip(docs, metas))})
                        st.session_state.qa_question = ""
                        st.rerun()
                    except Exception as e: st.error(f"失败：{e}")
    if st.session_state.qa_history:
        for qa in reversed(st.session_state.qa_history):
            st.markdown(f"**🙋 {qa['question']}**")
            st.markdown("### 🤖 回答"); st.markdown(qa['answer'])
            with st.expander("🔍 溯源"):
                for d, m in qa['contexts']:
                    st.markdown(render_source_card(m['source'], m['page'], d), unsafe_allow_html=True)
            st.markdown("---")

# ================= 页面 3：版式阅读与智能翻译（彻底重写，修复问题6/7/8/9） =================
elif menu == "📖 3. 版式阅读与智能翻译":
    st.header("📖 版式阅读与智能翻译")
    rf = st.file_uploader("上传 PDF", type=["pdf"], key="read_pdf")
    if rf:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(rf.read()); st.session_state.read_pdf_path = tmp.name
        reset_pdf_state()  # 关键：上传新PDF时重置所有状态
    
    if st.session_state.read_pdf_path:
        rp = st.session_state.read_pdf_path
        doc = fitz.open(rp); tps = doc.page_count
        st.caption(f"📄 已加载：{os.path.basename(rp)}（共 {tps} 页）")
        
        tabs = st.tabs(["📄 原生渲染", "🌐 同步翻译", "📄 原位 PDF", "🔗 引用跳转", "🖱️ 划词翻译", "📑 标题树"])
        
        # ===== Tab 1: 原生渲染 =====
        with tabs[0]:
            st.subheader("📄 原生渲染（文字可选中复制）")
            pn = st.number_input("页码", 1, tps, min(st.session_state.current_pdf_page, tps), key="np")
            if st.button("🎨 渲染该页"):
                h, pw, ph = render_native_page(doc, pn)
                components.html(h, height=min(int(ph * 1.15) + 40, 900), scrolling=True)
        
        # ===== Tab 2: 同步滚动翻译（修复问题6） =====
        with tabs[1]:
            st.subheader("🌐 中英同步滚动")
            pn2 = st.number_input("选择页码", 1, tps, 1, key="sp")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("📥 加载该页文本"):
                    raw = doc[pn2-1].get_text().strip()
                    if len(raw) < 50:
                        st.error("❌ 该页文本为空或太少，可能是扫描件，请使用 OCR 功能。")
                    else:
                        # ===== 修复问题7：清理文本块 =====
                        blocks = [p.strip() for p in raw.split("\n") if p.strip()]
                        # 过滤掉太短的碎片（单字符、符号）
                        paras = [b for b in blocks if len(b) > 15 and not re.match(r'^[\W\d]+$', b)]
                        if not paras:
                            st.error("❌ 该页没有可读的段落。")
                        else:
                            st.session_state.paras = paras
                            st.session_state.trans = [""] * len(paras)
                            st.session_state.full_trans = ""
                            st.success(f"✅ 已加载 {len(paras)} 个段落")
            with c2:
                if st.button("⚡ 一键翻译整页"):
                    if not st.session_state.paras:
                        st.warning("请先点击'加载该页文本'")
                    elif client:
                        with st.spinner("翻译..."):
                            ft = "\n\n".join(st.session_state.paras)
                            r = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"请将以下学术论文内容精准翻译为中文，保持段落结构，保留专业术语和公式：\n\n{ft[:5000]}"}], 0.3, 3)
                            st.session_state.full_trans = r.choices[0].message.content
            
            if st.session_state.full_trans:
                # 简化双栏渲染，避免 iframe 高度问题
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("### 📘 英文原文")
                    for i, p in enumerate(st.session_state.paras):
                        st.markdown(f'<div style="padding:8px;border-bottom:1px solid #eee;font-size:13px;line-height:1.7;">{p}</div>', unsafe_allow_html=True)
                with c2:
                    st.markdown("### 📗 中文译文")
                    trans_paras = [p.strip() for p in st.session_state.full_trans.split("\n") if p.strip()]
                    for p in trans_paras:
                        st.markdown(f'<div style="padding:8px;border-bottom:1px solid #eee;font-size:13px;line-height:1.7;background:#f0fff4;">{p}</div>', unsafe_allow_html=True)
        
        # ===== Tab 3: 原位 PDF（修复问题8） =====
        with tabs[2]:
            st.subheader("📄 原位排版 PDF 翻译输出")
            st.caption("在原 PDF 页面底部叠加中文译文，保持原版式。")
            pn3 = st.number_input("页码", 1, tps, 1, key="pp")
            if st.button("⚡ 翻译并生成 PDF"):
                if client:
                    with st.spinner("翻译并生成 PDF..."):
                        raw = doc[pn3-1].get_text()[:3000]
                        if len(raw.strip()) < 30:
                            st.error("❌ 该页无可提取文本，无法翻译。")
                        else:
                            r = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"翻译为中文，保留专业术语：\n{raw}"}], 0.3, 3)
                            tr = r.choices[0].message.content
                            op = f"translated_p{pn3}.pdf"
                            try:
                                generate_original_layout_pdf(doc, pn3, tr, op)
                                with open(op, "rb") as f: fb = f.read()
                                st.success(f"✅ 已生成！文件大小 {len(fb)/1024:.1f} KB")
                                st.download_button("📥 下载原位排版 PDF", fb, file_name=op, mime="application/pdf")
                                st.info(f"**译文预览**：\n\n{tr[:300]}")
                            except Exception as e:
                                st.error(f"生成失败：{e}")
        
        # ===== Tab 4: 引用双向跳转 =====
        with tabs[3]:
            st.subheader("🔗 引用双向跳转")
            cit = st.text_input("引用标记（如 [13]）", key="cit")
            if st.button("生成跳转锚点"):
                if cit:
                    with st.spinner("生成..."):
                        ft = "".join([p.get_text() for p in doc])
                        cn = cit.strip("[]").strip()
                        bp = re.compile(rf".{{0,200}}\[{re.escape(cn)}\].{{0,200}}", re.DOTALL)
                        bm = [m.strip().replace("\n", " ") for m in bp.findall(ft[:30000]) if len(m.strip()) > 30]
                        rt = ft[-20000:]
                        rp = re.compile(rf"\[{re.escape(cn)}\]\s*(.*?)(?=\n\s*\[|\Z)", re.DOTALL)
                        rm = [m.strip().replace("\n", " ") for m in rp.findall(rt) if 20 < len(m.strip()) < 800]
                        fw = bm[0] if bm else "未找到该引用在正文中的位置。"
                        bw = rm[0] if rm else "未找到该引用的参考文献条目。"
                        ah = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
                        body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;padding:16px;}}
                        .section{{margin-bottom:16px;padding:16px;border-radius:8px;}}
                        .forward{{background:#f0f7ff;border-left:4px solid #4A90E2;}}
                        .backward{{background:#f0fff4;border-left:4px solid #1e7d3f;}}
                        h4{{margin:0 0 10px 0;color:#333;}}.content{{line-height:1.8;font-size:14px;color:#333;}}
                        .jump-btn{{display:inline-block;margin-top:10px;padding:6px 14px;background:#4A90E2;color:white;
                                     border-radius:6px;text-decoration:none;font-size:13px;cursor:pointer;}}
                        </style></head><body>
                        <div class="section forward" id="forward"><h4>📘 正文引用位置</h4>
                        <div class="content">...{fw.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')}...</div>
                        <a class="jump-btn" href="#backward">⬇️ 跳转参考文献</a></div>
                        <div class="section backward" id="backward"><h4>📗 参考文献条目</h4>
                        <div class="content">{bw.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')}</div>
                        <a class="jump-btn" href="#forward">⬆️ 跳回正文</a></div>
                        <script>document.querySelectorAll('.jump-btn').forEach(b=>{{b.addEventListener('click',function(e){{e.preventDefault();
                        document.getElementById(this.getAttribute('href').substring(1)).scrollIntoView({{behavior:'smooth'}});}});}});</script></body></html>"""
                        components.html(ah, height=550, scrolling=True)
        
        # ===== Tab 5: 划词翻译 =====
        with tabs[4]:
            st.subheader("🖱️ 划词翻译（选中即译）")
            pn5 = st.number_input("页码", 1, tps, 1, key="hl_p")
            if st.button("📥 加载该页用于划词"):
                raw = doc[pn5-1].get_text()
                if len(raw.strip()) < 50:
                    st.error("❌ 该页文本为空，可能是扫描件。")
                else:
                    st.session_state["hl_text"] = raw[:5000]
                    st.success("✅ 已加载，请在下方文本中划词")
            if "hl_text" in st.session_state and st.session_state.get("hl_text"):
                if st.session_state.saved_api_key:
                    render_highlight_translate(st.session_state["hl_text"], st.session_state.saved_api_key)
                else:
                    st.warning("⚠️ 请先在左侧输入 API Key")
        
        # ===== Tab 6: 标题树跳转（修复问题9：页码边界检查） =====
        with tabs[5]:
            st.subheader("📑 标题树导航（点击跳转）")
            if st.button("📥 加载标题树"):
                st.session_state.toc_tree = doc.get_toc()
            if st.session_state.toc_tree:
                for idx, (level, title, page) in enumerate(st.session_state.toc_tree):
                    ic = "📄 " if level==1 else ("└─ " if level==2 else "　└─ ")
                    if st.button(f"{ic}{title} · 第 {page} 页", key=f"toc_{idx}"):
                        if page <= tps:
                            st.session_state.current_pdf_page = page
                            st.success(f"✅ 已跳转到第 {page} 页（切换上方 Tab 查看）"); st.rerun()
                        else:
                            st.error(f"❌ 页码 {page} 超出范围（共 {tps} 页）")

# ================= 页面 4：引用图谱 =================
elif menu == "🕸️ 4. 引用图谱与综述":
    st.header("🕸️ 引用图谱与综述")
    st.subheader("📊 引用图谱")
    dm = st.checkbox("⚡ 演示模式")
    if st.button("生成引用图谱"):
        if not st.session_state.processed_files: st.warning("请先上传论文")
        elif client is None: st.error("请先输入 API Key")
        else:
            with st.spinner("构建..."):
                try:
                    G = nx.DiGraph(); [G.add_node(f) for f in st.session_state.processed_files]
                    if dm:
                        ns = list(G.nodes())
                        if len(ns) >= 2: G.add_edge(ns[0], ns[1], label="引用")
                    fs = list(G.nodes())
                    for i in range(len(fs)):
                        for j in range(i+1, len(fs)):
                            f1, f2 = fs[i], fs[j]
                            id1 = f1.split('v')[0].replace(".pdf",""); id2 = f2.split('v')[0].replace(".pdf","")
                            if id1 == id2 and f1 != f2: G.add_edge(f2, f1, label="版本演进")
                    pr = nx.pagerank(G); top = sorted(pr.items(), key=lambda x: x[1], reverse=True)[:3]
                    st.markdown("### 🏆 基石论文")
                    for i, (p, s) in enumerate(top):
                        if s > 0: st.write(f"**{i+1}. {p}** ({s:.4f})")
                    ep = sorted(pr.items(), key=lambda x: x[1])[:3]
                    st.markdown("### 🌿 边缘衍生论文")
                    for i, (p, s) in enumerate(ep):
                        if s < 0.3: st.write(f"**{i+1}. {p}** ({s:.4f})")
                    net = Network(height="500px", width="100%", directed=True)
                    for n in G.nodes(): net.add_node(n, label=n, size=20+pr.get(n,0)*50, color="#4A90E2")
                    for e in G.edges(data=True):
                        if e[2].get('label') == "版本演进": net.add_edge(e[0], e[1], color="#FF4B4B", width=3, dashes=True, label="版本演进", arrows="to")
                        else: net.add_edge(e[0], e[1], color="#888888", width=2, label="引用", arrows="to")
                    net.save_graph("citation_graph.html")
                    with open("citation_graph.html", "r", encoding="utf-8") as f: st.session_state.graph_html = f.read()
                except Exception as e: st.error(f"失败：{e}")
    if st.session_state.graph_html: components.html(st.session_state.graph_html, height=520)
    
    st.markdown("---")
    st.subheader("📝 综述与前沿")
    ca, cb = st.columns(2)
    with ca:
        if st.button("一键领域综述"):
            if client and st.session_state.processed_files:
                with st.spinner("生成..."):
                    at = " ".join(st.session_state.collection.get(include=['documents'])['documents'])[:5000]
                    r = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"用中文生成领域综述：{at}"}], 0.3, 3)
                    st.session_state.review_text = r.choices[0].message.content
    with cb:
        if st.button("Future Work 提炼"):
            if client and st.session_state.processed_files:
                with st.spinner("分析..."):
                    at = " ".join(st.session_state.collection.get(include=['documents'])['documents'])[:8000]
                    r = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"提取 Future Work，中英对照 Markdown 表格：\n{at}"}], 0.3, 3)
                    st.session_state.future_work_text = r.choices[0].message.content
    if st.session_state.review_text:
        st.markdown("### 📖 综述"); st.info(st.session_state.review_text)
    if st.session_state.future_work_text:
        st.markdown("### 🚀 Future Work"); st.markdown(st.session_state.future_work_text)
    
    st.markdown("---")
    st.subheader("🎯 协同过滤推荐")
    if st.button("生成个性化推荐"):
        if client and st.session_state.processed_files:
            with st.spinner("计算..."):
                if not st.session_state.user_like_matrix:
                    st.session_state.user_like_matrix[st.session_state.current_user] = [f for f in st.session_state.processed_files if st.session_state.likes.get(f, 0) > 0]
                    if len(st.session_state.processed_files) >= 3:
                        for k, v in [("user_002", 0), ("user_003", 1), ("user_004", 2)]:
                            st.session_state.user_like_matrix[k] = st.session_state.processed_files[v:v+2]
                    save_json("user_matrix.json", st.session_state.user_like_matrix)
                pi = {p: i for i, p in enumerate(st.session_state.processed_files)}
                uv = {}
                for u, ls in st.session_state.user_like_matrix.items():
                    v = [0] * len(st.session_state.processed_files)
                    for p in ls:
                        if p in pi: v[pi[p]] = 1
                    uv[u] = v
                if st.session_state.current_user in uv:
                    tv = uv[st.session_state.current_user]
                    sims = []
                    for u, v in uv.items():
                        if u == st.session_state.current_user: continue
                        sims.append((u, cosine_sim(tv, v)))
                    sims.sort(key=lambda x: x[1], reverse=True)
                    recs = {}
                    for n, s in sims[:5]:
                        for p in st.session_state.user_like_matrix.get(n, []):
                            if p not in st.session_state.user_like_matrix.get(st.session_state.current_user, []):
                                recs[p] = recs.get(p, 0) + s
                    sr = sorted(recs.items(), key=lambda x: x[1], reverse=True)
                    if sr:
                        st.success(f"✅ 推荐（{len(st.session_state.user_like_matrix)} 用户）：")
                        for i, (p, s) in enumerate(sr[:5]):
                            st.markdown(f"**{i+1}. {p}**（得分：{s:.3f}）")
                            try:
                                rr = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"一句话说明为什么推荐《{p}》。50字内。"}], 0.3, 3)
                                st.caption(f"💡 {rr.choices[0].message.content}")
                            except: pass
                    else: st.info("暂无推荐。")
    
    st.markdown("---")
    st.subheader("👍 点赞")
    sfs = sorted(st.session_state.processed_files, key=lambda f: st.session_state.likes.get(f, 0), reverse=True)
    for f in sfs:
        lc = st.session_state.likes.get(f, 0)
        c1, c2, c3 = st.columns([3, 1, 1])
        with c1: st.write(f"{f} (🔥 {lc})")
        with c2:
            if st.button("👍", key=f"l_{f}"):
                st.session_state.likes[f] = lc + 1
                if st.session_state.current_user not in st.session_state.user_like_matrix:
                    st.session_state.user_like_matrix[st.session_state.current_user] = []
                if f not in st.session_state.user_like_matrix[st.session_state.current_user]:
                    st.session_state.user_like_matrix[st.session_state.current_user].append(f)
                update_profile(st.session_state.current_user, f)
                save_json("likes.json", st.session_state.likes)
                save_json("user_matrix.json", st.session_state.user_like_matrix)
                st.rerun()
        with c3:
            if st.button("👎", key=f"dl_{f}"):
                st.session_state.likes[f] = max(0, lc-1); save_json("likes.json", st.session_state.likes); st.rerun()

# ================= 页面 5：写作可视化 =================
elif menu == "✍️ 5. 写作辅助与可视化":
    st.header("✍️ 学术写作与可视化")
    st.subheader("📊 CSV 可视化")
    cf = st.file_uploader("上传 CSV", type=["csv"])
    if cf: st.session_state.df = pd.read_csv(cf)
    if st.session_state.df is not None:
        df = st.session_state.df; st.write(df.head())
        cols = df.columns.tolist()
        c1, c2, c3 = st.columns(3)
        with c1: x = st.selectbox("X", cols)
        with c2: y = st.selectbox("Y", cols, index=min(1, len(cols)-1))
        with c3: ct = st.selectbox("类型", ["折线图","柱状图","散点图","气泡图","三维雷达图","相关性热力图","桑基图","平行坐标图","树状图"])
        tt = "plotly_dark"
        if ct == "折线图": st.plotly_chart(px.line(df, x=x, y=y, template=tt))
        elif ct == "柱状图": st.plotly_chart(px.bar(df, x=x, y=y, template=tt))
        elif ct == "散点图": st.plotly_chart(px.scatter(df, x=x, y=y, template=tt))
        elif ct == "气泡图":
            nc = df.select_dtypes(include=['float64','int64']).columns.tolist()
            if len(nc) >= 3:
                sc = st.selectbox("气泡", nc); st.plotly_chart(px.scatter(df, x=x, y=y, size=sc, template=tt))
        elif ct == "三维雷达图":
            nc = df.select_dtypes(include=['float64','int64']).columns.tolist()
            if len(nc) >= 3:
                fg = go.Figure()
                for c in nc[:5]: fg.add_trace(go.Scatterpolar(r=df[c], theta=[f"维度 {c}"]*len(df), fill='toself', name=c))
                fg.update_layout(template=tt); st.plotly_chart(fg)
        elif ct == "相关性热力图":
            nd = df.select_dtypes(include=['float64','int64'])
            if len(nd.columns) >= 2: st.plotly_chart(px.imshow(nd.corr(), text_auto=True, aspect="auto", template=tt))
        elif ct == "桑基图":
            if len(cols) >= 3:
                s1 = st.selectbox("源", cols, 0); t1 = st.selectbox("目标", cols, 1); v1 = st.selectbox("值", cols, min(2, len(cols)-1))
                try: st.plotly_chart(px.sankey(df, source=s1, target=t1, value=v1, template=tt))
                except Exception as e: st.error(f"{e}")
        elif ct == "平行坐标图":
            nc = df.select_dtypes(include=['float64','int64']).columns.tolist()
            if len(nc) >= 3: st.plotly_chart(px.parallel_coordinates(df, dimensions=nc, template=tt))
        elif ct == "树状图":
            nc = df.select_dtypes(include=['float64','int64']).columns.tolist()
            if len(nc) >= 1:
                try: st.plotly_chart(px.treemap(df, path=[x], values=nc[0], template=tt))
                except: pass
        if st.button("🤖 生成 Figure Caption"):
            if client:
                r = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"用英文生成学术规范的 Figure Caption：{df.describe().to_string()}"}], 0.3, 3)
                st.session_state.caption_text = r.choices[0].message.content
        if st.session_state.caption_text: st.info(f"**Figure Caption:** {st.session_state.caption_text}")
    st.markdown("---")
    st.subheader("✍️ 论文框架与架构图脚本")
    idea = st.text_area("输入 Idea", value=st.session_state.get("research_idea", ""))
    st.session_state.research_idea = idea
    if st.button("生成框架与全部脚本") and idea and client:
        with st.spinner("生成中..."):
            r = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"生成论文框架，含真实参考文献：{idea}"}], 0.3, 3)
            st.session_state.outline_text = r.choices[0].message.content
            mr = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"生成 Mermaid 代码，节点ID英文，名称中文：{idea}"}], 0.1, 3)
            mc = mr.choices[0].message.content.strip()
            for m in ["```mermaid", "```"]: mc = mc.replace(m, "")
            st.session_state.mermaid_code = mc.strip()
            tr = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"生成 TikZ 代码：{idea}"}], 0.1, 3)
            st.session_state.tikz_code = tr.choices[0].message.content
            gr = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"生成 Graphviz DOT 代码：{idea}"}], 0.1, 3)
            st.session_state.graphviz_code = gr.choices[0].message.content
            pr = call_llm(client, "glm-4-flash", [{"role": "user", "content": f"生成 Matplotlib 脚本：{idea}"}], 0.1, 3)
            st.session_state.matplotlib_code = pr.choices[0].message.content
    if st.session_state.outline_text: st.markdown("### 📝 论文框架"); st.write(st.session_state.outline_text)
    if st.session_state.mermaid_code:
        st.markdown("### 📊 Mermaid"); st.code(st.session_state.mermaid_code, language="mermaid")
        components.html(f'<div class="mermaid" style="display:flex;justify-content:center;width:100%;">{st.session_state.mermaid_code}</div><script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";mermaid.initialize({{ startOnLoad: true, theme: "default", flowchart: {{ useMaxWidth: false }} }});</script>', height=800, scrolling=True)
    if st.session_state.tikz_code: st.markdown("### 📐 TikZ"); st.code(st.session_state.tikz_code, language="latex")
    if st.session_state.graphviz_code: st.markdown("### 📊 Graphviz"); st.code(st.session_state.graphviz_code, language="dot")
    if st.session_state.matplotlib_code: st.markdown("### 📈 Matplotlib"); st.code(st.session_state.matplotlib_code, language="python")

# ================= 页面 6：检索与追踪 =================
elif menu == "🔍 6. 论文自动检索与追踪":
    st.header("🔍 论文自动检索与追踪")
    
    st.subheader("📚 arXiv 检索")
    q = st.text_input("arXiv 标题或 ID", key="aq")
    if st.button("搜索 arXiv") and q and client:
        with st.spinner("检索..."):
            try:
                s = arxiv.Search(query=q, max_results=3, sort_by=arxiv.SortCriterion.Relevance)
                c = arxiv.Client(page_size=100, delay_seconds=3, num_retries=5)
                st.session_state.arxiv_results = list(c.results(s))
            except: st.warning("降级。")
    if st.session_state.arxiv_results:
        for p in st.session_state.arxiv_results:
            st.markdown(f"**{p.title}**"); st.write(f"{p.summary[:200]}...")
            gh = re.findall(r'https://github\.com/[\w\-]+/[\w\-]+', p.summary + " " + p.title)
            if gh: st.info(f"💡 {gh[0]}")
            if st.button(f"入库", key=p.entry_id):
                with st.spinner("下载..."):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp: urllib.request.urlretrieve(p.pdf_url, tmp.name); tp = tmp.name
                    doc = fitz.open(tp); txts = [pg.get_text() for pg in doc if pg.get_text().strip()]
                    ms = [{"page": i+1, "source": p.title+".pdf", "first_page_snippet": txts[0][:2000]} for i in range(len(txts))]
                    for i in range(0, len(txts), 100): st.session_state.collection.add(documents=txts[i:i+100], metadatas=ms[i:i+100], ids=[f"{p.entry_id}_p{k}" for k in range(len(txts))][i:i+100])
                    st.session_state.processed_files.append(p.title+".pdf"); st.success("入库！"); st.rerun()
    
    st.markdown("---")
    st.subheader("📋 参考文献列表批量自动抓取")
    batch_input = st.text_area("粘贴标题列表（每行一条）", height=150, key="batch_titles")
    if st.button("🚀 批量抓取入库"):
        if batch_input.strip():
            with st.spinner("批量抓取..."):
                titles = [t.strip() for t in batch_input.split("\n") if t.strip()]
                results = batch_fetch(titles)
                st.session_state.batch_fetch_results = results
                st.success(f"✅ 处理 {len(results)} 条")
    if st.session_state.batch_fetch_results:
        for r in st.session_state.batch_fetch_results:
            if r.get("matched"):
                st.markdown(f"✅ **{r['query'][:50]}** → *{r['matched'][:80]}*（相似度 {r['similarity']:.2f}）")
                if st.button(f"下载入库", key=f"bd_{r['entry_id']}"):
                    with st.spinner("下载..."):
                        try:
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp: urllib.request.urlretrieve(r['pdf_url'], tmp.name); tp = tmp.name
                            doc = fitz.open(tp); txts = [pg.get_text() for pg in doc if pg.get_text().strip()]
                            ms = [{"page": i+1, "source": r['matched']+".pdf", "first_page_snippet": txts[0][:2000]} for i in range(len(txts))]
                            for i in range(0, len(txts), 100): st.session_state.collection.add(documents=txts[i:i+100], metadatas=ms[i:i+100], ids=[f"{r['entry_id']}_p{k}" for k in range(len(txts))][i:i+100])
                            st.session_state.processed_files.append(r['matched']+".pdf"); st.success("入库！"); st.rerun()
                        except Exception as e: st.error(f"{e}")
            else:
                st.warning(f"❌ {r['query'][:50]} → {r.get('error', '未找到')}")
    
    st.markdown("---")
    st.subheader("🔍 搜索引擎（DuckDuckGo）")
    wq = st.text_input("搜索关键词", key="wq")
    if st.button("搜索网页") and wq:
        with st.spinner("搜索..."):
            st.session_state.web_results = ddg_search(wq, 5)
    if st.session_state.web_results:
        for r in st.session_state.web_results:
            if "error" in r: st.error(f"{r['error']}")
            else: st.markdown(f"**[{r['title'][:100]}]({r['url']})**")
    
    st.markdown("---")
    st.subheader("🧬 PubMed 检索")
    pq = st.text_input("关键词", key="pq")
    if st.button("搜索 PubMed") and pq:
        with st.spinner("检索..."):
            try:
                eu = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                p1 = {"db": "pubmed", "term": pq, "retmode": "json", "retmax": 3}
                with urllib.request.urlopen(eu + "?" + urllib.parse.urlencode(p1), timeout=15) as r: d = json.loads(r.read().decode())
                ids = d.get("esearchresult", {}).get("idlist", [])
                if ids:
                    su = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
                    p2 = {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
                    with urllib.request.urlopen(su + "?" + urllib.parse.urlencode(p2), timeout=15) as r: d = json.loads(r.read().decode())
                    res = []
                    for pid in ids:
                        info = d.get("result", {}).get(pid, {})
                        res.append({"pmid": pid, "title": info.get("title", ""), "authors": ", ".join([a.get("name", "") for a in info.get("authors", [])][:5]), "journal": info.get("fulljournalname", ""), "pubdate": info.get("pubdate", ""), "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/"})
                    st.session_state.pubmed_results = res
            except Exception as e: st.session_state.pubmed_results = [{"error": str(e)}]
    if st.session_state.pubmed_results:
        for r in st.session_state.pubmed_results:
            if "error" in r: st.error(f"{r['error']}")
            else:
                st.markdown(f"**{r['title']}**")
                st.write(f"作者：{r['authors']}")
                st.write(f"期刊：{r['journal']} ({r['pubdate']})")
                st.markdown(f"[🔗 PubMed]({r['url']})")
                st.markdown("---")
    
    st.markdown("---")
    st.subheader("🔬 引用含金量自动剔除")
    refs = st.text_area("粘贴参考文献", height=200, key="refs")
    if st.button("分析并剔除"):
        if client and refs.strip():
            with st.spinner("分析..."):
                rl = [l.strip() for l in refs.split("\n") if l.strip()]
                pr = f"判断每条引用的含金量：实质性 vs 应付式。分组输出保留和剔除，并说明原因（中文）。\n{chr(10).join(rl)}"
                r = call_llm(client, "glm-4-flash", [{"role": "user", "content": pr}], 0.2, 3)
                st.markdown(r.choices[0].message.content)
        else: st.warning("请粘贴")
    
    st.markdown("---")
    st.subheader("⏰ arXiv 定时追踪")
    iv = st.number_input("间隔(秒)", min_value=30, value=60, step=10)
    ar = st.checkbox("定时刷新")
    if ar:
        now = time.time()
        if now - st.session_state.last_arxiv_refresh > iv:
            st.session_state.last_arxiv_refresh = now
            try:
                s = arxiv.Search(query="cat:cs.AI", max_results=3, sort_by=arxiv.SortCriterion.SubmittedDate)
                c = arxiv.Client(page_size=100, delay_seconds=3, num_retries=5)
                st.session_state.arxiv_results = list(c.results(s))
                st.success("✅ 更新！")
            except: pass

# ================= 页面 7 & 8 =================
elif menu == "🏗️ 系统架构设计":
    st.header("🏗️ 系统架构设计")
    arch = "graph TD\nUser[用户]-->UI[Streamlit前端]\nUI-->Agent[Agent调度中心]\nAgent-->M1[文献管理]\nAgent-->M2[RAG问答]\nAgent-->M3[阅读翻译]\nAgent-->M4[图谱综述]\nAgent-->M5[写作可视化]\nAgent-->M6[自动检索]"
    components.html(f'<div class="mermaid">{arch}</div><script type="module">import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";mermaid.initialize({{ startOnLoad: true }});</script>', height=500)

elif menu == "💡 技术创新亮点":
    st.header("💡 技术创新亮点")
    st.markdown("""
1. **原生 PDF 渲染**：文字块坐标 → HTML，文字可选中复制
2. **原位排版 PDF 输出**：原页+译文叠加，逐行插入中文
3. **划词翻译**：选中即译
4. **标题树跳转**：带页码边界检查
5. **内置 v1/v7 对比数据**
6. **参考文献列表批量抓取**
7. **DuckDuckGo 搜索引擎**（无需 Key）
8. **协同过滤 + 用户偏好画像**
9. **参考文献结构化提取**（中文友好）
10. **版本管理策略建议**（自动推荐保留最新版）
11. **PubMed + arXiv 双接口**
12. **引用含金量自动剔除**
13. **精美 HTML 表格渲染**
14. **卡片式溯源展示**
15. **公式 KaTeX 实时渲染**
16. **9 种可视化图表**
17. **全链路重试 + 磁盘清理 + 内存监控**
""")