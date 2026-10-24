"""页面 1：文献库管理。语义查重与版本决策、元数据/排版识别、细粒度结构（标题树/表格/图表/公式/参考文献/附录）、首跑自愈。"""
import os
import time

import pandas as pd
import streamlit as st

from config import UPLOAD_DIR, MAX_UPLOAD_MB
from services import library, parsing
from ui.common import (ensure_state, page_header, pretty_table, resolve_pdf, friendly_error,
                       empty_state, llm_guard, long_block, step_text, metric_strip,
                       bg_submit, bg_consume, bg_running, LONG_TEXT_LIMIT, section_header)
from ui.sidebar import set_flash

# 后台任务 kind：本页各长任务提交/认领的唯一标识（bg_consume 按它路由）。
# R19 只后台化了「解析并查重」；元数据抽取 / 全方位解析 / 公式 / 参考文献四个按钮
# 仍是内联 spinner——用户实测「运行其中一个，同板块其他项目全部无法操作」
# （内联 LLM 调用占住主线程，整个页面冻结）。2026-10-22 按同一模式补齐全。
# ⚠️ 半迁移态比不迁移更危险：只接 submit 漏接 consume = 结果永久丢失且零测试覆盖
# （writing 页踩过），四个 kind 的 submit/consume 必须成对接线。
_LIB_PARSE_KIND = "library_parse"
_LIB_META_KIND = "library_meta"
_LIB_STRUCT_KIND = "library_struct"
_LIB_FORMULA_KIND = "library_formula"
_LIB_REF_KIND = "library_ref"

# 文件级拒收（prepare_pdf 返回带 error 的 dict）的分档话术，键 = prepared["reason"]。
# 取值来源 services/library.py：_validate_prepared(:501-530) 写 empty / scanned_no_ocr /
# corrupt / truncated；_empty_prepared 调用处(:584) 另写 encrypted。
_PDF_ERR_TIPS = {
    "empty": (["文件是 0 字节，或没有任何可读页面", "下载中断 / 网盘同步不全"],
              ["换一个完整的 PDF 重试", "或点击左侧「🚀 一键加载演示数据」继续演示"]),
    "truncated": (["文件不完整：下载中断 / 网盘同步不全 / 磁盘坏块导致对象流丢失"],
                  ["重新下载完整版后重试", "或点击左侧「🚀 一键加载演示数据」继续演示"]),
    "corrupt": (["PDF 结构损坏 / 非标准排版", "扫描件但未配置可用的多模态模型 Key"],
                ["换一个 PDF 重试", "或先加载演示数据继续演示"]),
    "scanned_no_ocr": (["是扫描件，但当前未启用 / 不可用的视觉模型 Key，全部页面都提取不到文本"],
                       ["在左侧「接入方式」填入支持视觉的 Key 后重试",
                        "或先加载演示数据继续演示"]),
    "encrypted": (["PDF 被加密，需要密码才能打开"],
                  ["换一个未加密的 PDF 重试", "或先加载演示数据继续演示"]),
    "_": (["PDF 损坏 / 加密 / 非标准排版"], ["换一个 PDF 重试", "或先加载演示数据继续演示"]),
}

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
        "_parse_warnings": None,
    })


def _section(title, desc=""):
    """分区标题：收编到 common.section_header（3px 强调色竖条 + 全令牌化样式）。

    原实现自带 #3b6fb6 第二强调蓝、蓝色渐变底、6px 圆角与 20px/10px 脱轨间距，
    2026-10-24 西式简洁重构删除，改为全站统一分区标题（.ai-section* 构件类），
    与 page_header / page_graph 分区头完全同一形态。
    """
    section_header(title, desc)


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


def _parse_worker(ctx, data, filename, progress):
    """后台线程执行 PDF 解析（2026-10 R19 后台化改造）。

    硬约束：本函数运行在 services/tasks.py 的 worker 线程里，**禁止任何 st.\*
    调用**（会报 missing ScriptRunContext）。进度只能经 progress 回调上报，
    由侧边栏任务中心渲染。
    """
    def pcb(done, total, msg=None):
        try:
            pct = (float(done) / float(total)) if total else None
        except Exception:
            pct = None
        progress(msg or ("已处理 %s/%s 页" % (done, total)), pct)

    return library.prepare_pdf(ctx, data=data, filename=filename, progress_cb=pcb)


