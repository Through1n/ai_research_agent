"""页面 2：抗幻觉跨文献问答。混合检索 + 重排，答案内联【n】引用，下方给出可点击跳转的溯源锚点。

改动要点（抗幻觉 RAG 专项）：
- 主问答流程统一调用 services/rag.py 的 answer()，不再内联重写，保证自动修复环生效；
- 溯源卡片 anchor_card() 已与 ui/common.py 的 source_card 合并为**同一套 .ai-* 构件类
  + design 令牌**，但仍保留本页独有的两处能力（页内锚点 / 阅读页入口，见该函数 docstring）；
- 跳转写入 session_state["qa_jump_page"]（结构化）与 query_params（阅读页已实现的消费方）；
- 支持"全部文献 / 单篇"范围锁定、多篇勾选对比、拒答闸门与可疑引用的可视化。

Round 13 全站视觉重做（本页迁移）：
- _QA_CSS 仅剩两个在用的哨兵类 + pills 字号/圆角，视觉值全部改为 var(--...)，
  并改为会话级幂等注入（原来每轮 rerun 整段重灌）；
- 溯源卡去装饰性阴影改 1px 描边、圆角统一 var(--r)=8px、颜色收敛到唯一强调色
  var(--c-accent)；【n】序号改用公共类 .ai-num。

Round 9 改动（S1）：
- 缺陷 C：库内片段极少（< SMALL_LIBRARY_CHUNKS）时给出可操作的补充引导；
- 缺陷 B：问答期间通过 progress 回调展示"检索→扩展→生成"阶段进度，
  强命中时跳过查询扩展，用户更快看到首字/结果；
- 抗幻觉：展示 result["unverified"]（答案中未在来源验证的数字/实体）与
  引用净化记录；溯源卡片编号与答案角标由 rag.sanitize_citations 保证一一对应。
"""
import html as _html

import streamlit as st
from services import rag
from ui import design as _design
from ui.common import (require_library, page_header, at,
                       is_llm_degraded, llm_degraded_banner, empty_state_card,
                       bg_submit, bg_consume, bg_running)


# 本页局部样式：仅作用于问答页，不改动 ui/common.py；让答案/来源分层更清爽。
# 视觉值全部引用 ui/design.py 的令牌（页面渲染时已幂等注入，主文档内可直接用 var）。
# ⚠️ .qa-answer-start / .qa-src-gap 是**仍在使用**的哨兵类（见 render 历史循环与
#    _render_source）：只改样式值，绝不删类名。已删除两个零调用者的死选择器
#    （.qa-hint、.qa-ex-wrap —— 2026-10-24 全仓 grep 确认无任何使用点）。
_QA_CSS = """
<style>
.qa-answer-start{height:0;border-top:1px solid var(--c-border);margin:var(--s-2) 0 var(--s-1);}
.qa-src-gap{height:var(--s-2);}
</style>
"""
_QA_CSS_FLAG = "_qa_page_css_injected"   # 本轮幂等旗标（值=本轮 _run_seq）


def _inject_qa_css():
    """**本轮幂等**注入本页局部 CSS（原实现在 render() 里每轮 rerun 都整段重灌一次）。

    ⚠️ 原实现是会话级旗标（置 True 后永久跳过）——这是真实事故（2026-10-24）：
    Streamlit 每次 rerun 重建元素树，上一轮的 <style> 不保留；旗标导致第二次起不再
    注入，本页样式在任意一次交互后消失。现按 app.py 的 `_run_seq` 判定本轮是否已注入
    （与 ui/common.py::_inject_css_once 同一口径）。
    """
    try:
        _design.ensure()
    except Exception:
        pass
    try:
        seq = st.session_state.get("_run_seq")
        if seq is not None and st.session_state.get(_QA_CSS_FLAG) == seq:
            return
        st.markdown(_QA_CSS, unsafe_allow_html=True)
        st.session_state[_QA_CSS_FLAG] = seq if seq is not None else True
    except Exception:
        pass


EX = ["这篇论文提出了什么模型？", "训练用了什么 GPU？数量和型号？",
      "对比 Transformer 与 BERT 的架构（表格）", "GPT/BERT/Transformer 参数量演化",
      "Adam 的 beta_1、beta_2 是多少？BLEU 分数分别是多少？"]

