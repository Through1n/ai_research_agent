"""页面 1：文献库管理。语义查重与版本决策、元数据/排版识别、细粒度结构（标题树/表格/图表/公式/参考文献/附录）。"""
import os
import json
import tempfile
import streamlit as st

from config import UPLOAD_DIR
from services import library, parsing
from ui.common import html_table, resolve_pdf, page_header, render_styled_table


def _init():
    ss = st.session_state
    ss.setdefault("pending", None)
    ss.setdefault("struct", None)
    ss.setdefault("meta_view", None)
    ss.setdefault("formula_view", None)
    ss.setdefault("ref_view", None)


def render(ctx):
    _init()
    page_header("📁 文献库管理", "基于内容 embedding 的语义去重（识别 arXiv v1/v7）、版本管理、细粒度多模态解析。")

    # ---------------- 上传 & 语义查重 ----------------
    st.subheader("⬆️ 上传 PDF（自动语义查重 / 扫描件 OCR）")
    f = st.file_uploader("选择 PDF", type=["pdf"], key="lib_up")
    if f and st.button("🔍 解析并查重"):
        data = f.read()
        bar = st.progress(0.0); note = st.empty()

        def cb(r, page):
            bar.progress(min(r, 1.0)); note.text(f"OCR/解析第 {page} 页…")

        prepared = library.prepare_pdf(ctx, data=data, filename=f.name, progress_cb=cb)
        bar.empty(); note.empty()
        if prepared["scanned"]:
            st.info("🧾 检测为扫描件，已通过多模态 OCR 提取文本。")
        if prepared["dup_name"]:
            st.session_state.pending = prepared
        else:
            p = library.commit(ctx, prepared)
            st.success(f"✅ 已入库：{p['title']}（{p['page_count']} 页，{len([c for c in ctx.chunks if c['source']==p['filename']])} 个分块）")
            st.rerun()

    pend = st.session_state.pending
    if pend:
        st.warning(f"⚠️ 语义查重命中：库中已有《{pend['dup_name']}》，内容相似度 {pend['dup_sim']:.1%}。")
        if pend.get("dup_evidence"):
            st.caption(f"🔎 判定依据：{pend['dup_evidence']}（基于正文内容指纹，非文件名）")
        st.caption("💡 建议：若为同一论文的早期版本，优先「覆盖旧版（保留新版）」以保持文献库时效性。")
        c1, c2, c3 = st.columns(3)
        if c1.button("♻️ 覆盖旧版（保留新版）"):
            p = library.commit(ctx, pend, replace=pend["dup_name"])
            st.success(f"✅ 已覆盖：{p['filename']}"); st.session_state.pending = None; st.rerun()
        if c2.button("🗂️ 保留两个版本"):
            p = library.commit(ctx, pend)
            st.success("✅ 已同时保留两版，可在下方查看版本建议。"); st.session_state.pending = None; st.rerun()
        if c3.button("❌ 取消"):
            st.session_state.pending = None; st.rerun()

    st.markdown("---")
    # ---------------- 元数据 / 排版 ----------------
    st.subheader("🪪 元数据与排版识别")
    if not ctx.files:
        st.caption("文献库为空。")
    else:
        mf = st.selectbox("选择论文", ctx.files, key="meta_sel")
        if st.button("🤖 抽取标题/作者/机构/摘要 + 判断排版") and ctx.llm.available:
            _p = resolve_pdf(mf)
            if not _p:
                st.info("该论文为内置演示语料（无原始 PDF），元数据已在入库时抽取完成，见下方文献库列表。")
            else:
                doc = parsing.open_doc(_p)
                first = doc[0].get_text()[:3000]
                pw, ph = doc[0].rect.width, doc[0].rect.height
                blocks = doc[0].get_text("blocks")
                lb = sum(1 for b in blocks if b[0] < pw / 2); rb = sum(1 for b in blocks if b[0] > pw / 2)
                layout = "双栏" if lb and rb else "单栏"
                prompt = f"从首页抽取 标题/作者/机构/摘要，输出纯净 JSON。页面 {pw:.0f}×{ph:.0f}，{layout}。\n文本：{first}"
                with st.spinner("🤖 正在抽取元数据…"):
                    st.session_state.meta_view = ctx.llm.json_chat([{"role": "user", "content": prompt}])
                doc.close()
        if st.session_state.meta_view:
            st.json(st.session_state.meta_view)

    st.markdown("---")
    # ---------------- 细粒度结构 ----------------
    st.subheader("🔬 细粒度结构解析")
    if ctx.files:
        sf = st.selectbox("选择论文", ctx.files, key="struct_sel")
        with_vision = st.checkbox("用多模态模型逐图理解（较慢/耗额度）")
        if st.button("🧩 全方位解析（标题树/表格/图表）"):
            _p = resolve_pdf(sf)
            if not _p:
                st.info("该论文为内置演示语料（无原始 PDF），版式级解析不适用；问答与图谱功能不受影响。")
            else:
                with st.spinner("🧩 正在解析标题树/表格/图表…"):
                    doc = parsing.open_doc(_p)
                    toc = parsing.extract_toc(doc)
                    tables = parsing.extract_tables(doc)
                    fig_dir = os.path.join(str(UPLOAD_DIR), "figs", sf.replace(".pdf", ""))
                    figs = parsing.extract_figures(doc, fig_dir)
                    if with_vision and ctx.llm.available and figs:
                        pb = st.progress(0.0)
                        parsing.describe_figures(figs, ctx.llm, lambda r: pb.progress(min(r, 1.0)))
                        pb.empty()
                    doc.close()
                st.session_state.struct = {"file": sf, "toc": toc, "tables": tables, "figs": figs}

        stobj = st.session_state.struct
        if stobj:
            if stobj["toc"]:
                with st.expander(f"📚 标题树（{len(stobj['toc'])} 项）"):
                    for level, title, page in stobj["toc"]:
                        st.markdown(f"{'　'*(level-1)}- {title} <span style='color:#999'>·p{page}</span>",
                                    unsafe_allow_html=True)
            if stobj["tables"]:
                st.markdown(f"##### 📊 表格（{len(stobj['tables'])}）")
                for t in stobj["tables"]:
                    st.caption(f"第 {t['page']} 页 · 表 {t['index']}")
                    st.markdown(html_table(t["df"]), unsafe_allow_html=True)
            if stobj["figs"]:
                st.markdown(f"##### 🖼️ 图表（{len(stobj['figs'])}）")
                for fig in stobj["figs"]:
                    st.image(fig["path"], caption=fig.get("desc", f"p{fig['page']}"), width="stretch")

        c1, c2 = st.columns(2)
        if c1.button("🧮 提取 LaTeX 公式") and ctx.llm.available:
            text = "\n".join(c["text"] for c in ctx.chunks if c["source"] == sf)[:6000]
            st.session_state.formula_view = ctx.llm.json_chat([{"role": "user",
                "content": f"提取数学公式，输出JSON数组，每条 {{name,latex(不含$)}}。\n{text}"}])
        if c2.button("📚 结构化参考文献") and ctx.llm.available:
            _p = resolve_pdf(sf)
            if not _p:
                st.info("该论文为内置演示语料（无原始 PDF）。")
            else:
                doc = parsing.open_doc(_p)
                raw = parsing.extract_references_raw(doc)[:8000]; doc.close()
                st.session_state.ref_view = ctx.llm.json_chat([{"role": "user",
                    "content": "提取参考文献，输出JSON数组，每条 {id,authors,title,year,venue}；无法识别填'未识别'。\n"+raw}])
        if st.session_state.formula_view:
            for f in st.session_state.formula_view:
                st.caption(f.get("name", "公式"))
                st.latex(f.get("latex", "").strip().strip("$"))
        if st.session_state.ref_view:
            import pandas as pd
            st.markdown(html_table(pd.DataFrame(st.session_state.ref_view)), unsafe_allow_html=True)

    st.markdown("---")
    # ---------------- 当前库 & 删除 ----------------
    st.subheader("📚 当前文献库")
    for fn in ctx.files:
        p = ctx.papers[fn]
        c1, c2 = st.columns([6, 1])
        c1.markdown(f"**{p.get('title', fn)}** `{p.get('version','')}` · {fn} · {p.get('year','')}")
        if c2.button("🗑️ 删除", key=f"rm_{fn}"):
            library.remove_paper(ctx, fn); st.rerun()

    st.markdown("---")
    st.subheader("🗝️ 版本管理与差异")
    groups = library.group_versions(ctx)
    shown = False
    for gid, files in groups.items():
        if len(files) > 1:
            shown = True
            latest = library.latest_of(files)[0]
            st.info(f"同一论文（{gid}）有 {len(files)} 版：{', '.join(files)}；**建议保留最新版 {latest}**。")
            if st.button(f"🔍 对比 {gid} 版本差异") and ctx.llm.available:
                st.markdown(library.version_diff(ctx, files))
    if not shown:
        st.caption("未检测到同一论文的多版本。")
