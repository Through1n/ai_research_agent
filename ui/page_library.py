"""页面 1：文献库管理。语义查重与版本决策、元数据/排版识别、细粒度结构（标题树/表格/图表/公式/参考文献/附录）、首跑自愈。"""
import os
import time
from html import escape as _h

import pandas as pd
import streamlit as st

from config import UPLOAD_DIR
from services import library, parsing
from ui.common import (ensure_state, page_header, pretty_table, resolve_pdf, friendly_error,
                       empty_state, llm_guard, long_block, step_text, LONG_TEXT_LIMIT)
from ui.sidebar import set_flash

# 细粒度结构一次最多展示的图表数量（避免超大 PDF 把页面拖垮）
MAX_FIG_SHOW = 8
# 失败页清单最多展示条数（避免超长列表把页面拖垮）
MAX_FAILED_SHOW = 50


def _init():
    ensure_state({
        "pending": None,
        "struct": None,
        "meta_view": None,
        "formula_view": None,
        "ref_view": None,
    })


def _section(title, desc=""):
    """分区标题：与 page_header 一致的蓝色左侧强调条风格，统一层级、留白舒适。

    借鉴 Zotero 三栏分区 + Paperpile 按逻辑分组的原则：每个功能区用一致的标题卡片，
    替代原先零散的 st.subheader，避免视觉噪音、提升扫描可读性。
    """
    block = (f'<div style="margin:20px 0 12px;padding:4px 0 4px 12px;'
             f'border-left:4px solid #3b6fb6;'
             f'background:linear-gradient(90deg,#eef4fc 0%,rgba(238,244,252,0) 100%);'
             f'border-radius:6px;">'
             f'<div style="font-size:16px;font-weight:700;color:#173a63;line-height:1.4;">{_h(title)}</div>')
    if desc:
        block += (f'<div style="margin-top:4px;font-size:12.5px;color:#5a6678;'
                  f'line-height:1.6;">{_h(desc)}</div>')
    block += '</div>'
    st.markdown(block, unsafe_allow_html=True)


def _struct_error_hint(what, e):
    """结构性解析失败统一话术。"""
    friendly_error(what, e,
                   reasons=["PDF 文件损坏或加密", "该论文是演示语料，无原始 PDF 文件"],
                   actions=["重新上传完整 PDF", "或改用「📁 文献库管理」中已有纸质元数据进行演示"])


def _show_prepare_warnings(prepared):
    """入库前如实展示解析告警：失败页清单、截断警示、扫描件 OCR 状态（R9 T4）。"""
    failed = prepared.get("failed_pages") or []
    if failed:
        pages = "、".join(f"p{fp.get('page')}" for fp in failed[:12])
        st.warning(f"⚠️ 有 {len(failed)} 页解析失败（其余页正常入库）：{pages}"
                   + ("…" if len(failed) > 12 else ""))
        with st.expander("🔧 失败页原因（供排查）", expanded=False):
            for fp in failed[:MAX_FAILED_SHOW]:
                st.caption(f"第 {fp.get('page')} 页：{str(fp.get('error', ''))[:160]}")
    if prepared.get("truncated"):
        st.warning(f"⚠️ PDF 疑似不完整：声称 {prepared.get('expected_pages', '?')} 页，"
                   f"成功提取 {prepared.get('extracted_pages', '?')} 页；已按可提取部分入库，"
                   f"建议核对原文件是否完整。")
    if prepared.get("scanned"):
        if prepared.get("ocr_error"):
            st.warning(f"🧾 检测为扫描件；OCR 存在失败：{str(prepared['ocr_error'])[:120]}")
        else:
            st.info("🧾 检测为扫描件，已通过多模态 OCR 提取文本。")


def _render_library_health(ctx):
    """首跑自愈（R9 T2）：库空或 chunk 覆盖异常时给醒目提示 + 一键重建入口。

    异常判定在 services/library.py:library_health（每篇 chunk 中位数 < 3，
    或超过一半 chunk 集中在第 1 页即视为退化——典型成因是演示文本语料
    覆盖了真实 PDF 入库结果，用户只能检索到首页片段）。
    """
    try:
        health = library.library_health(ctx)
    except Exception:
        return
    if not health.get("unhealthy"):
        return
    if health.get("empty"):
        st.warning("📭 文献库为空：问答/图谱/写作/对比页将无可检索内容。"
                   "可从上方上传 PDF，或用下方按钮一键重建官方测试文献库。")
    else:
        st.error("⚠️ 检测到文献库内容异常退化（不是空库，但几乎检索不到正文）："
                 + "；".join(health["reasons"]) + "。")
        st.caption("🩺 典型成因：「一键加载演示数据」的示例文本覆盖了真实 PDF 的入库结果，"
                   "每篇只剩首条片段；问答/图谱/对比页目前只能基于这些片段作答，质量很差。")
        if health.get("chunks_per_paper"):
            rows = [{"文献": fn, "chunk 数": n,
                     "状态": "⚠️ 异常" if n < 3 else "✅ 正常"}
                    for fn, n in health["chunks_per_paper"].items()]
            pretty_table(rows, title="🩺 当前库 chunk 覆盖明细", max_rows=MAX_FAILED_SHOW)
    st.caption("🛠️ 一键重建：从 pdf/ 官方测试件走完整入库路径（分 20 页批 + 单页 30s 超时 + "
               "扫描件 OCR），同名文献自动覆盖，不影响你的其它上传。")
    if st.button("🔧 一键重建文献库（pdf/ 官方测试件，含 541 页教材）", key="lib_rebuild"):
        _run_rebuild(ctx)


