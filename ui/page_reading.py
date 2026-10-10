"""页面 3：版式感知阅读与双向联动翻译。原生版面、中英同步滚动、原位 PDF、引用双向跳转、划词翻译、标题树。"""
import os
import re
import tempfile
import streamlit as st
from ui.common import embed_html, page_header

from config import UPLOAD_DIR, PDF_DIR
from services import parsing, writing
from services.translate import (native_page_html, bilingual_html, in_place_pdf, citation_jump_html)


def _resolve_doc(ctx):
    path = None
    up = st.file_uploader("或临时上传 PDF", type=["pdf"], key="read_up")
    if up:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(up.read()); path = tmp.name
    sel_opts = ([""] + ctx.files) if ctx.files else [""]
    sel = st.selectbox("从文献库选择", sel_opts, index=(1 if ctx.files else 0), key="read_sel")
    if not path and sel:
        # 依次查找：上传目录 → 官方测试 pdf 目录（演示语料常有同名原卷）
        for base in (UPLOAD_DIR, PDF_DIR):
            candidate = str(base / sel)
            if os.path.exists(candidate):
                path = candidate
                break
        if not path:
            st.warning("该论文为内置演示语料（纯文本入库），无原始 PDF 文件，不支持版式渲染。"
                       "如需体验阅读与翻译，请改选带 PDF 的论文或临时上传。")
    return path


def _page_paragraphs(doc, page):
    raw = doc[page - 1].get_text()
    blocks = [b.strip() for b in raw.split("\n") if b.strip()]
    return [b for b in blocks if len(b) > 15 and not re.match(r"^[\W\d_]+$", b)]


def render(ctx):
    page_header("📖 版式阅读与智能翻译", "原生坐标渲染、中英同步滚动、原位 PDF 叠加中文、引用双向跳转、划词翻译、标题树。")
    # 溯源卡片点击跳转：从 query_params 读取 paper 预下拉选中
    qp = st.query_params
    if "paper" in qp and ctx.files:
        cand = qp["paper"]
        if cand in ctx.files and "read_sel" not in st.session_state:
            st.session_state["read_sel"] = cand
    path = _resolve_doc(ctx)
    if not path:
        st.info("请先在文献库导入论文，或临时上传。"); return
    doc = parsing.open_doc(path)
    tps = doc.page_count
    st.caption(f"📄 {os.path.basename(path)}（{tps} 页）")
    # 跳转页码预填：边界钳制到 [1, tps]
    if "jump_page" in qp:
        try:
            jp = min(max(1, int(qp["jump_page"])), tps)
            for _k in ("np", "sp", "pp", "hp"):
                st.session_state.setdefault(_k, jp)
            st.caption(f"🎯 已从问答页跳转到第 {jp} 页（切换上方标签查看）")
        except (TypeError, ValueError):
            pass
    tabs = st.tabs(["📄 原生渲染", "🌐 中英同步滚动", "📑 原位 PDF", "🔗 引用跳转", "🖱️ 划词翻译", "📚 标题树"])

    # 1 原生
    with tabs[0]:
        p = st.number_input("页码", 1, tps, 1, key="np")
        if st.button("🎨 渲染该页", key="np_go"):
            h, pw, ph = native_page_html(doc, p)
            embed_html(h, height=min(int(ph * 1.18) + 30, 900), scrolling=True)

    # 2 同步滚动
    with tabs[1]:
        p2 = st.number_input("页码", 1, tps, 1, key="sp")
        if st.button("📥 加载并逐段翻译（段落对齐）", key="sp_go"):
            if not ctx.llm.available:
                st.warning("请先填入 API Key。")
            else:
                paras = _page_paragraphs(doc, p2)
                if not paras:
                    st.error("该页文本过少，可能是扫描件。")
                    return
                pairs, pb = [], st.progress(0.0)
                for i, en in enumerate(paras):
                    zh = writing.translate_to_chinese(ctx, en[:1500])
                    pairs.append((en, zh)); pb.progress((i + 1) / len(paras))
                pb.empty()
                embed_html(bilingual_html(pairs), height=760, scrolling=False)

    # 3 原位 PDF
    with tabs[2]:
        p3 = st.number_input("页码", 1, tps, 1, key="pp")
        if st.button("⚡ 翻译并生成原位 PDF", key="pp_go") and ctx.llm.available:
            try:
                p3 = min(max(1, int(p3)), tps)
                raw = doc[p3 - 1].get_text()[:3000]
                with st.spinner("⚡ 正在翻译并生成原位 PDF…"):
                    zh = writing.translate_to_chinese(ctx, raw)
                    out = os.path.join(tempfile.mkdtemp(), f"translated_p{p3}.pdf")
                    in_place_pdf(doc, p3, zh, out)
                if not os.path.exists(out):
                    st.error("PDF 生成失败：输出文件不存在。")
                else:
                    with open(out, "rb") as fh:
                        data = fh.read()
                    st.success(f"✅ 已生成（{len(data)/1024:.0f} KB）")
                    st.download_button("📥 下载", data, file_name=f"translated_p{p3}.pdf")
            except Exception as e:
                st.error(f"原位 PDF 生成失败：{str(e)[:160]}")

    # 4 引用跳转
    with tabs[3]:
        cit = st.text_input("引用标记（如 13）", key="cit")
        if st.button("🔗 生成双向跳转", key="cit_go") and cit:
            from services.graph import ref_section
            full = "".join(p.get_text() for p in doc)
            bp = re.compile(rf".{{0,180}}\[{re.escape(cit.strip('[]'))}\].{{0,180}}", re.DOTALL)
            bm = [m.strip().replace("\n", " ") for m in bp.findall(full[:30000]) if len(m.strip()) > 30]
            refs = ref_section(full)   # 锚定 References 标题区，兼容多种排版
            rp = re.compile(rf"\[{re.escape(cit.strip('[]'))}\]\s*(.*?)(?=\n\s*\[|\Z)", re.DOTALL)
            rm = [m.strip().replace("\n", " ") for m in rp.findall(refs) if 20 < len(m.strip()) < 800]
            embed_html(citation_jump_html(bm[0] if bm else "未找到正文位置。",
                                               rm[0] if rm else "未找到参考文献条目。"),
                            height=560, scrolling=True)

    # 5 划词翻译（后端代理调用模型，API Key 不下发到前端页面）
    with tabs[4]:
        p5 = st.number_input("页码", 1, tps, 1, key="hp")
        if st.button("📥 加载该页", key="hp_go"):
            st.session_state["hl"] = doc[p5 - 1].get_text()[:4500]
        if st.session_state.get("hl"):
            st.caption("在下方文本框中选中（或粘贴）要翻译的词句，点击按钮由后端调用模型翻译。")
            frag = st.text_area("选中/粘贴原文片段", height=110, key="hl_frag")
            if st.button("🌐 翻译该片段") and frag.strip():
                if not ctx.llm.available:
                    st.warning("请先在左侧填入 API Key。")
                else:
                    with st.spinner("翻译中…"):
                        st.markdown("**🇨🇳 译文：**" + writing.translate_to_chinese(ctx, frag.strip()))
        elif not ctx.llm.available:
            st.caption("请先在左侧填入 API Key。")

    # 6 标题树
    with tabs[5]:
        toc = doc.get_toc()
        if not toc:
            st.caption("未检测到内置书签目录。")
        for i, (level, title, page) in enumerate(toc):
            if st.button(f"{'　'*(level-1)}{title} · p{page}", key=f"toc{i}"):
                st.success(f"跳转到第 {page} 页（切换上方标签查看）")
    doc.close()