def _meta_worker(ctx, pdf_path, progress):
    """后台线程执行元数据抽取（2026-10-22 后台化补全，消除板块冻结）。

    与旧内联流程逐步骤等价：open_doc → 首页文本/尺寸/分栏判定 → LLM JSON。
    硬约束：worker 线程内禁止任何 st.* 调用。
    """
    progress("打开 PDF 并读取首页", 0.15)
    doc = parsing.open_doc(str(pdf_path))
    try:
        first = doc[0].get_text()[:3000]
        pw, ph = doc[0].rect.width, doc[0].rect.height
        blocks = doc[0].get_text("blocks")
        lb = sum(1 for b in blocks if b[0] < pw / 2)
        rb = sum(1 for b in blocks if b[0] > pw / 2)
        layout = "双栏" if lb and rb else "单栏"
        prompt = (f"从首页抽取 标题/作者/机构/摘要，输出纯净 JSON。"
                  f"页面 {pw:.0f}×{ph:.0f}，{layout}。\n文本：{first}")
    finally:
        try:
            doc.close()
        except Exception:
            pass
    progress("调用大模型抽取元数据", 0.6)
    result = ctx.llm.json_chat([{"role": "user", "content": prompt}])
    progress("抽取完成", 1.0)
    return result


def _struct_worker(ctx, pdf_path, filename, with_vision, progress):
    """后台线程执行细粒度结构解析（标题树/表格/插图 + 可选多模态读图）。

    与旧内联流程逐步骤等价；vision 未可用的告警不抛异常，随结果带回交付。
    硬约束：worker 线程内禁止任何 st.* 调用。
    """
    progress("打开 PDF", 0.1)
    doc = parsing.open_doc(str(pdf_path))
    try:
        progress("解析标题树", 0.3)
        toc = parsing.extract_toc(doc)
        progress("解析表格", 0.5)
        tables = parsing.extract_tables(doc)
        progress("提取插图", 0.75)
        fig_dir = os.path.join(str(UPLOAD_DIR), "figs", str(filename).replace(".pdf", ""))
        figs = parsing.extract_figures(doc, fig_dir)
        vision_note = ""
        if with_vision and figs:
            if getattr(getattr(ctx, "llm", None), "available", False):
                def _vcb(r):
                    try:
                        ratio = min(max(float(r), 0.0), 1.0)
                        progress("多模态逐图理解 %.0f%%" % (ratio * 100), 0.75 + 0.25 * ratio)
                    except Exception:
                        pass
                parsing.describe_figures(figs, ctx.llm, _vcb)
            else:
                vision_note = "⚠️ 未检测到可用多模态模型，已跳过图片理解（不影响其余结构解析）。"
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return {"file": filename, "toc": toc or [], "tables": tables or [],
            "figs": figs or [], "vision_note": vision_note}


# LaTeX 里的 `\f` `\b` `\r` `\n` `\t` 在 JSON 字符串里是**合法转义**，模型直接写
# 单反斜杠时会被 json.loads 悄悄转成控制字符（`\frac{a}{b}` → 换页符 + "rac{a}{b}"），
# 解析不报错，但公式渲染出来是乱码。改在公式字段上把这些控制字符还原成反斜杠+字母。
_LATEX_CTRL_MAP = {"\x08": "\\b", "\x0c": "\\f", "\r": "\\r", "\n": "\\n", "\t": "\\t"}


def _fix_latex_controls(items):
    """还原公式字段里被 JSON 转义吃掉的反斜杠（`\\frac` 被读成换页符的经典坑）。"""
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return items
    for it in items:
        if not isinstance(it, dict):
            continue
        for k in ("latex", "formula"):
            v = it.get(k)
            if isinstance(v, str) and any(c in v for c in _LATEX_CTRL_MAP):
                for ctrl, rp in _LATEX_CTRL_MAP.items():
                    v = v.replace(ctrl, rp)
                it[k] = v
    return items