def _show_rebuild_report(report):
    """重建实测表 + 失败页清单 + 备份信息。"""
    rows = []
    for r in report.get("results", []):
        pages = r.get("pages") or []
        cover = f"{pages[0]}–{pages[-1]}（{len(pages)} 页）" if pages else "—"
        rows.append({
            "文献": r.get("filename", "?"),
            "PDF 页数": r.get("page_count", 0),
            "扫描件": "是（OCR）" if r.get("scanned") else "否",
            "chunk 数": r.get("chunks", 0),
            "覆盖页": cover,
            "失败页": len(r.get("failed_pages") or []),
            "耗时(s)": round(float(r.get("elapsed") or 0.0), 1),
            "结果": "✅ 已入库" if r.get("ok") else f"❌ {str(r.get('error', ''))[:60]}",
        })
    if rows:
        pretty_table(rows, title="🛠️ 重建实测结果", max_rows=MAX_FAILED_SHOW)
    all_failed = []
    for r in report.get("results", []):
        for fp in r.get("failed_pages") or []:
            all_failed.append({"文献": r.get("filename", "?"), "页": fp.get("page"),
                               "原因": str(fp.get("error", ""))[:80]})
    if all_failed:
        st.warning(f"⚠️ 共 {len(all_failed)} 页解析失败（不影响其它页入库）：")
        pretty_table(all_failed[:MAX_FAILED_SHOW], title="失败页清单", max_rows=MAX_FAILED_SHOW)
    else:
        st.success("✅ 全部页面解析成功，无失败页。")
    for s in report.get("skipped") or []:
        st.caption(f"⏭️ 跳过 {s.get('filename')}：{s.get('error')}")
    if report.get("backup"):
        st.caption(f"🗂️ 重建前已自动备份至 {report['backup']}"
                   f"（{', '.join(report.get('backup_files') or []) or '无'}）")
    for w in report.get("backup_warnings") or []:
        st.caption(f"⚠️ {w}")


def _run_rebuild(ctx):
    """一键重建：真实进度（累计页/总页、已用时间、失败页累计）+ 实测结果表（R9 T2）。"""
    bar = st.progress(0.0)
    note = st.empty()
    failed = []
    t0 = time.time()

    def cb(done, total, msg):
        try:
            bar.progress(min(max(done / max(total, 1), 0.0), 1.0))
            note.caption(f"🛠️ {msg} · 累计 {done}/{total} 页 · 已用 {time.time() - t0:.0f}s · "
                         f"失败页累计 {len(failed)}")
        except Exception:
            pass

    def on_page_error(page_index, reason):
        failed.append({"page": page_index + 1, "error": str(reason)[:200]})

    try:
        report = library.rebuild_from_pdf_dir(ctx, progress_cb=cb, page_error_cb=on_page_error)
    except Exception as e:
        try:
            bar.empty()
            note.empty()
        except Exception:
            pass
        friendly_error("重建文献库", e,
                       reasons=["pdf/ 目录缺少官方测试件", "磁盘空间不足 / 向量库写入失败",
                                "扫描件 OCR 需要可用的多模态模型 Key"],
                       actions=["确认 pdf/ 下 5 个官方 PDF 存在后重试",
                                "在左侧「接入方式」填入有效 Key 后重试（扫描件 OCR 需要）"])
        return
    try:
        bar.progress(1.0)
        note.caption(f"✅ 重建完成，总耗时 {float(report.get('elapsed') or 0):.0f}s")
    except Exception:
        pass
    _show_rebuild_report(report)
    if report.get("ok"):
        set_flash("success", f"✅ 文献库已重建：{len(report.get('results') or [])} 篇，"
                             f"共 {report.get('total_chunks', 0)} 个 chunk")
    else:
        set_flash("warning", "⚠️ 重建存在失败项，详见上方实测表（其余文献不受影响）")
    st.rerun()