# ---------- 本页自持常量（不改动 config.py） ----------
SNIPPET_LEN = 200            # 溯源卡片折叠区展示的原文片段长度（字）
MAX_COMPARE_PAPERS = 5       # 多篇对比最多勾选篇数
JUMP_KEY = "qa_jump_page"    # 跨页联动 session_state key
READING_PAGE_IDX = 3         # 📖 版式阅读页在 MENU 中的索引（ui/sidebar.py MENU / app.py）
SMALL_LIBRARY_CHUNKS = 8     # 与 services/rag.py 保持一致：片段少于此值给出补充引导
_K_QA = "qa_answer"          # 后台任务 kind：问答生成（bg_submit/bg_consume 认领用）

# 查询扩展模式 → 人话说明（debug["expansion_mode"]）
_EXPAND_MODE_LABELS = {
    "llm": "LLM 查询扩展（弱命中触发）",
    "cached": "查询扩展命中缓存（未重复调用 LLM）",
    "skipped_strong_hit": "原查询已强命中，跳过查询扩展（节省一次 LLM 调用）",
    "offline_strong_hit": "原查询已强命中，跳过 LLM 扩展；离线符号对齐变体仍参与检索（零成本）",
    "offline_no_key": "未配置 Key，离线词表兜底",
    "offline_key_issue": "Key 形态异常，已拦截并走离线词表兜底",
    "offline_llm_failed": "LLM 扩展失败，离线词表兜底",
}


def _library_guidance(ctx):
    """缺陷 C：库内片段极少时返回可操作引导文案；内容丰富返回 None。"""
    try:
        n = len(getattr(ctx, "chunks", []) or [])
    except Exception:
        n = 0
    if n == 0:
        return None  # require_library 已给出空库引导
    if n < SMALL_LIBRARY_CHUNKS:
        return (f"当前文献库仅 {n} 个片段（{len(getattr(ctx, 'files', []) or [])} 篇），"
                f"检索覆盖面有限，答案可能不完整。建议：① 到「文献库管理」上传更多 PDF；"
                f"② 或点击左侧「一键加载演示数据（6 篇）」；③ 或在该页重建索引后重试。")
    return None


def _init_state():
    """所有 session_state key 先初始化再读。"""
    if "qa_runs" not in st.session_state:
        st.session_state["qa_runs"] = []
    if "qa_q" not in st.session_state:
        st.session_state["qa_q"] = ""
    if JUMP_KEY not in st.session_state:
        st.session_state[JUMP_KEY] = None
    if "qa_scope_mode" not in st.session_state:
        st.session_state["qa_scope_mode"] = "全部文献"
    if "qa_single_paper" not in st.session_state:
        st.session_state["qa_single_paper"] = ""
    if "qa_compare_papers" not in st.session_state:
        st.session_state["qa_compare_papers"] = []
    if "qa_use_rerank" not in st.session_state:
        st.session_state["qa_use_rerank"] = False
    if "qa_input" not in st.session_state:
        st.session_state["qa_input"] = ""
    if "qa_run_seq" not in st.session_state:
        st.session_state["qa_run_seq"] = 0


def _esc(s):
    return _html.escape(str(s or ""), quote=True)