def _formula_worker(ctx, filename, progress):
    """后台线程执行 LaTeX 公式抽取（正文文本来自已入库 chunks）。"""
    progress("收集正文片段", 0.2)
    # 2026-10-24 实测：喂 6000 字符 + 不限条数时，生成经常顶到 LLM_TIMEOUT=45s
    # 才结束（实测同 prompt 单次 37s，波动即超时 → 后台任务失败）。收敛输入与
    # 输出规模，把生成时长压进演示保险窗口；-- 提取「最具代表性的公式」，不是全集。
    text = "\n".join(c["text"] for c in ctx.chunks if c.get("source") == filename)[:3000]
    if not text.strip():
        return {"items": None, "note": "该论文暂无可分析的正文文本，换一篇论文试试。"}
    progress("调用大模型抽取公式", 0.7)
    # 提示词 2026-10-24 收紧：旧版只说「提取数学公式」，实测 glm-4-flash 会把
    # 「attention mechanism」「BLEU」这类名词当公式返回（st.latex 渲染成一串斜体单词）。
    # 现在要求必须含运算符/上下标，且条数收敛到 6，兼顾质量与 LLM_TIMEOUT=45s 窗口。
    items = ctx.llm.json_chat([{"role": "user",
                                "content": ("只提取原文中真实出现的数学表达式（必须含 = ^ _ / sqrt "
                                            "softmax max min sum prod 等运算符或上下标），"
                                            "不要提取普通名词短语；最多 6 条；"
                                            "输出 JSON 数组，每条 {name,latex(不含$)}；"
                                            "latex 里反斜杠按 JSON 转义写成两个；"
                                            "只输出 JSON，不要代码围栏与解释。\n" + text)}])
    return {"items": _fix_latex_controls(items), "note": ""}


def _ref_worker(ctx, pdf_path, progress):
    """后台线程执行参考文献结构化（读 PDF 尾部参考文献区 → LLM JSON）。"""
    progress("打开 PDF 并读取参考文献区", 0.3)
    doc = parsing.open_doc(str(pdf_path))
    try:
        raw = parsing.extract_references_raw(doc)[:8000]
    finally:
        try:
            doc.close()
        except Exception:
            pass
    progress("调用大模型结构化", 0.8)
    # 同 _formula_worker：收敛输入与条数，避免生成顶到 LLM_TIMEOUT 造成后台任务失败
    return ctx.llm.json_chat([{"role": "user",
                               "content": ("提取参考文献，最多 12 条，输出 JSON 数组，"
                                           "每条 {id,authors,title,year,venue}；"
                                           "无法识别填'未识别'；"
                                           "只输出 JSON，不要代码围栏与解释。\n" + raw[:5000])}])


def _prepare_warning_text(prepared):
    """把解析告警压成一段文案（后台交付没有内联展开区，改用 session_state 单通道展示）。"""
    parts = []
    failed = prepared.get("failed_pages") or []
    if failed:
        pages = "、".join(f"p{fp.get('page')}" for fp in failed[:8])
        parts.append(f"⚠️ 有 {len(failed)} 页解析失败（其余页正常入库）：{pages}"
                     + ("…" if len(failed) > 8 else ""))
    if prepared.get("truncated"):
        parts.append(f"⚠️ PDF 疑似不完整：声称 {prepared.get('expected_pages', '?')} 页，"
                     f"成功提取 {prepared.get('extracted_pages', '?')} 页；已按可提取部分入库，"
                     f"建议核对原文件是否完整。")
    if prepared.get("scanned"):
        if prepared.get("ocr_error"):
            parts.append(f"🧾 检测为扫描件；OCR 存在失败：{str(prepared['ocr_error'])[:120]}")
        else:
            parts.append("🧾 检测为扫描件，已通过多模态 OCR 提取文本。")
    return "\n\n".join(parts)


def _consume_parse(ctx, kind, t):
    """后台解析完成交付：与旧内联流程逐分支等价（错误分档 / 版本决策 / 直接入库）。

    注意 flash 是单槽位：告警走 _parse_warnings 独立通道，避免被 _commit 的
    成功提示覆盖（2026-10 R19：后台化时发现的槽位冲突）。
    """
    if kind != _LIB_PARSE_KIND:
        return
    name = str(t.get("name") or "").replace("解析并查重：", "") or "PDF"
    if t["state"] == "error":
        set_flash("error", f"⚠️ 《{name}》解析失败：{str(t.get('error') or '')[:150]}"
                           "\n\n🩺 可能原因：PDF 文件损坏 / 加密 / 超大导致超时；"
                           "向量库写入失败或磁盘空间不足"
                           "\n\n🛠️ 怎么办：重试一次；换一个体积更小的 PDF")
        return
    prepared = t.get("result")
    if not isinstance(prepared, dict):
        set_flash("error", f"⚠️ 《{name}》解析返回数据异常，未入库。请重试或换一个 PDF。")
        return
    if prepared.get("error"):
        # 文件级拒收**不是异常**：按 reason 分档给原因/动作（与旧内联分支同一套话术）
        _rs, _as_ = _PDF_ERR_TIPS.get(str(prepared.get("reason") or ""), _PDF_ERR_TIPS["_"])
        set_flash("error", "⚠️ " + str(prepared.get("error"))
                  + "\n\n🩺 可能原因：" + "；".join(_rs)
                  + "\n\n🛠️ 怎么办：" + "；".join(_as_))
        return
    warn = _prepare_warning_text(prepared)
    if warn:
        st.session_state["_parse_warnings"] = warn
    if prepared.get("dup_name"):
        st.session_state.pending = prepared
        msg = (f"⚠️ 语义查重命中：库中已有《{prepared.get('dup_name')}》，"
               f"内容相似度 {float(prepared.get('dup_sim') or 0):.1%}。请在下方选择版本决策。")
        set_flash("warning", msg)
        return
    _commit(ctx, prepared)      # 内部 set_flash(success) + st.rerun()