def render(ctx):
    _init()
    page_header("📁 文献库管理",
                "基于内容 embedding 的语义去重（识别 arXiv v1/v7）、版本管理、细粒度多模态解析。",
                icon="📁")

    # ---------------- 首跑自愈：库空/退化检测 + 一键重建（R9 T2） ----------------
    _render_library_health(ctx)
    st.markdown("---")

    # ---------------- 上传 & 语义查重 ----------------
    _section("⬆️ 上传 PDF（自动语义查重 / 扫描件 OCR）",
             "拖入 PDF 后自动解析与语义查重；命中已有论文会提示版本决策。")
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
                                   reasons=["PDF 损坏 / 加密 / 非标准排版",
                                            "扫描件但未配置可用的多模态模型 Key"],
                                   actions=["换一个 PDF 重试", "或先加载演示数据继续演示"])
                else:
                    # 失败页 / 截断 / 扫描件 OCR 状态如实展示后再决定入库（R9 T4）
                    _show_prepare_warnings(prepared)
                    if prepared.get("dup_name"):
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
    _section("🪪 元数据与排版识别", "选择论文后抽取标题/作者/机构/摘要，并判断单/双栏排版。")
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
    _section("🔬 细粒度结构解析", "提取标题树 / 表格 / 插图，并可按需抽取公式与参考文献。")
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
                        st.image(path, caption=f"第 {fig.get('page','?')} 页插图")
                        desc = (fig.get("desc") or "").strip()
                        if desc and not desc.startswith("（当前"):
                            st.markdown(desc)
                        st.markdown("---")
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
    _section("📚 当前文献库", "库内全部论文一览；可删除单篇以释放空间。")
    if not ctx.files:
        empty_state("文献库为空。", hints=["点击左侧「🚀 一键加载演示数据（v1/v7/BERT）」",
                                       "或在上方上传 PDF"])
    else:
        rows = []
        for fn in ctx.files:
            p = ctx.papers.get(fn, {})
            n = len([c for c in ctx.chunks if c.get("source") == fn])
            rows.append({
                "篇名": p.get("title", fn),
                "版本": p.get("version", "") or "—",
                "年份": p.get("year", "") or "—",
                "片段数": n,
                "文件名": fn,
            })
        pretty_table(rows, title=f"✅ 当前共 {len(ctx.files)} 篇文献",
                     max_rows=MAX_FAILED_SHOW)
        with st.container(border=True):
            st.markdown("**🗑️ 删除文献**")
            delf = st.selectbox("选择要删除的文献", ctx.files, key="lib_rm_sel")
            if st.button("🗑️ 确认删除所选文献", key="lib_rm_btn"):
                try:
                    library.remove_paper(ctx, delf)
                    set_flash("success", f"✅ 已删除：{delf}")
                    st.rerun()
                except Exception as e:
                    friendly_error("删除文献", e, reasons=["文件被占用 / 向量库写入失败"],
                                   actions=["重试一次", "重启应用后重试"])

    st.markdown("---")
    # ---------------- 版本管理与差异 ----------------
    _section("🗝️ 版本管理与差异", "同一论文的多个版本会在此分组，便于对比差异与取舍。")
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
        with st.container(border=True):
            st.markdown(f"🗂️ **同一论文（{gid}）** · 库中有 {len(files)} 版")
            st.caption("版本清单：" + "、".join(files))
            try:
                latest = library.latest_of(files)[0]
                st.caption(f"💡 建议保留最新版 **{latest}**，其余版本可删除以避免检索重复。")
            except Exception:
                latest = ""
            if st.button(f"🔍 对比 {gid} 版本差异", key=f"lib_diff_{gid}"):
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
                            long_block(txt, title=f"版本差异报告（{gid}）", limit=LONG_TEXT_LIMIT)
                    except Exception as e:
                        friendly_error("版本差异对比", e,
                                       reasons=["大模型返回超时 / 额度不足", "版本元数据缺失"],
                                       actions=["重试一次", "或跳过该步继续演示"])


def _commit(ctx, prepared):
    """入库统一封装：异常友好化，成功统一成功提示（含失败页数，R9 T4）。"""
    try:
        p = library.commit(ctx, prepared)
        n = len([c for c in ctx.chunks if c.get("source") == p.get("filename")])
        fp = len(prepared.get("failed_pages") or [])
        tail = f"，{fp} 页解析失败" if fp else ""
        set_flash("success", f"✅ 已入库：{p.get('title','')}（{p.get('page_count','?')} 页，"
                             f"{n} 个分块{tail}）")
        st.rerun()
    except Exception as e:
        friendly_error("入库", e, reasons=["向量库写入失败 / 磁盘空间不足"],
                       actions=["重试一次", "或点击左侧「🚀 一键加载演示数据」继续演示"])