def anchor_card(source, page, score=0.0, para_idx=None, n=None):
    """溯源卡片：**已与 ui/common.py 的 source_card 合并为同一套视觉实现**。

    Round 13 前这里是第二套溯源卡（自建内联样式：圆角 8px/12px 混用、#f7faff 底、
    #3b6fb6/#27518a 第二强调色、装饰性 box-shadow）。现改为与 source_card 完全同构：
    同一批 .ai-card / .ai-chip / .ai-ellipsis / .ai-num 构件类 + 同一套 var(--...) 令牌，
    圆角一律 var(--r)、间距一律 8px 倍数、去阴影改 1px 描边、颜色只用唯一强调色。

    **合并≠删功能**——本页独有的两处能力全部保留（source_card 的跨页能力也不丢）：
    1) 卡片顶部 <a id="p{page}"> 页内锚点落点（source_card 无，本页独有）；
    2) 页码徽章 <a href="#p{page}"> 页内锚点跳转（source_card 的徽章是**跨页** href，
       语义不同，故保留本页的页内语义）；
    3) 徽章旁的「打开阅读页」<a href="?p=3&jump_page=N&paper=X"> 跨页跳阅读页
       —— 这正是 common.source_card 的跨页能力，本卡把它保留在自己独有的入口上
       （ui/page_reading.py 已消费 jump_page / paper 两个 query 参数）。

    💡 建议后续收敛：给 ui/common.py 的 source_card 加两个开关参数
    ``anchor_id=True``（生成 <a id="p{N}"> + 徽章 href 改页内）与
    ``reading_entry=True``（追加「打开阅读页」入口），本函数即可退化为一次调用。
    本轮不改 common.py（他人并行在改），故在此内联保留，能力一个都不少。
    """
    try:
        _design.ensure()
    except Exception:
        pass
    name = _esc(source)
    # 【n】角标改用公共序号类 .ai-num（强调色 + 粗体），与空状态卡片同一套标记
    tag = f'<span class="ai-num">【{n}】</span> ' if n else ""
    para = f" · 第{int(para_idx)}段" if para_idx else ""
    # 强调色底上的徽章必须显式给白字：design 层有全局 a{color:var(--c-accent)}，
    # 不覆盖会变成「蓝底蓝字」看不见
    badge = (f'<a href="#p{int(page)}" style="color:var(--c-on-accent);text-decoration:none;'
             f'display:inline-block;">第 {int(page)} 页</a>')
    cross = (f'<a href="?p={READING_PAGE_IDX}&jump_page={int(page)}&paper={name}" target="_self" '
             f'style="color:var(--c-accent);text-decoration:none;">打开阅读页</a>')
    return (
        f'<a id="p{int(page)}"></a>'
        f'<div class="ai-card" style="border-left:3px solid var(--c-accent);">'
        f'<div style="display:flex;flex-wrap:wrap;gap:var(--s-2);'
        f'justify-content:space-between;align-items:baseline;'
        f'margin-bottom:var(--s-2);min-width:0;">'
        f'<strong class="ai-ellipsis" style="flex:1 1 auto;min-width:0;'
        f'font-size:var(--fs-body);line-height:var(--lh-body);'
        f'color:var(--c-text);font-weight:600;">{tag}📄 {name}</strong>'
        f'<span class="ai-chip" style="background:var(--c-accent);color:var(--c-on-accent);'
        f'flex:0 0 auto;">{badge}</span></div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:var(--s-2);align-items:baseline;'
        f'min-width:0;font-size:var(--fs-caption);line-height:var(--lh-caption);'
        f'color:var(--c-text-2);">'
        f'<span>相关度 {float(score):.2f}{para}</span>{cross}</div></div>'
    )


def _set_jump(source, page, para_idx=None):
    """写入跨页联动状态：结构化 session_state + 阅读页已在消费的 query_params。"""
    try:
        payload = {"source": str(source), "page": int(page),
                   "para_idx": int(para_idx) if para_idx else None, "from": "qa"}
    except (TypeError, ValueError):
        payload = {"source": str(source), "page": 1, "para_idx": None, "from": "qa"}
    st.session_state[JUMP_KEY] = payload
    try:
        st.query_params["jump_page"] = str(payload["page"])
        st.query_params["paper"] = str(payload["source"])
    except Exception as e:
        st.caption(f"（写入 URL 参数失败，已仅用会话状态记录：{str(e)[:80]}）")
    st.success(f"已定位到《{payload['source']}》第 {payload['page']} 页"
               + (f"（第 {payload['para_idx']} 段）" if payload["para_idx"] else "")
               + "，可到版式阅读页查看。")