def _consume_meta(ctx, kind, t):
    """元数据抽取完成交付：写 meta_view + flash 提示。"""
    if kind != _LIB_META_KIND:
        return
    name = str(t.get("name") or "").replace("元数据抽取：", "") or "论文"
    if t["state"] == "error":
        set_flash("error", f"⚠️ 《{name}》元数据抽取失败：{str(t.get('error') or '')[:150]}"
                           "\n\n🩺 可能原因：大模型返回非 JSON / 超时 / 网络受限；PDF 损坏或加密"
                           "\n\n🛠️ 怎么办：重试一次；或到「文献库」页签查看入库时自动生成的元数据")
        return
    result = t.get("result")
    if not result:
        set_flash("warning", f"⚠️ 《{name}》元数据抽取未返回内容，请重试。")
        return
    st.session_state.meta_view = result
    set_flash("success", f"✅ 《{name}》元数据抽取完成，见下方「🪪 元数据 JSON」。")


def _consume_struct(ctx, kind, t):
    """细粒度结构解析完成交付：写 struct + flash（空结果不刷 flash，由渲染区说明）。"""
    if kind != _LIB_STRUCT_KIND:
        return
    name = str(t.get("name") or "").replace("结构解析：", "") or "论文"
    if t["state"] == "error":
        set_flash("error", f"⚠️ 《{name}》细粒度结构解析失败：{str(t.get('error') or '')[:150]}"
                           "\n\n🩺 可能原因：PDF 损坏或加密 / 解析超时 / 磁盘写入失败"
                           "\n\n🛠️ 怎么办：重试一次；换一个 PDF")
        return
    result = t.get("result")
    if not isinstance(result, dict):
        set_flash("error", f"⚠️ 《{name}》结构解析返回数据异常，请重试。")
        return
    st.session_state.struct = {k: result.get(k) for k in ("file", "toc", "tables", "figs")}
    note = str(result.get("vision_note") or "")
    if any(result.get(k) for k in ("toc", "tables", "figs")):
        msg = (f"✅ 《{result.get('file') or name}》解析完成："
               f"标题树 {len(result.get('toc') or [])} 项 · "
               f"表格 {len(result.get('tables') or [])} 个 · "
               f"插图 {len(result.get('figs') or [])} 张")
        if note:
            msg += "\n\n" + note
        set_flash("success", msg)


def _consume_formula(ctx, kind, t):
    """LaTeX 公式抽取完成交付：写 formula_view + flash。"""
    if kind != _LIB_FORMULA_KIND:
        return
    name = str(t.get("name") or "").replace("公式抽取：", "") or "论文"
    if t["state"] == "error":
        set_flash("error", f"⚠️ 《{name}》公式抽取失败：{str(t.get('error') or '')[:150]}"
                           "\n\n🩺 可能原因：大模型返回非 JSON / 超时 / 网络受限"
                           "\n\n🛠️ 怎么办：重试一次；或换一篇论文")
        return
    result = t.get("result") if isinstance(t.get("result"), dict) else {}
    items = result.get("items")
    note = str(result.get("note") or "")
    if items is None:
        set_flash("info", f"📭 《{name}》：{note or '该论文暂无可分析的正文文本。'}")
        return
    st.session_state.formula_view = items
    n = len(items) if isinstance(items, (list, tuple)) else "若干"
    set_flash("success", f"✅ 《{name}》公式抽取完成，共 {n} 条，见下方「🧮 公式」。")


