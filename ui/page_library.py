"""页面 1：文献库管理。语义查重与版本决策、元数据/排版识别、细粒度结构（标题树/表格/图表/公式/参考文献/附录）。"""
import os

import pandas as pd
import streamlit as st

from config import UPLOAD_DIR
from services import library, parsing
from ui.common import (ensure_state, page_header, pretty_table, resolve_pdf, friendly_error,
                       empty_state, llm_guard, long_block, step_text, LONG_TEXT_LIMIT)
from ui.sidebar import set_flash

# 细粒度结构一次最多展示的图表数量（避免超大 PDF 把页面拖垮）
MAX_FIG_SHOW = 8


def _init():
    ensure_state({
        "pending": None,
        "struct": None,
        "meta_view": None,
        "formula_view": None,
        "ref_view": None,
    })


def _struct_error_hint(what, e):
    """结构性解析失败统一话术。"""
    friendly_error(what, e,
                   reasons=["PDF 文件损坏或加密", "该论文是演示语料，无原始 PDF 文件"],
                   actions=["重新上传完整 PDF", "或改用「📁 文献库管理」中已有纸质元数据进行演示"])


def render(ctx):
    _init()
    page_header("📁 文献库管理",
                "基于内容 embedding 的语义去重（识别 arXiv v1/v7）、版本管理、细粒度多模态解析。",
                icon="📁")

    # ---------------- 上传 & 语义查重 ----------------
    st.subheader("⬆️ 上传 PDF（自动语义查重 / 扫描件 OCR）")
    f = st.file_uploader("选择 PDF", type=["pdf"], key="lib_up")
    if f and st.button("🔍 解析并查重"):
        if not f.name:
            st.warning("⚠️ 未取得文件名，请重新选择文件。")
        else:
            data = f.read()
            bar = st.progress(0.0)
            note = st.empty()

            def cb(r, page):
                try:
                    bar.progress(min(max(float(r), 0.0), 1.0))
                    note.caption(step_text(f"📄 OCR/解析第 {page} 页"))
                except Exception:
                    pass

            try:
                prepared = library.prepare_pdf(ctx, data=data, filename=f.name, progress_cb=cb)
                try:
                    bar.empty()
                    note.empty()
                except Exception:
                    pass
                if not isinstance(prepared, dict):
                    st.warning("⚠️ 解析返回数据异常，未入库。请重试或换一个 PDF。")
                elif prepared.get("error"):
                    friendly_error("PDF 解析", prepared.get("error"),
                                   reasons=["PDF 损坏 / 加密 / 非标准排版"],
                                   actions=["换一个 PDF 重试", "或先加载演示数据继续演示"])
                elif prepared.get("scanned"):
                    st.info("🧾 检测为扫描件，已通过多模态 OCR 提取文本。")
                    if prepared.get("dup_name"):
                        st.session_state.pending = prepared
                    else:
                        _commit(ctx, prepared)
                elif prepared.get("dup_name"):
                    st.session_state.pending = prepared
                else:
                    _commit(ctx, prepared)
            except Exception as e:
                try:
                    bar.empty()
                    note.empty()
                except Exception:
                    pass
                friendly_error("PDF 解析", e,
                               reasons=["PDF 文件损坏 / 加密 / 超大导致超时",
                                        "向量库写入失败或磁盘空间不足"],
                               actions=["重试一次", "换一个体积更小的 PDF",
                                        "或点击左侧「🚀 一键加载演示数据」继续演示"])

    pend = st.session_state.get("pending")
    if pend and isinstance(pend, dict):
        st.warning(f"⚠️ 语义查重命中：库中已有《{pend.get('dup_name','未知')}》，"
                   f"内容相似度 {float(pend.get('dup_sim') or 0):.1%}。")
        if pend.get("dup_evidence"):
            st.caption(f"🔎 判定依据：{pend['dup_evidence']}（基于正文内容指纹，非文件名）")
        st.caption("💡 建议：若为同一论文的早期版本，优先「♻️ 覆盖旧版」以保持文献库时效性。")
        c1, c2, c3 = st.columns([1, 1, 1])
        if c1.button("♻️ 覆盖旧版（保留新版）"):
            try:
                p = library.commit(ctx, pend, replace=pend.get("dup_name"))
                # 写入 flash 而非 st.success：下方紧跟 st.rerun()，直接渲染会被吞掉
                set_flash("success", f"✅ 已覆盖：{p.get('filename', '')}")
                st.session_state.pending = None
                st.rerun()
            except Exception as e:
                friendly_error("覆盖旧版", e, reasons=["文献写入失败"], actions=["重试一次"])
        if c2.button("🗂️ 保留两个版本"):
            try:
                library.commit(ctx, pend)
                set_flash("success", "✅ 已同时保留两版，可在下方「🗝️ 版本管理与差异」查看版本建议。")
                st.session_state.pending = None
                st.rerun()
            except Exception as e:
                friendly_error("保留双版本", e, reasons=["文献写入失败"], actions=["重试一次"])
        if c3.button("❌ 取消本次上传"):
            st.session_state.pending = None
            st.rerun()

    st.markdown("---")
    # ---------------- 元数据 / 排版 ----------------
    st.subheader("🪪 元数据与排版识别")
    if not ctx.files:
        empty_state("文献库为空，暂无可识别元数据的论文。",
                    hints=["在上方上传 PDF", "或点击左侧「🚀 一键加载演示数据」"])
    else:
        mf = st.selectbox("选择论文", ctx.files, key="meta_sel")
        if st.button("🤖 抽取标题/作者/机构/摘要 + 判断排版"):
            if not llm_guard(ctx, "元数据智能抽取"):
                st.info("📭 演示语料的元数据已在入库时自动生成，见下方「📚 当前文献库」。")
            else:
                _p = resolve_pdf(mf)
                if not _p or not os.path.exists(str(_p)):
                    st.info("📭 该论文为内置演示语料（无原始 PDF），元数据已在入库时抽取完成，见下方文献库列表。")
                else:
                    try:
                        with st.spinner(step_text("🤖 正在抽取元数据")):
                            doc = parsing.open_doc(_p)
                            try:
                                first = doc[0].get_text()[:3000]
                                pw, ph = doc[0].rect.width, doc[0].rect.height
                                blocks = doc[0].get_text("blocks")
                                lb = sum(1 for b in blocks if b[0] < pw / 2)
                                rb = sum(1 for b in blocks if b[0] > pw / 2)
                                layout = "双栏" if lb and rb else "单栏"
                                prompt = (f"从首页抽取 标题/作者/机构/摘要，输出纯净 JSON。"
                                          f"页面 {pw:.0f}×{ph:.0f}，{layout}。\n文本：{first}")
                                st.session_state.meta_view = ctx.llm.json_chat(
                                    [{"role": "user", "content": prompt}])
                            finally:
                                try:
                                    doc.close()
                                except Exception:
                                    pass
                    except Exception as e:
                        _struct_error_hint("元数据抽取", e)
        if st.session_state.get("meta_view"):
            st.success("✅ 大模型抽取结果")
            with st.expander("🪪 元数据 JSON（点击展开 / 收起）", expanded=True):
                st.json(st.session_state.meta_view)

    st.markdown("---")
    # ---------------- 细粒度结构 ----------------
    st.subheader("🔬 细粒度结构解析")
    if not ctx.files:
        empty_state("文献库为空，暂无可解析的细粒度结构。",
                    hints=["在上方上传 PDF", "或点击左侧「🚀 一键加载演示数据」"])
    else:
        sf = st.selectbox("选择论文", ctx.files, key="struct_sel")
        with_vision = st.checkbox("🖼️ 用多模态模型逐图理解（较慢 / 消耗额度）")
        if st.button("🧩 全方位解析（标题树/表格/图表）"):
            _p = resolve_pdf(sf)
            if not _p or not os.path.exists(str(_p)):
                st.info("📭 该论文为内置演示语料（无原始 PDF），版式级解析不适用；问答与图谱功能不受影响。")
            else:
                try:
                    bar = st.progress(0.0)
                    note = st.empty()
                    try:
                        note.caption(step_text("🧩 打开 PDF"))
                        doc = parsing.open_doc(_p)
                        bar.progress(0.2)
                        note.caption(step_text("🧩 解析标题树", 1, 3))
                        toc = parsing.extract_toc(doc)
                        bar.progress(0.45)
                        note.caption(step_text("🧩 解析表格", 2, 3))
                        tables = parsing.extract_tables(doc)
                        bar.progress(0.7)
                        note.caption(step_text("🧩 提取插图", 3, 3))
                        fig_dir = os.path.join(str(UPLOAD_DIR), "figs", str(sf).replace(".pdf", ""))
                        figs = parsing.extract_figures(doc, fig_dir)
                        bar.progress(1.0)
                        if with_vision and figs:
                            if getattr(getattr(ctx, "llm", None), "available", False):
                                pb = st.progress(0.0)
                                parsing.describe_figures(figs, ctx.llm, lambda r: pb.progress(min(r, 1.0)))
                                try:
                                    pb.empty()
                                except Exception:
                                    pass
                            else:
                                st.warning("⚠️ 未检测到可用多模态模型，已跳过图片理解（不影响其余结构解析）。")
                        try:
                            doc.close()
                        except Exception:
                            pass
                        bar.empty()
                        note.empty()
                        st.session_state.struct = {"file": sf, "toc": toc or [],
                                                   "tables": tables or [], "figs": figs or []}
                        st.success(f"✅ 解析完成：标题树 {len(toc or [])} 项 · "
                                   f"表格 {len(tables or [])} 个 · 插图 {len(figs or [])} 张")
                    except Exception as e:
                        try:
                            doc.close()
                        except Exception:
                            pass
                        raise e
                except Exception as e:
                    try:
                        bar.empty()
                        note.empty()
                    except Exception:
                        pass
                    _struct_error_hint("细粒度结构解析", e)

        stobj = st.session_state.get("struct")
        if stobj and isinstance(stobj, dict):
            if stobj.get("file") != sf:
                st.caption(f"ℹ️ 下方结构来自上一次解析的《{stobj.get('file','—')}》，"
                           f"如需解析当前选择的《{sf}》请重新点击「🧩 全方位解析」。")
            if stobj.get("toc"):
                with st.expander(f"📚 标题树（{len(stobj['toc'])} 项）", expanded=False):
                    for item in stobj["toc"]:
                        try:
                            level, title, page = item
                        except (TypeError, ValueError):
                            continue
                        st.markdown(f"{'　'*(max(int(level), 1)-1)}- {title} "
                                    f"<span style='color:#999'>·p{page}</span>", unsafe_allow_html=True)
            else:
                st.info("📭 未识别到标题树（该 PDF 可能无书签式章节结构）。")

            if stobj.get("tables"):
                st.markdown(f"##### 📊 表格（{len(stobj['tables'])}）")
                for t in stobj["tables"]:
                    st.caption(f"第 {t.get('page','?')} 页 · 表 {t.get('index','?')}")
                    try:
                        pretty_table(t.get("df"))
                    except Exception as e:
                        friendly_error("表格渲染", e, reasons=["表格数据格式异常"],
                                       actions=["换一个 PDF 重试"])
            else:
                st.info("📭 未识别到表格。")

            if stobj.get("figs"):
                figs = stobj["figs"]
                st.markdown(f"##### 🖼️ 图表（{len(figs)}）")
                for fig in figs[:MAX_FIG_SHOW]:
                    path = fig.get("path", "")
                    if not path or not os.path.exists(str(path)):
                        st.caption(f"⚠️ 插图文件缺失：{path or '(空路径)'}")
                        continue
                    try:
                        st.image(path, caption=fig.get("desc", f"p{fig.get('page','?')}"))
                    except Exception as e:
                        st.caption(f"⚠️ 插图渲染失败（p{fig.get('page','?')}）：{str(e)[:100]}")
                if len(figs) > MAX_FIG_SHOW:
                    st.caption(f"ℹ️ 共 {len(figs)} 张插图，已展示前 {MAX_FIG_SHOW} 张，可在 storage/uploads/figs 查看全部。")
            else:
                st.info("📭 未提取到插图。")

        c1, c2 = st.columns([1, 1])
        if c1.button("🧮 提取 LaTeX 公式"):
            if llm_guard(ctx, "LaTeX 公式抽取"):
                try:
                    text = "\n".join(c["text"] for c in ctx.chunks if c.get("source") == sf)[:6000]
                    if not text.strip():
                        empty_state("该论文暂无可分析的正文文本。", hints=["换一篇论文试试"])
                    else:
                        with st.spinner(step_text("🧮 正在抽取公式")):
                            st.session_state.formula_view = ctx.llm.json_chat([{"role": "user",
                                "content": f"提取数学公式，输出JSON数组，每条 {{name,latex(不含$)}}。\n{text}"}])
                except Exception as e:
                    friendly_error("公式抽取", e, reasons=["大模型返回非 JSON / 超时"],
                                   actions=["重试一次", "或换个问题继续演示"])
        if c2.button("📚 结构化参考文献"):
            if llm_guard(ctx, "参考文献结构化"):
                _p = resolve_pdf(sf)
                if not _p or not os.path.exists(str(_p)):
                    st.info("📭 该论文为内置演示语料（无原始 PDF），无版式级参考文献区。")
                else:
                    try:
                        with st.spinner(step_text("📚 正在结构化参考文献")):
                            doc = parsing.open_doc(_p)
                            try:
                                raw = parsing.extract_references_raw(doc)[:8000]
                            finally:
                                try:
                                    doc.close()
                                except Exception:
                                    pass
                            st.session_state.ref_view = ctx.llm.json_chat([{"role": "user",
                                "content": "提取参考文献，输出JSON数组，每条 {id,authors,title,year,venue}；无法识别填'未识别'。\n"+raw}])
                    except Exception as e:
                        _struct_error_hint("参考文献结构化", e)
        if st.session_state.get("formula_view"):
            with st.expander(f"🧮 公式（{len(st.session_state.formula_view)} 条）", expanded=False):
                for fml in st.session_state.formula_view:
                    if not isinstance(fml, dict):
                        continue
                    st.caption(fml.get("name", "公式"))
                    try:
                        st.latex(str(fml.get("latex", "")).strip().strip("$"))
                    except Exception:
                        st.code(str(fml.get("latex", "")))
        if st.session_state.get("ref_view"):
            st.success(f"✅ 参考文献结构化完成（{len(st.session_state.ref_view)} 条）")
            try:
                pretty_table(pd.DataFrame(st.session_state.ref_view), title="📚 参考文献")
            except Exception as e:
                friendly_error("参考文献表格渲染", e, reasons=["返回结构不规则"], actions=["重试一次"])

    st.markdown("---")
    # ---------------- 当前库 & 删除 ----------------
    st.subheader("📚 当前文献库")
    if not ctx.files:
        empty_state("文献库为空。", hints=["点击左侧「🚀 一键加载演示数据（v1/v7/BERT）」",
                                       "或在上方上传 PDF"])
    else:
        st.success(f"✅ 当前共 {len(ctx.files)} 篇文献")
        for fn in ctx.files:
            p = ctx.papers.get(fn, {})
            c1, c2 = st.columns([2, 1])
            c1.markdown(f"**{p.get('title', fn)}** `{p.get('version','')}` · {fn} · {p.get('year','')}")
            if c2.button("🗑️ 删除", key=f"rm_{fn}"):
                try:
                    library.remove_paper(ctx, fn)
                    set_flash("success", f"✅ 已删除：{fn}")
                    st.rerun()
                except Exception as e:
                    friendly_error("删除文献", e, reasons=["文件被占用 / 向量库写入失败"],
                                   actions=["重试一次", "重启应用后重试"])

    st.markdown("---")
    # ---------------- 版本管理与差异 ----------------
    st.subheader("🗝️ 版本管理与差异")
    try:
        groups = library.group_versions(ctx) or {}
    except Exception as e:
        friendly_error("版本分组", e, reasons=["文献元数据缺失"], actions=["重新加载演示数据"])
        groups = {}
    multi = {gid: files for gid, files in groups.items() if len(files) > 1}
    if not multi:
        empty_state("未检测到同一论文的多版本。",
                    hints=["演示时先加载演示数据（内含 1706.03762 的 v1 / v7 两版）",
                           "或手动上传同一论文的 v1 / v7"])
    for gid, files in multi.items():
        st.info(f"🗂️ 同一论文（{gid}）在库中有 {len(files)} 版：{', '.join(files)}")
        try:
            latest = library.latest_of(files)[0]
            st.caption(f"💡 建议保留最新版 **{latest}**，其余版本可删除以避免检索重复。")
        except Exception:
            latest = ""
        if st.button(f"🔍 对比 {gid} 版本差异"):
            if llm_guard(ctx, "版本差异对比"):
                try:
                    with st.spinner(step_text("🔍 正在对比版本差异")):
                        diff_md = library.version_diff(ctx, files)
                    if not diff_md or not str(diff_md).strip():
                        st.info("📭 未生成差异报告（可能两版正文完全一致）。")
                    else:
                        txt = str(diff_md)
                        # 大模型不可用时 version_diff 会返回降级文案而非抛异常，
                        # 这里显式降级提示 + 引导，避免评委看到一串技术错误摘要却无任何说明。
                        if "暂不可用" in txt or txt.startswith("【"):
                            st.warning("⚠️ 版本差异报告已降级：大模型暂不可用，未生成对比表格。")
                            st.caption("🩺 可能原因：API Key 无效 / 额度不足 / 现场网络受限。")
                            st.caption("🛠️ 怎么办：填入有效 Key 后重试；"
                                       "无 Key 时可直接讲解下方「💡 建议保留最新版」与版本清单，演示不受阻断。")
                        long_block(txt, title="版本差异报告", limit=LONG_TEXT_LIMIT)
                except Exception as e:
                    friendly_error("版本差异对比", e,
                                   reasons=["大模型返回超时 / 额度不足", "版本元数据缺失"],
                                   actions=["重试一次", "或跳过该步继续演示"])


def _commit(ctx, prepared):
    """入库统一封装：异常友好化，成功统一成功提示。"""
    try:
        p = library.commit(ctx, prepared)
        n = len([c for c in ctx.chunks if c.get("source") == p.get("filename")])
        set_flash("success", f"✅ 已入库：{p.get('title','')}（{p.get('page_count','?')} 页，{n} 个分块）")
        st.rerun()
    except Exception as e:
        friendly_error("入库", e, reasons=["向量库写入失败 / 磁盘空间不足"],
                       actions=["重试一次", "或点击左侧「🚀 一键加载演示数据」继续演示"])