def _render_source(rid, i, c):
    """一条来源：溯源卡（相关度 / 段落 / 阅读页入口）+ 定位按钮 + 原文片段折叠区。

    ⚠️ 布局为何从 [1.3, 1.1, 3.0] 三列改成 [1, 2] 两列（Round 13 实测计算，1200px
    桌面壳下主区内容宽 = min(1280,1200) - 2×40 = 1120px）：
      · 令牌层给每个 stColumn 加了 ``min-width:min(--col-basis(280px),100%)``；
      · 三列的 flex 基准约为 259 / 219 / 597，前两列被 280px 下限抬到 280+280，
        **假设主尺寸合计 1157px > 容器 1120px** → 第三列（expander）被挤到第二行，
        第一行只剩两个被 flex-grow 拉到 560px 的按钮 / 说明列。这是**确定性塌陷**，
        不是窄屏自适应换行，光靠视觉迁移修不掉；
      · 且 col_meta 的「相关度 x.xx｜第 N 段」与溯源卡第三行**完全重复**，是冗余信息。
    改后：定位按钮 373px / 片段折叠 747px，1120～1024 断点均稳定并排；相关度与段落
    仍在卡片内可见，信息零损失。widget 文案与 key（jump_{rid}_{i}）一律未动。
    """
    source = c.get("source", "未知来源")
    try:
        page = int(c.get("page", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    st.markdown(anchor_card(source, page, c.get("score", 0.0), c.get("para_idx"), n=i),
                unsafe_allow_html=True)
    # 定位按钮 / 原文片段两列：卡片下方留白由 .qa-src-gap 统一给
    col_btn, col_snip = st.columns([1, 2])
    with col_btn:
        if st.button(f"📖 定位第 {page} 页", key=f"jump_{rid}_{i}", use_container_width=True):
            _set_jump(source, page, c.get("para_idx"))
    with col_snip:
        with st.expander(f"原文片段（前 {SNIPPET_LEN} 字）", expanded=False):
            st.text((c.get("text") or "")[:SNIPPET_LEN])
    st.markdown('<div class="qa-src-gap"></div>', unsafe_allow_html=True)


def _render_debug(run):
    d = run.get("debug") or {}
    st.caption(f"向量召回 {len(d.get('dense') or [])} 条，BM25 召回 {len(d.get('sparse') or [])} 条，"
               f"RRF 融合后 {len(d.get('fused') or [])} 条")
    if d.get("variants"):
        st.caption("🌐 查询扩展（dense 与 sparse 双路独立参与 RRF）：" + " ┃ ".join(d["variants"]))
    dense_ranks = d.get("dense_variant_ranks") or []
    if len(dense_ranks) > 1:
        st.caption(f"🧬 dense 路消费 {len(dense_ranks)} 个查询变体（含原查询），结果按 RRF(k=60) 合并")
    if d.get("source_filter"):
        st.caption(f"🔒 范围已锁定单篇：{d['source_filter']}")
    cap = d.get("source_cap") or {}
    if cap:
        dist = "，".join(f"{k}×{v}" for k, v in (cap.get("per_source") or {}).items())
        st.caption(f"⚖️ 来源多样性截断：融合池 {cap.get('pool', 0)} 条 → 入选 {cap.get('kept', 0)} 条"
                   f"（{dist}）——防止大书淹没 top-k")
    if (d.get("source_cap") or {}).get("dropped_stale"):
        st.caption(f"🗑️ 同论文多版本去重：丢弃旧版本片段 {(d.get('source_cap') or {}).get('dropped_stale')} 条"
                   "（最新版入池覆盖充分，旧版数值可能已被作者修订）")
    if d.get("per_paper"):
        st.caption("📚 对比模式每篇取到：" + "，".join(f"{k}×{v}条" for k, v in d["per_paper"].items()))
    mode = d.get("expansion_mode")
    if mode:
        label = _EXPAND_MODE_LABELS.get(mode, mode)
        note = f"（{d['expand_note']}）" if d.get("expand_note") else ""
        st.caption(f"🧭 查询扩展：{label}{note}")
    t = d.get("timings") or {}
    if t:
        st.caption(f"⏱️ 检索耗时：原查询双路 {t.get('phase1_ms', 0)}ms"
                   f" + 扩展/变体 {t.get('expand_ms', 0)}ms")
    st.caption(f"🎯 最高相关度 {float(d.get('top_score') or 0):.2f}"
               f"（拒答阈值 {rag.RELEVANCE_FLOOR}）；自动修复次数 {int(d.get('repairs') or 0)}"
               f"（上限 {rag.MAX_REPAIR}）")
    hit = set(d.get("sparse") or []) & set(d.get("dense") or [])
    if hit:
        st.caption(f"✅ 双路共同命中 {len(hit)} 条——混合检索交叉验证降低漏检")


def _qa_worker(ctx, question, scope_label, use_rerank, source_filter, compare_papers, progress):
    """后台任务体：跑 rag.answer 并组装成一条可入库的 run 记录。

    ⚠️ worker 线程内**禁止**任何 st.*（会抛 ScriptRunContext 缺失并污染会话）：
    这里只做纯计算 + 数据组装，渲染交给 _consume_qa 在主线程完成。
    为什么必须后台化：原实现把 rag.answer 直接跑在主线程上，单次问答 7～20s
    （多篇对比可达 1～2 分钟），期间 Streamlit 无法处理任何新事件——侧边栏
    导航点击全部排队，用户反馈「切到别的页面会卡顿，切不出去」。
    """
    def _say(stage):
        try:
            progress(str(stage), None)
        except Exception:
            pass

    result = rag.answer(ctx, question, use_rerank=use_rerank,
                        source_filter=source_filter,
                        compare_papers=list(compare_papers) if compare_papers else None,
                        progress=_say)
    if not result:
        return None
    return {
        "q": question,
        "a": result.get("answer", ""),
        "sources": result.get("sources", []),
        "issues": result.get("issues", []),
        "unverified": result.get("unverified", []),
        "debug": result.get("debug", {}),
        "refused": bool(result.get("refused")),
        "top_score": result.get("top_score", 0.0),
        "scope": scope_label,
    }


def _consume_qa(kind, task):
    """bg_consume 处理器：把后台问答结果登记进 qa_runs（主线程执行）。

    一次性语义：失败时只 flash 错误，不写半成品；成功时同时置「清空输入框」标记，
    由 render 顶部消费（那时 st.text_input 还没实例化，写 key 才合法）。
    """
    if task.get("state") != "done":
        try:
            from ui.sidebar import set_flash
            set_flash("error", "⚠️ 问答生成失败：%s" % str(task.get("error") or "")[:160])
        except Exception:
            pass
        return
    run = task.get("result")
    if not isinstance(run, dict) or not run.get("q"):
        return
    run = dict(run)
    run["id"] = st.session_state.get("qa_run_seq", 0)
    st.session_state.setdefault("qa_runs", []).append(run)
    st.session_state["qa_run_seq"] = int(st.session_state.get("qa_run_seq", 0)) + 1
    st.session_state["_qa_need_clear_input"] = True


def render(ctx):
    page_header("跨文献问答与溯源",
                "Dense+Sparse 混合检索（多查询变体 RRF）→ 可选 LLM 重排 → 引用校验 + 一次自动修复；"
                "每个结论带【n】内联引用与可点击的原文出处锚点。")
    _inject_qa_css()
    if not require_library(ctx):
        return
    _init_state()

    # 后台任务交付必须放在所有 widget 之前：consume 会写 qa_runs / 清空标记，
    # 并触发一次整页重绘，本轮后续渲染读到的直接就是新结果。
    # "提交后切走再切回来"的自动出结果由 bg_consume 内部的 bg_watch 兜住
    # （每个已后台化页面通用，无需在页面里单独挂轮询）。
    bg_consume({_K_QA: _consume_qa})

    # 上一轮提交成功后留下的「清空输入框」标记：必须在这里消费——此刻带
    # key="qa_input" 的 st.text_input 还没实例化，写它合法。晚一步（在提交完成的
    # 当轮直接写）会抛 StreamlitWidgetAlreadyInstantiatedError，整页崩成错误卡片：
    # 2026-10-24 真机实测，答案已经流式显示出来了，一两秒后整页被错误卡替换掉。
    if st.session_state.pop("_qa_need_clear_input", False):
        st.session_state["qa_input"] = ""
        st.session_state["qa_q"] = ""
        # 复位胶囊选中态 + 影子键（2026-10-26 用户缺陷：「点示例问题没反应」）。
        # 原实现只清输入框，胶囊仍处选中态、影子键仍记着上一次的值 ⇒ 用户再点
        # 同一个胶囊时，先取消选中（_sel=None 不写），再选回来（_val == _qa_ex_prev
        # 不写），输入框永远是空的；界面上还留着「已选中但输入框空」的矛盾状态。
        st.session_state["qa_ex_pills"] = None
        st.session_state["_qa_ex_prev"] = ""

    # ---------- 一行状态带（首屏唯一的说明性文字，其余提示收进折叠区 / 答案区） ----------
    try:
        _n_chunks = len(getattr(ctx, "chunks", []) or [])
    except Exception:
        _n_chunks = 0
    st.caption(f"文献库 {len(getattr(ctx, 'files', []) or [])} 篇 · {_n_chunks} 个片段 ｜ "
               f"混合检索 + 引用校验 + 自动修复（检索范围与重排见下方折叠区）")

    # ---------- 缺陷 C：库内内容极少时的可操作引导 ----------
    # 非阻断信息，降级为 caption：不与「主操作」抢首屏（原为 st.info 整块色条）
    guidance = _library_guidance(ctx)
    if guidance:
        st.caption(guidance)

    # 跨页联动提示：本页写入，版式阅读页消费（见报告"跨文件约定"）
    # 从「只说不做」的文字提示升级为一键操作条（Round 17 UI/UX）：用户点过
    # 「📖 定位第 N 页」后意图已经明确，不该再让他去侧边栏 9 个菜单里找阅读页。
    _jump_action_bar()

    # 示例问题：一行紧凑 chips（st.pills 原生胶囊样式）
    # —— 借鉴 Consensus「Suggested Follow-Up Questions」/ Elicit 极简界面，降低首屏决策成本。
    # ⚠️ 这里有两个坑（都是本项目踩过的）：
    #   ① widget 实例化后**禁止**回填它自己的 key（StreamlitWidgetAlreadyInstantiatedError），
    #      所以不能写 st.session_state["qa_ex_pills"] = None 来复位；
    #   ② 也不能用「中段 st.rerun()」把问题刷进输入框——rerun 会让排在它后面的
    #      multiselect 等 widget 本轮根本不实例化，Streamlit 据此把这些 widget 的
    #      状态裁剪重置（2026-10-22 实测：用户选好的对比论文被清成 []，
    #      反馈「我没法在此勾选论文比对」）。点击 pill 本身就会触发一次自动重跑，
    #      同一轮里写输入框的显式 key 即可带入，无需二次 rerun。
    # 解法：影子键 _qa_ex_prev 只在选项值**发生变化**时写入 qa_input（输入框的显式
    # key，实例化前写入即默认值），不变化就不写——防止用户手改输入框后被选中态覆盖。
    # 说明：点击仅「填入」输入框（与改造前示例按钮语义一致），需用户再点「提问」才真正
    # 发起检索——避免误触就直接打真实模型。
    # ---------- 主操作区：问题输入 + 提交（首屏第一视线） ----------
    # ⚠️ 这里**不能**用 st.form（2026-10-26 真机最小复现的结论，见
    #    scripts/_tmp_form_probe.py 与 scripts/_probe_qa_reclick.py）：
    #    st.form 一旦提交过，前端会为表单内 widget 钉住"表单态"（含
    #    clear_on_submit 清零值），**服务端下发的 value 不再生效**。于是
    #    "点示例问题胶囊 → 写 st.session_state['qa_input'] → 输入框填入"这条链路
    #    在第一次提问之后就永久断掉（实测：提交后点任意胶囊，DOM value 恒为 ''）。
    #    换 form key 强制重挂载**也无效**（真实浏览器实测），只能放弃 st.form。
    #    代价：失去"回车提交"，改为显式点「提问」按钮——与胶囊文案
    #    「点击填入下方输入框，再点「提问」」本来就说的一致。
    # AppTest 读的是脚本侧 widget 值，完全看不见这个缺陷（一直是绿的），
    # 所以本条只能靠真实浏览器门禁兜住。
    # 卡片：示例胶囊 + 输入框 + 提交按钮属于同一次「提问」动作，包成一张卡，
    # 否则三者在页面上是散落的控件，与下方答案区（card10）也不成体系。
    with st.container(border=True, key="card22"):
        _sel = st.pills("试试这些问题（点击填入下方输入框，再点「提问」）",
                        EX, key="qa_ex_pills", selection_mode="single", default=None)
        if _sel:
            _val = _sel if isinstance(_sel, str) else (_sel[0] if _sel else "")
            if _val and _val != st.session_state.get("_qa_ex_prev"):
                st.session_state["qa_q"] = _val
                st.session_state["qa_input"] = _val   # 输入框显式 key：实例化前写入即默认值
                st.session_state["_qa_ex_prev"] = _val   # 影子键：非 widget key，可安全写

        _qa_run = bg_running(kinds={_K_QA})
        _c_q, _c_go = st.columns([4, 1], vertical_alignment="bottom")
        with _c_q:
            q = st.text_input("请输入问题", key="qa_input",
                              placeholder="例如：这篇论文提出了什么模型？（支持中文提问，自动做中英检索扩展）")
        with _c_go:
            go = st.button("提问", key="qa_ask", type="primary", use_container_width=True,
                           disabled=_qa_run,
                           help="后台生成：提交后可切换其他页面，进度见左侧「任务中心」")
        if _qa_run:
            st.caption("⏳ 正在后台检索并生成答案（进度见左侧「任务中心」）——可随时切换到其他页面。")

    # ---------- 检索范围与高级设置（首屏不铺开；功能逐项保留） ----------
    # 顺序说明：范围 / 对比 / 重排的值在下方 `if go` 之前完成计算，提交当轮即可读到，
    # 只是把「设置类」控件从首屏挪进折叠区，不改任何取值逻辑与 session_state 键。
    with st.expander("检索范围与高级设置", expanded=False):
        scope = st.radio("检索范围", ["全部文献", "单篇"], horizontal=True, key="qa_scope_mode")
        source_filter = None
        if scope == "单篇" and ctx.files:
            cur = st.session_state.get("qa_single_paper")
            if cur not in ctx.files:
                st.session_state["qa_single_paper"] = ctx.files[0]
            source_filter = st.selectbox("锁定论文（只在该篇内检索）", ctx.files, key="qa_single_paper")

        st.session_state["qa_compare_papers"] = [p for p in st.session_state.get("qa_compare_papers", [])
                                                 if p in ctx.files]
        compare_papers = st.multiselect(
            f"多篇对比：勾选 2~{MAX_COMPARE_PAPERS} 篇（走按论文分组的对比流程，输出分论文陈述 + 对比表格）",
            ctx.files, key="qa_compare_papers")
        if len(compare_papers) > MAX_COMPARE_PAPERS:
            st.caption(f"最多勾选 {MAX_COMPARE_PAPERS} 篇，已自动截取前 {MAX_COMPARE_PAPERS} 篇。")
            compare_papers = compare_papers[:MAX_COMPARE_PAPERS]
        if scope == "单篇" and compare_papers:
            st.caption("ℹ️ 已选择单篇锁定，多篇对比勾选本次不生效（两种模式互斥）。")
            compare_papers = []
        if len(compare_papers) == 1:
            st.caption("ℹ️ 多篇对比至少需要 2 篇，本次按普通问答处理。")
            compare_papers = []

        use_rerank = st.checkbox("启用 LLM 重排（更精准，耗额度）", key="qa_use_rerank")

    # 无问答历史时的教学式空状态（Round 17 UI/UX）：提问前先讲清「答案会长什么样」，
    # 避免首屏只有输入框、用户不确定提交后会发生什么（Notion/Linear 式空状态教学）。
    if not st.session_state.get("qa_runs"):
        empty_state_card(
            "还没有问答记录",
            desc="提问后，答案会出现在这里：内联【n】引用标注来源，下方附可点击定位原文的溯源卡片。",
            actions=["点上方「试试这些问题」胶囊自动填入输入框，再点「提问」"],
            icon="💬",
        )

    # 提交了空问题时的显式反馈（2026-10-26 用户缺陷：「点提问也不出结果」）。
    # 原实现 `if go and q.strip():` 之外没有任何分支——表单提交成功、问题为空时
    # 页面一个像素都不变，用户完全看不出发生了什么（真机实测：qa_runs 不变、
    # 无 warning、无 info，纯静默）。配合上面的胶囊复位，这里给出可操作下一步。
    if go and not q.strip():
        st.info("还没有输入问题：请在上方输入框写一句，或点上方任意示例问题胶囊"
                "自动填入内容，再点「提问」。")

    if go and q.strip():
        if not getattr(ctx.llm, "available", False):
            st.warning("请先在左侧填入 API Key。")
        else:
            # 后台化（2026-10-26）：原实现把 rag.answer 跑在主线程 + 内联 st.empty
            # 流式渲染，单次问答 7～20s（多篇对比 1～2 分钟）整页冻结，期间侧边栏
            # 导航点击全部排队 ⇒ 用户反馈「切页卡顿、切不出去」。改为 bg_submit
            # 提交到 services/tasks 线程池：提交后即可自由切页，进度见左侧任务中心，
            # 完成时由 bg_consume 交付（答案仍然只以**已校验**版本呈现，流式预览的
            # 那层"未校验占位"随之取消）。
            _scope_label = (f"单篇：{source_filter}" if source_filter
                            else ("对比：" + "、".join(compare_papers) if compare_papers
                                  else "全部文献"))
            bg_submit(f"跨文献问答：{q.strip()[:24]}",
                      lambda progress, _q=q.strip(), _sl=_scope_label: _qa_worker(
                          ctx, _q, _sl, use_rerank, source_filter,
                          list(compare_papers) or None, progress),
                      kind=_K_QA)
            try:
                st.toast("💬 已提交到后台生成，可随时切换到其他页面")
            except Exception:
                pass

    # ---------- 历史问答渲染（答案优先，证据/调试默认折叠，保持界面整洁） ----------
    for run in reversed(st.session_state.get("qa_runs", [])):
        rid = run.get("id", 0)
        st.markdown(f"#### 🙋 {run.get('q', '')}")
        st.caption(f"检索范围：{run.get('scope', '全部文献')}")
        _ans = str(run.get("a", "") or "")
        _degraded = (not run.get("refused")) and is_llm_degraded(_ans)
        if run.get("refused"):
            st.warning("🛑 " + _ans)
        else:
            # 答案区：轻分隔 + 内联【n】引用保持可点跳转（答案优先于证据，视觉分层）
            st.markdown('<div class="qa-answer-start"></div>', unsafe_allow_html=True)
            # 离线占位不能看起来像正常答案（Round 12 ②残留）
            if _degraded:
                llm_degraded_banner()
            st.markdown(_ans)
        if run.get("issues"):
            st.warning("⚠️ 幻觉防护校验未完全通过：\n" + "\n".join(f"- {x}" for x in run["issues"]))
        elif _degraded:
            st.caption("⚠️ 离线降级结果未做引用校验（没有真实模型输出可供校验）。")
        else:
            st.success("✅ 引用校验通过：角标均在有效来源范围内，且与来源文本存在实质词汇重叠")
        if run.get("unverified"):
            st.warning("⚠️ 以下内容未在来源文本中找到对应依据（已自动标注，请谨慎对待）：\n"
                       + "\n".join(f"- {x}" for x in run["unverified"]))
        srcs = run.get("sources") or []
        if srcs:
            # 「📚 答案来源」溯源区：小标题改用全站公共构件 .ai-section-title
            # （左侧 3px 强调竖条 + --fs-title），与其余页面分区层级一致；文案原样保留。
            # 默认折叠，卡片间距统一、页码徽章与跳转按钮对齐
            st.markdown('<div class="ai-section-title">📚 答案来源</div>',
                        unsafe_allow_html=True)
            with st.expander(f"共 {len(srcs)} 条来源 · 点击展开可定位原文", expanded=False):
                for i, c in enumerate(srcs, 1):
                    _render_source(rid, i, c)
        # 🛠️ 检索调试：默认折叠、字号小、不喧宾夺主（高级信息后置）
        with st.expander("🛠️ 检索调试（高级）", expanded=False):
            _render_debug(run)
        st.markdown("---")


# ---------- 跨页跳转操作条（Round 17 UI/UX） ----------
def _jump_payload(payload):
    """把 session_state["qa_jump_page"] 归一化为 {source, page, para}；无效返回 None。

    纯函数（不碰 st.*），便于单测；渲染逻辑见 _jump_action_bar。
    """
    if not isinstance(payload, dict) or not payload.get("source"):
        return None
    try:
        page = int(payload.get("page", 1) or 1)
    except (TypeError, ValueError):
        page = 1
    para = payload.get("para_idx")
    try:
        para = int(para) if para else None
    except (TypeError, ValueError):
        para = None
    return {"source": str(payload.get("source")), "page": page, "para": para}


def _jump_action_bar():
    """待跳转定位操作条：从「文字提示用户自己切导航」升级为「一键直达」。

    借鉴 duply / Vercel 的 deep-linking 实践：用户意图已经明确（点过「📖 定位」），
    就该一键落到目标状态，而不是让他在侧边栏 9 个菜单里重新找一遍阅读页。

    跳转机制与 ui/common.py jump_banner 完全同构（不另起一套）：
    session_state["menu"] + query_params（p/jump_page/paper）双通道 + rerun。
    ⚠️ Streamlit 状态机：写 "menu" 是安全的——导航 radio 的 widget key 是
    "_nav_main"，"menu" 只是普通 session 变量（见 ui/sidebar.py:415）。
    """
    info = _jump_payload(st.session_state.get(JUMP_KEY))
    if not info:
        return
    tail = f"（第 {info['para']} 段）" if info["para"] else ""
    with st.container(border=True, key="card10"):
        st.markdown(f"🎯 **问答溯源待跳转**：《{_esc(info['source'])}》"
                    f"第 {info['page']} 页{tail}")
        st.caption("点击下方按钮直达「📖 版式阅读与翻译」并定位到该页；查看后点「已查看」清除本提示。")
        c1, c2 = st.columns([1, 1])
        if c1.button("📖 立即打开阅读页", key="qa_jump_open", use_container_width=True):
            try:
                from ui.sidebar import MENU
                st.session_state["menu"] = at(MENU, READING_PAGE_IDX,
                                              MENU[0] if MENU else "")
                # URL 参数失败不能顺带吞掉跳转本身：逐项 try，保证 rerun 一定执行
                for _k, _v in (("p", str(READING_PAGE_IDX)),
                               ("jump_page", str(info["page"])),
                               ("paper", str(info["source"]))):
                    try:
                        st.query_params[_k] = _v
                    except Exception:
                        pass
                st.rerun()
            except Exception as e:
                st.caption(f"跳转失败，请手动切换左侧导航：{str(e)[:100]}")
        if c2.button("✅ 已查看，清除", key="qa_jump_clear", use_container_width=True):
            st.session_state[JUMP_KEY] = None
            try:
                st.rerun()
            except Exception:
                pass