def _consume_ref(ctx, kind, t):
    """参考文献结构化完成交付：写 ref_view + flash。"""
    if kind != _LIB_REF_KIND:
        return
    name = str(t.get("name") or "").replace("参考文献结构化：", "") or "论文"
    if t["state"] == "error":
        set_flash("error", f"⚠️ 《{name}》参考文献结构化失败：{str(t.get('error') or '')[:150]}"
                           "\n\n🩺 可能原因：PDF 损坏或加密 / 大模型返回非 JSON / 超时"
                           "\n\n🛠️ 怎么办：重试一次；或换一个 PDF")
        return
    items = t.get("result")
    if not items:
        set_flash("warning", f"⚠️ 《{name}》参考文献结构化未返回内容，请重试。")
        return
    st.session_state.ref_view = items
    set_flash("success", f"✅ 《{name}》参考文献结构化完成，见下方「📚 参考文献」。")


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
                   "可上传 PDF，或用下方按钮一键重建官方测试文献库。")
    else:
        st.error("⚠️ 检测到文献库内容异常退化（不是空库，但几乎检索不到正文）："
                 + "；".join(health["reasons"]) + "。")
        st.caption("典型成因：「一键加载演示数据」的示例文本覆盖了真实 PDF 的入库结果，"
                   "每篇只剩首条片段，问答/图谱/对比页只能基于这些片段作答。")
        if health.get("chunks_per_paper"):
            rows = [{"文献": fn, "chunk 数": n,
                     "状态": "⚠️ 异常" if n < 3 else "✅ 正常"}
                    for fn, n in health["chunks_per_paper"].items()]
            pretty_table(rows, title="🩺 当前库 chunk 覆盖明细", max_rows=MAX_FAILED_SHOW)
    st.caption("从 pdf/ 官方测试件完整重建（含扫描件 OCR），同名自动覆盖，不影响其它上传。")
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
    # 后台解析交付：任务完成（含失败）时按 kind 认领，等价旧内联流程的各分支。
    # 必须放在页面顶部——flash 由 app.py 在页面渲染前重放，交付里写入的提示
    # 依赖 bg_consume 内部的重绘才能在当轮可见。
    bg_consume({_LIB_PARSE_KIND: lambda k, t: _consume_parse(ctx, k, t),
                _LIB_META_KIND: lambda k, t: _consume_meta(ctx, k, t),
                _LIB_STRUCT_KIND: lambda k, t: _consume_struct(ctx, k, t),
                _LIB_FORMULA_KIND: lambda k, t: _consume_formula(ctx, k, t),
                _LIB_REF_KIND: lambda k, t: _consume_ref(ctx, k, t)})
    page_header("📁 文献库管理",
                "基于内容 embedding 的语义去重（识别 arXiv v1/v7）、版本管理、细粒度多模态解析。",
                icon="📁")

    # embedding 降级原因透出：services/library.py 的 _paper_embedding /
    # _paper_fingerprints 失败时会把人话原因写到 ctx.embed_degraded_reason。
    # 语义查重/版本匹配是增强能力，失败不拦入库，但必须让用户看见「哪一步降级了、
    # 后果是什么」。用 st.caption 而非 st.error：论文已正常入库，这不是致命错误，
    # 不能把页面刷红（渲染风格与 ui/common.py 的说明性 caption 一致）。
    _embed_reason = getattr(ctx, "embed_degraded_reason", None)
    if _embed_reason:
        st.caption(f"⚠️ 语义检索模型未就绪：{str(_embed_reason)[:200]}。"
                   "论文已正常入库，但「跨版本内容匹配 / 智能去重」暂不可用；"
                   "可稍后重试或先用关键词检索。")

    # key=：解析/入库提交后重跑时不要把页签踢回「上传与解析」（同 page_search 的理由）
    tab_up, tab_lib, tab_ver = st.tabs(["上传与解析", "文献库", "版本差异"],
                                       key="lib_tab")

    with tab_up:
        # ---------------- 首跑自愈：库空/退化检测 + 一键重建（R9 T2） ----------------
        _render_library_health(ctx)

        # ---------------- 上传 & 语义查重 ----------------
        _section("⬆️ 上传 PDF", "自动解析并语义查重；命中已有论文会提示版本决策，扫描件走 OCR。")
        # 上一轮后台解析的告警（失败页/截断/OCR 状态）在这里展示后即清——
        # 独立于 flash 单槽位，避免被入库成功提示覆盖（R9 T4 的如实展示不能丢）。
        _w = st.session_state.get("_parse_warnings")
        if _w:
            st.session_state["_parse_warnings"] = None
            st.warning(str(_w))
        f = st.file_uploader("选择 PDF", type=["pdf"], key="lib_up")
        _parsing = bg_running(kinds={_LIB_PARSE_KIND})
        if f and st.button("🔍 解析并查重", disabled=_parsing,
                           help="后台解析：提交后可切换其他页面，实时进度见左侧任务中心"):
            if not f.name:
                st.warning("⚠️ 未取得文件名，请重新选择文件。")
            else:
                data = f.read()
                # 应用层体积闸门（Round 12 场景 9）：超限在**提交之前**友好拒绝，
                # 避免超大 PDF 进了后台队列才报错、用户还不知道发生了什么。
                try:
                    _mb = len(data) / 1048576.0
                except Exception:
                    _mb = 0.0
                if _mb > MAX_UPLOAD_MB:
                    st.warning(f"⚠️ 这个文件有 {_mb:.0f} MB，超过 {MAX_UPLOAD_MB} MB 上限，"
                               f"已停止解析（现场解析会很慢且占用大量内存）。")
                    st.caption("🛠️ 怎么办：换一个更小的 PDF（建议 "
                               f"≤ {MAX_UPLOAD_MB} MB）；或先点击左侧"
                               "「🚀 一键加载演示数据」继续演示，其余功能不受影响。")
                else:
                    bg_submit("解析并查重：" + f.name,
                              lambda progress, _d=data, _n=f.name:
                                  _parse_worker(ctx, _d, _n, progress),
                              kind=_LIB_PARSE_KIND)
                    try:
                        st.toast("📄 已转入后台解析，进度见左侧任务中心，可随时切换其他页面",
                                 icon="🚀")
                    except Exception:
                        pass
        if _parsing:
            st.caption("⏳ 解析任务正在后台运行（实时进度见左侧「任务中心」）——"
                       "可切换其他页面，完成后结果自动出现在本页。")

        pend = st.session_state.get("pending")
        if pend and isinstance(pend, dict):
            st.warning(f"⚠️ 语义查重命中：库中已有《{pend.get('dup_name','未知')}》，"
                       f"内容相似度 {float(pend.get('dup_sim') or 0):.1%}。")
            if pend.get("dup_evidence"):
                st.caption(f"判定依据：{pend['dup_evidence']}（基于正文内容指纹，非文件名）")
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
                    set_flash("success", "✅ 已同时保留两版，可在「版本差异」页签查看版本建议。")
                    st.session_state.pending = None
                    st.rerun()
                except Exception as e:
                    friendly_error("保留双版本", e, reasons=["文献写入失败"], actions=["重试一次"])
            if c3.button("❌ 取消本次上传"):
                st.session_state.pending = None
                st.rerun()

        # ---------------- 元数据 / 排版 ----------------
        _section("🪪 元数据与排版识别")
        if not ctx.files:
            empty_state("文献库为空，暂无可识别元数据的论文。",
                        hints=["在上方上传 PDF", "或点击左侧「🚀 一键加载演示数据」"])
        else:
            mf = st.selectbox("选择论文", ctx.files, key="meta_sel")
            _meta_bg = bg_running(kinds={_LIB_META_KIND})
            if st.button("🤖 抽取标题/作者/机构/摘要 + 判断排版",
                         disabled=_meta_bg,
                         help="后台抽取：提交后可切换其他页面，实时进度见左侧「任务中心」"):
                if not llm_guard(ctx, "元数据智能抽取"):
                    st.info("📭 演示语料的元数据已在入库时自动生成，见「文献库」页签。")
                else:
                    _p = resolve_pdf(mf)
                    if not _p or not os.path.exists(str(_p)):
                        st.info("📭 该论文为内置演示语料（无原始 PDF），元数据已在入库时抽取完成，见「文献库」页签。")
                    else:
                        bg_submit("元数据抽取：" + str(mf),
                                  lambda progress, _p=_p: _meta_worker(ctx, _p, progress),
                                  kind=_LIB_META_KIND)
                        try:
                            st.toast("📄 已转入后台抽取，进度见左侧任务中心，可随时切换其他页面",
                                     icon="🚀")
                        except Exception:
                            pass
            if _meta_bg:
                st.caption("⏳ 元数据抽取正在后台运行（实时进度见左侧「任务中心」）——"
                           "可切换其他页面，完成后结果自动出现在本页。")
            if st.session_state.get("meta_view"):
                with st.expander("🪪 元数据 JSON（点击展开 / 收起）", expanded=True):
                    st.json(st.session_state.meta_view)

        # ---------------- 细粒度结构 ----------------
        _section("🔬 细粒度结构解析")
        if not ctx.files:
            empty_state("文献库为空，暂无可解析的细粒度结构。",
                        hints=["在上方上传 PDF", "或点击左侧「🚀 一键加载演示数据」"])
        else:
            sf = st.selectbox("选择论文", ctx.files, key="struct_sel")
            with_vision = st.checkbox("🖼️ 用多模态模型逐图理解（较慢 / 消耗额度）")
            _struct_bg = bg_running(kinds={_LIB_STRUCT_KIND})
            if st.button("🧩 全方位解析（标题树/表格/图表）",
                         disabled=_struct_bg,
                         help="后台解析：提交后可切换其他页面，实时进度见左侧「任务中心」"):
                _p = resolve_pdf(sf)
                if not _p or not os.path.exists(str(_p)):
                    st.info("📭 该论文为内置演示语料（无原始 PDF），版式级解析不适用；问答与图谱功能不受影响。")
                else:
                    bg_submit("结构解析：" + str(sf),
                              lambda progress, _p=_p, _s=sf, _v=with_vision:
                                  _struct_worker(ctx, _p, _s, _v, progress),
                              kind=_LIB_STRUCT_KIND)
                    try:
                        st.toast("📄 已转入后台解析，进度见左侧任务中心，可随时切换其他页面",
                                 icon="🚀")
                    except Exception:
                        pass
            if _struct_bg:
                st.caption("⏳ 结构解析正在后台运行（实时进度见左侧「任务中心」）——"
                           "可切换其他页面，完成后结果自动出现在本页。")

            stobj = st.session_state.get("struct")
            if stobj and isinstance(stobj, dict):
                if stobj.get("file") != sf:
                    st.caption(f"下方结构来自上一次解析的《{stobj.get('file','—')}》，"
                               f"重新点击「🧩 全方位解析」可刷新为《{sf}》。")
                if not any(stobj.get(k) for k in ("toc", "tables", "figs")):
                    st.info("📭 未识别到标题树 / 表格 / 插图（该 PDF 可能无书签式章节结构或版式表格）。")
                if stobj.get("toc"):
                    with st.expander(f"📚 标题树（{len(stobj['toc'])} 项）", expanded=False):
                        for item in stobj["toc"]:
                            try:
                                level, title, page = item
                            except (TypeError, ValueError):
                                continue
                            st.markdown(f"{'　'*(max(int(level), 1)-1)}- {title} "
                                        f"<span style='color:var(--c-text-3)'>·p{page}</span>", unsafe_allow_html=True)

                if stobj.get("tables"):
                    st.markdown(f"##### 📊 表格（{len(stobj['tables'])}）")
                    for t in stobj["tables"]:
                        try:
                            pretty_table(t.get("df"),
                                         title=f"第 {t.get('page','?')} 页 · 表 {t.get('index','?')}")
                        except Exception as e:
                            friendly_error("表格渲染", e, reasons=["表格数据格式异常"],
                                           actions=["换一个 PDF 重试"])

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
                        except Exception as e:
                            st.caption(f"⚠️ 插图渲染失败（p{fig.get('page','?')}）：{str(e)[:100]}")
                    if len(figs) > MAX_FIG_SHOW:
                        st.caption(f"共 {len(figs)} 张插图，已展示前 {MAX_FIG_SHOW} 张，其余见 storage/uploads/figs。")

            c1, c2 = st.columns([1, 1])
            _formula_bg = bg_running(kinds={_LIB_FORMULA_KIND})
            if c1.button("🧮 提取 LaTeX 公式", disabled=_formula_bg,
                         help="后台抽取：提交后可切换其他页面，进度见左侧「任务中心」"):
                if llm_guard(ctx, "LaTeX 公式抽取"):
                    bg_submit("公式抽取：" + str(sf),
                              lambda progress, _s=sf: _formula_worker(ctx, _s, progress),
                              kind=_LIB_FORMULA_KIND)
                    try:
                        st.toast("📄 已转入后台抽取，进度见左侧任务中心", icon="🚀")
                    except Exception:
                        pass
            if _formula_bg:
                c1.caption("⏳ 公式抽取正在后台运行（进度见左侧「任务中心」）…")
            _ref_bg = bg_running(kinds={_LIB_REF_KIND})
            if c2.button("📚 结构化参考文献", disabled=_ref_bg,
                         help="后台抽取：提交后可切换其他页面，进度见左侧「任务中心」"):
                if llm_guard(ctx, "参考文献结构化"):
                    _p = resolve_pdf(sf)
                    if not _p or not os.path.exists(str(_p)):
                        st.info("📭 该论文为内置演示语料（无原始 PDF），无版式级参考文献区。")
                    else:
                        bg_submit("参考文献结构化：" + str(sf),
                                  lambda progress, _p=_p: _ref_worker(ctx, _p, progress),
                                  kind=_LIB_REF_KIND)
                        try:
                            st.toast("📄 已转入后台抽取，进度见左侧任务中心", icon="🚀")
                        except Exception:
                            pass
            if _ref_bg:
                c2.caption("⏳ 参考文献结构化正在后台运行（进度见左侧「任务中心」）…")
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
                try:
                    pretty_table(pd.DataFrame(st.session_state.ref_view), title="📚 参考文献")
                except Exception as e:
                    friendly_error("参考文献表格渲染", e, reasons=["返回结构不规则"], actions=["重试一次"])

    with tab_lib:
        # ---------------- 当前库 & 删除 ----------------
        if not ctx.files:
            empty_state("文献库为空。", hints=["点击左侧「🚀 一键加载演示数据（v1/v7/BERT）」",
                                           "或在「上传与解析」页签上传 PDF"])
        else:
            # 库概览指标条（Round 17 UI/UX，借鉴 Stripe KPI 卡：hero 指标先行、
            # 明细表格后置）——先回答「库有多大、覆盖如何」，再给逐篇明细表。
            _n_pdf = sum(1 for fn in ctx.files if resolve_pdf(fn))
            try:
                _n_multi = sum(1 for _fs in (library.group_versions(ctx) or {}).values()
                               if len(_fs) > 1)
            except Exception:
                _n_multi = 0
            metric_strip([
                ("文献篇数", len(ctx.files), "含同文多版本"),
                ("片段总数", len(ctx.chunks or []), "问答 / 图谱的检索单位"),
                ("多版本组", _n_multi, "同文多版本已自动识别"),
                ("可定位原文", _n_pdf, "有原始 PDF，可做版式阅读"),
            ])
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
                # ⚠️ 删除后 options（ctx.files）会变短，而 selectbox 的选中值存在
                # session_state 里不会自动失效——残留的旧文件名已不在新选项中，
                # 下一轮渲染会拿它当选中项。必须在实例化 selectbox **之前**清掉
                # （实例化之后回填自己的 key 会抛 StreamlitWidgetAlreadyInstantiatedError）。
                _rm_key = "lib_rm_sel"
                if st.session_state.get(_rm_key) not in ctx.files:
                    st.session_state[_rm_key] = None
                delf = st.selectbox("选择要删除的文献", ctx.files, key=_rm_key)
                if st.button("🗑️ 确认删除所选文献", key="lib_rm_btn"):
                    if not delf:
                        st.info("📭 请先在上方选择要删除的文献。")
                    else:
                        try:
                            library.remove_paper(ctx, delf)
                            set_flash("success", f"✅ 已删除：{delf}")
                            st.rerun()
                        except Exception as e:
                            friendly_error("删除文献", e, reasons=["文件被占用 / 向量库写入失败"],
                                           actions=["重试一次", "重启应用后重试"])

    with tab_ver:
        # ---------------- 版本管理与差异 ----------------
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
                st.markdown(f"🗂️ **同一论文（{gid}）** · 库中有 {len(files)} 版：{'、'.join(files)}")
                try:
                    latest = library.latest_of(files)[0]
                except Exception:
                    latest = ""
                if latest:
                    st.caption(f"建议保留最新版 **{latest}**，其余版本可删除以避免检索重复。")
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
                                if "暂不可用" in txt or "已降级为离线对比" in txt or txt.startswith("【"):
                                    st.warning("⚠️ 版本差异报告已降级：大模型暂不可用，未生成对比表格。")
                                    st.caption("🩺 可能原因：API Key 无效 / 额度不足 / 现场网络受限。")
                                    st.caption("🛠️ 怎么办：填入有效 Key 后重试；"
                                               "无 Key 时可直接讲解「建议保留最新版」与版本清单，演示不受阻断。")
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
