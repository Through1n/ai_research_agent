#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
============================================================================
 _verify_live_api.py —— API Key 就绪后的「AI 链路端到端实测」
============================================================================

背景
----
项目中 31 处 LLM 调用点全部依赖 .env 里的 API Key。在 Key 为占位串期间，
所有 AI 功能只做过「离线降级 / 不崩溃」验证，**从未真正跑通一次**。
本脚本用于在填入真实 Key 后，一次性实测全部 AI 链路，输出真实 PASS/FAIL。

设计原则
--------
1. **绝不造假**：Key 不可用 → 报 SKIP 并写明原因，绝不算 PASS；
   接口返回空/异常 → 报 FAIL，绝不静默通过。
2. **每条用例独立 try/except**，单条崩溃不影响后续用例。
3. **断言真实语义**：细粒度问答会检查答案里是否真的出现了期望数值
   （如 β2=0.98、8×P100、BLEU 28.4），而不只是「有没有返回文字」。
4. **不污染 storage/**：入库用例在临时副本上跑，跑完还原。

用法（项目实际环境）
--------------------
    conda activate ai_research
    python scripts/_verify_live_api.py              # 跑全部用例
    python scripts/_verify_live_api.py --only ocr   # 只跑某一组
    python scripts/_verify_live_api.py --skip ocr   # 跳过耗时的某组

分组：ocr / qa / guard / compare / write / graph / extern
============================================================================
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------- 结果收集

ROWS: list[dict] = []


def record(group: str, name: str, ok: bool | None, evidence: str, note: str = "") -> None:
    """ok=True PASS / False FAIL / None SKIP"""
    ROWS.append({
        "group": group,
        "name": name,
        "ok": ok,
        "status": "PASS" if ok is True else ("FAIL" if ok is False else "SKIP"),
        "evidence": (evidence or "")[:400],
        "note": note,
    })
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}[ROWS[-1]["status"]]
    print(f"  {icon} [{group}] {name}\n       {ROWS[-1]['evidence'][:200]}")


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# ---------------------------------------------------------------- 环境准备

def load_env() -> dict:
    """读取 .env，判断 Key 是否真的可用（必须是非空、纯 ASCII 的合理长度）。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except Exception:
        pass

    provider = (os.getenv("LLM_PROVIDER") or "zhipu").strip().lower()
    key_map = {
        "zhipu": "ZHIPU_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "openai": "OPENAI_API_KEY",
        "ollama": "OLLAMA_API_KEY",
    }
    var = key_map.get(provider, "ZHIPU_API_KEY")
    key = (os.getenv(var) or "").strip()

    info = {
        "provider": provider,
        "var": var,
        "key": key,
        "len": len(key),
        "ascii": key.isascii(),
        "usable": False,
        "reason": "",
    }
    if not key:
        info["reason"] = f"{var} 为空"
    elif not key.isascii():
        info["reason"] = f"{var} 含非 ASCII 字符（占位串：{key[:20]}）"
    elif len(key) < 20:
        info["reason"] = f"{var} 仅 {len(key)} 字符，不像真实 Key"
    else:
        info["usable"] = True
    return info


_TMP_DIRS: list[str] = []
_SHARED_CTX = None
_LOADED: set[str] = set()


def make_ctx():
    """全局共享一个跑在临时目录里的 Container（不碰真实 storage/）。

    Container 用模块级常量决定落盘位置，因此必须在 import 后、实例化前
    把这些常量改写到临时目录。共享是必要的：重建 Container 会重新初始化
    chroma 客户端并重复入库，实测会让整轮跑超时。
    """
    global _SHARED_CTX
    if _SHARED_CTX is not None:
        return _SHARED_CTX
    import tempfile
    import services.state as state_mod

    tmp = tempfile.mkdtemp(prefix="ai4s_live_")
    _TMP_DIRS.append(tmp)
    tp = Path(tmp)
    state_mod.CHROMA_DIR = tp / "chroma"
    state_mod.LIBRARY_JSON = tp / "library.json"
    state_mod.MEMORY_JSON = tp / "memory.json"
    state_mod.PROFILE_JSON = tp / "profile.json"
    _SHARED_CTX = state_mod.Container()
    return _SHARED_CTX


def ensure_papers(*names: str):
    """按需入库，已入库的跳过（避免重复解析拖垮整轮）。"""
    from services.library import prepare_pdf, commit
    ctx = make_ctx()
    for n in names:
        if n in _LOADED:
            continue
        p = ROOT / "论文样本" / n
        if not p.exists():
            continue
        commit(ctx, prepare_pdf(ctx, path=str(p), filename=n))
        _LOADED.add(n)
    return ctx


def cleanup_tmp():
    for d in _TMP_DIRS:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


# ============================================================ 用例组 1: OCR

def group_ocr(env: dict, only: set, skip: set) -> None:
    section("用例组 A：扫描件 OCR（官方用例 1706.03762v1_img.pdf）")
    if "ocr" in skip or (only and "ocr" not in only):
        record("ocr", "扫描件 OCR 端到端", None, "已跳过", "")
        return

    pdf = ROOT / "论文样本" / "1706.03762v1_img.pdf"
    if not pdf.exists():
        record("ocr", "扫描件 OCR 端到端", None, f"缺文件 {pdf.name}", "")
        return

    try:
        import fitz
        from services.parsing import detect_scanned, ocr_supported, ocr_page_text
        from core.llm import LLMClient

        doc = fitz.open(str(pdf))
        native_chars = sum(len(doc[i].get_text().strip()) for i in range(min(3, doc.page_count)))
        scanned = detect_scanned(doc)
        record("ocr", "识别为扫描件", scanned is True,
               f"前3页原生文本 {native_chars} 字符, detect_scanned={scanned}")

        # 先判 Key：ocr_supported 内部会因 Key 非 ASCII / 过短而返回 False，
        # 那属于「环境不具备」而非「代码缺陷」，应记 SKIP 而不是 FAIL。
        if not env["usable"]:
            record("ocr", "OCR 端到端", None,
                   f"Key 不可用（{env['reason']}），OCR 未实测", "")
            doc.close()
            return

        # 必须用 Container 构建好的客户端：LLMClient() 无参构造不会加载
        # Provider 配置（model/vl_model 全为 None），与 UI 真实路径不一致。
        llm = make_ctx().llm
        if not ocr_supported(llm):
            record("ocr", "OCR 端到端", False,
                   f"Key 合法但视觉模型不可用：ocr_supported=False（provider={env['provider']}）",
                   "该 Provider 无视觉模型；换 zhipu/qwen/openai 后可测")
            doc.close()
            return

        t0 = time.time()
        txt = ocr_page_text(doc[0], llm, zoom=2.0)
        dt = time.time() - t0
        ok = bool(txt) and len(txt.strip()) > 200
        record("ocr", "首页 OCR 输出文本", ok,
               f"{len(txt)} 字符 / {dt:.1f}s / 前80字：{txt.strip()[:80]!r}")
        doc.close()
    except Exception as e:
        record("ocr", "扫描件 OCR 端到端", False, f"异常：{type(e).__name__}: {e}",
               traceback.format_exc(limit=2)[:200])


# ======================================================== 用例组 2: 细粒度问答

# 占位符泄漏特征串：命中即说明模型偷懒没真生成（如曾实测出现的
# 「[1-40] (List all the references...)」和「[Placeholder]」）
PLACEHOLDER_PATTERNS = [
    "[placeholder]", "(list all", "list the references as per",
    "[1-40]", "[insert", "todo", "tbd", "xxx",
]


# 赛题原文点名的细粒度问题——评委现场最可能问的就是这些
FINEDRAINED_CASES = [
    {
        "q": "Adam 优化器的 β1 和 β2 分别设置为多少？",
        "expect": ["0.9", "0.98"],
        "pdf": "1706.03762v7.pdf",
    },
    {
        "q": "训练基础模型用了什么型号的 GPU、一共多少块？",
        "expect": ["P100", "8"],
        "pdf": "1706.03762v7.pdf",
    },
    {
        "q": "在 WMT 2014 英德和英法翻译任务上，BLEU 分数分别是多少？",
        "expect": ["28.4", "41.8"],
        "pdf": "1706.03762v7.pdf",
    },
]


def group_qa(env: dict, only: set, skip: set) -> None:
    section("用例组 B：单篇细粒度问答（赛题原文点名的问题 + 溯源锚点）")
    if "qa" in skip or (only and "qa" not in only):
        return
    if not env["usable"]:
        record("qa", "细粒度问答", None, f"Key 不可用（{env['reason']}），未实测", "")
        return

    try:
        from services.rag import answer
        if not (ROOT / "论文样本" / "1706.03762v7.pdf").exists():
            record("qa", "细粒度问答", None, "缺文件 1706.03762v7.pdf", "")
            return

        t0 = time.time()
        ctx = ensure_papers("1706.03762v7.pdf")
        n_chunks = len(getattr(ctx, "chunks", []))
        # 不能硬编码 True：入库失败会让后续所有问答退化成拒答，必须先卡住
        record("qa", "v7 入库", n_chunks > 0,
               f"{n_chunks} chunks / {time.time() - t0:.1f}s",
               "" if n_chunks else "入库后 chunks 为 0，后续问答结果不可信")

        for c in FINEDRAINED_CASES:
            try:
                t0 = time.time()
                res = answer(ctx, c["q"])
                ans = res.get("answer", "") if isinstance(res, dict) else str(res)
                srcs = res.get("sources", []) if isinstance(res, dict) else []
                hit = [e for e in c["expect"] if e in ans]
                miss = [e for e in c["expect"] if e not in ans]
                # 必须全部命中：只命中 1 个属于"答了一半"，对该问法不合格
                # （β1/β2 只答一个、BLEU 只给英德不给英法，评委照样算错）
                ok = len(hit) == len(c["expect"])
                evidence = (f"命中 {hit}"
                            f"{'（遗漏 ' + str(miss) + '）' if miss else ''}"
                            f" / 期望 {c['expect']} / {len(ans)}字 / {time.time() - t0:.1f}s / "
                            f"溯源 {len(srcs)} 条")
                record("qa", f"问答：{c['q'][:28]}…", ok, evidence,
                       "" if ok else f"遗漏期望值 {miss}——回答不完整或检索未命中")

                # 溯源锚点必须存在（赛题硬性要求）
                if srcs:
                    pg = [s.get("page") for s in srcs if s.get("page") is not None]
                    record("qa", f"  ↳ 溯源锚点（{c['q'][:16]}…）", len(pg) > 0,
                           f"{len(pg)}/{len(srcs)} 条带页码，页码={pg[:5]}")
                else:
                    record("qa", f"  ↳ 溯源锚点（{c['q'][:16]}…）", False,
                           "0 条来源——抗幻觉溯源失效")
            except Exception as e:
                record("qa", f"问答：{c['q'][:28]}…", False,
                       f"异常：{type(e).__name__}: {e}")
    except Exception as e:
        record("qa", "细粒度问答", False, f"异常：{type(e).__name__}: {e}",
               traceback.format_exc(limit=2)[:200])


# ==================================================== 用例组 3: 抗幻觉闸门

def group_guard(env: dict, only: set, skip: set) -> None:
    section("用例组 C：抗幻觉闸门（拒答 / 引用一致性 / References 白名单）")
    if "guard" in skip or (only and "guard" not in only):
        return

    # C1 引文一致性校验不需要 Key，纯本地逻辑，必测
    try:
        from core.guardrails import validate_citations
        good_chunk = "The Adam optimizer used beta1=0.9 and beta2=0.98 for training."
        bad_chunk = "We trained on 8 NVIDIA P100 GPUs for 100,000 steps."
        # 注意签名：validate_citations(answer, n_sources:int, source_texts=None)
        r_ok = validate_citations("β1=0.9、β2=0.98 [1]", 1, [good_chunk])
        r_bad = validate_citations("β1=0.9、β2=0.98 [1]", 1, [bad_chunk])
        ok_issues = (isinstance(r_ok, (list, tuple)) and len(r_ok) == 0)
        bad_issues = (isinstance(r_bad, (list, tuple)) and len(r_bad) > 0)
        record("guard", "引用内容一致性校验", bool(ok_issues and bad_issues),
               f"有依据→issues={len(r_ok) if isinstance(r_ok,(list,tuple)) else r_ok}；"
               f"无依据→issues={len(r_bad) if isinstance(r_bad,(list,tuple)) else r_bad}")
    except Exception as e:
        record("guard", "引用内容一致性校验", False, f"异常：{type(e).__name__}: {e}")

    if not env["usable"]:
        record("guard", "拒答闸门（真实 LLM）", None,
               f"Key 不可用（{env['reason']}），未实测", "")
        return

    # C2 拒答闸门：问一个文献库里绝不可能有的问题
    try:
        from services.rag import answer
        ctx = ensure_papers("1706.03762v7.pdf")
        res = answer(ctx, "这篇文章的作者在 2031 年获得了诺贝尔物理学奖吗？请给出获奖理由。")
        ans = res.get("answer", "") if isinstance(res, dict) else str(res)
        refused = any(k in ans for k in ["未找到", "没有找到", "无法", "不确定", "未在文献"])
        record("guard", "拒答闸门", refused,
               f"答案前120字：{ans.strip()[:120]!r}",
               "" if refused else "⚠️ 未拒答 → 可能产生了幻觉，这是抗幻觉最核心的防线")
    except Exception as e:
        record("guard", "拒答闸门", False, f"异常：{type(e).__name__}: {e}")


# ===================================================== 用例组 4: 多篇对比

def group_compare(env: dict, only: set, skip: set) -> None:
    section("用例组 D：多篇跨文献对比（Transformer vs BERT）")
    if "compare" in skip or (only and "compare" not in only):
        return
    if not env["usable"]:
        record("compare", "多篇跨文献对比", None, f"Key 不可用（{env['reason']}），未实测", "")
        return

    try:
        from services.rag import answer
        names = ["1706.03762v7.pdf", "1810.04805v2.pdf"]
        ctx = ensure_papers(*names)
        loaded = sum(1 for n in names if (ROOT / "论文样本" / n).exists())
        if loaded < 2:
            record("compare", "多篇跨文献对比", None, f"测试文件不足（仅 {loaded} 篇）", "")
            return

        q = "对比 Transformer 与 BERT 在架构上的承袭与差异，特别是 Encoder 部分的处理方式。"
        t0 = time.time()
        res = answer(ctx, q)
        ans = res.get("answer", "") if isinstance(res, dict) else str(res)
        # 只检查"提到了 BERT"太松：那可能只是顺带一提，并非真正的对比。
        # 真正的跨文献对比必须同时论及双方的特征实体。
        low = ans.lower()
        need_tf = ["transformer", "attention", "encoder"]
        need_bert = ["bert", "bidirectional", "masked", "pre-train", "pretrain"]
        hit_tf = [k for k in need_tf if k in low]
        hit_bert = [k for k in need_bert if k in low]
        ok = bool(hit_tf) and bool(hit_bert) and len(ans) > 200
        record("compare", "跨文献对比问答", ok,
               f"{len(ans)}字 / {time.time() - t0:.1f}s / "
               f"Transformer侧命中{hit_tf} / BERT侧命中{hit_bert}",
               "" if ok else f"未构成双向对比：Transformer侧{hit_tf}、BERT侧{hit_bert}")
    except Exception as e:
        record("compare", "多篇跨文献对比", False, f"异常：{type(e).__name__}: {e}")


# ==================================================== 用例组 5: 写作 Copilot

def group_write(env: dict, only: set, skip: set) -> None:
    section("用例组 E：学术写作 Copilot（无幻觉 References + Figure Caption）")
    if "write" in skip or (only and "write" not in only):
        return

    # E1 References 白名单校验是纯本地逻辑，不依赖 Key，必测
    try:
        from services.writing import validate_reference_whitelist
        allowed = [
            {"title": "Attention Is All You Need", "year": "2017"},
            {"title": "BERT: Pre-training of Deep Bidirectional Transformers", "year": "2019"},
        ]
        fake_text = ("...as shown in previous work [1][2][3].\n\nReferences\n"
                     "[1] Attention Is All You Need. 2017.\n"
                     "[2] BERT: Pre-training of Deep Bidirectional Transformers. 2019.\n"
                     "[3] Totally Fabricated Paper On Nonexistent Topic. 2025.")
        r = validate_reference_whitelist(fake_text, allowed)
        caught = (isinstance(r, dict) and (not r.get("ok", True) or r.get("hallucinated")))
        record("write", "References 白名单幻觉校验", bool(caught),
               f"ok={r.get('ok') if isinstance(r,dict) else r}, "
               f"hallucinated={r.get('hallucinated') if isinstance(r,dict) else '?'}")
    except Exception as e:
        record("write", "References 白名单幻觉校验", False, f"异常：{type(e).__name__}: {e}")

    # E2 Figure Caption：需要 Key 才能生成英文 caption，但 chart_facts 是本地的
    try:
        from services.writing import chart_facts
        import pandas as pd
        df = pd.DataFrame({"epoch": range(1, 11),
                           "acc": [0.60 + 0.03 * i for i in range(10)]})
        facts = chart_facts(df, x="epoch", y="acc")
        fstr = str(facts)
        # any(isdigit) 太松：随便一个页码都会命中。真正的抗幻觉要求是
        # caption 里的数字必须能在源数据里对得上，否则就是编造。
        real_vals = {"0.60", "0.87", "0.63", "0.60", "10"}
        matched = [v for v in real_vals if v in fstr]
        record("write", "Figure Caption 事实抽取", len(matched) >= 2,
               f"源数据真值命中 {matched}（要求≥2）/ facts 前120字：{fstr[:120]!r}",
               "" if len(matched) >= 2 else "facts 未包含可在 df 中核对的真实数值")
    except Exception as e:
        record("write", "Figure Caption 事实抽取", False, f"异常：{type(e).__name__}: {e}")

    if not env["usable"]:
        record("write", "论文框架生成（真实 LLM）", None,
               f"Key 不可用（{env['reason']}），未实测", "")
        return
    try:
        from services.writing import generate_outline_verified
        if not (ROOT / "论文样本" / "1706.03762v7.pdf").exists():
            record("write", "论文框架生成", None, "缺 1706.03762v7.pdf", "")
            return
        ctx = ensure_papers("1706.03762v7.pdf")
        t0 = time.time()
        out = generate_outline_verified(ctx, "Efficient attention for long documents")
        txt = out.get("text", "") if isinstance(out, dict) else str(out)
        dt = time.time() - t0

        # 只验长度会被占位符骗过：实测曾生成 4515 字但 References 是
        # 「[1-40] (List all the references...)」，长度断言照样 PASS。
        # 必须拆成三条实质断言：结构完整 / 无占位 / References 是真条目。
        low = txt.lower()
        has_ref_sec = ("references" in low) or ("参考文献" in txt)
        placeholders = [p for p in PLACEHOLDER_PATTERNS if p.lower() in low]
        # 真实条目形如 "[1] Jimmy Lei Ba, ... Layer normalization. arXiv..., 2016."
        import re as _re
        ref_items = _re.findall(r"^\s*\[\d+\]\s*\S{10,}", txt, flags=_re.M)

        record("write", "论文框架·章节完整性", has_ref_sec,
               f"{len(txt)}字 / {dt:.1f}s / 含 References 章节={has_ref_sec}",
               "" if has_ref_sec else "未生成 References 章节——赛题模块六硬性要求")
        record("write", "论文框架·无占位符残留", not placeholders,
               f"检出占位串 {placeholders}" if placeholders else "零占位串",
               "" if not placeholders else f"占位符泄漏 {placeholders}，现场会被一眼看穿")
        record("write", "论文框架·References 为真实条目", len(ref_items) >= 3,
               f"解析出 {len(ref_items)} 条真实文献条目（要求≥3）"
               + (f" / 示例：{ref_items[0][:90]}" if ref_items else ""),
               "" if len(ref_items) >= 3
               else "References 非真实条目（可能是占位指令），核心卖点『无幻觉 References』不成立")
    except Exception as e:
        record("write", "论文框架生成", False, f"异常：{type(e).__name__}: {e}")


# ======================================================= 用例组 6: 图谱

def group_graph(env: dict, only: set, skip: set) -> None:
    section("用例组 F：引用图谱（综述生成 / Future Work 判定）")
    if "graph" in skip or (only and "graph" not in only):
        return
    if not env["usable"]:
        record("graph", "图谱综述 + Future Work", None,
               f"Key 不可用（{env['reason']}），未实测", "")
        return
    try:
        from services.graph import build_graph, analyze, generate_review, explore_future_structured
        ctx = ensure_papers("1706.03762v7.pdf", "1810.04805v2.pdf")
        # 注意：build_graph 返回 (G, edges) 二元组
        G, edges = build_graph(ctx)
        m = analyze(G)
        n_nodes, n_edges = G.number_of_nodes(), G.number_of_edges()
        # 只断言 nodes>0 会被 2 节点图糊弄过去，而赛题要求的是「批量文献（如 20 篇）」。
        # 语料不足时如实标出规模，不冒充已验证大图。
        record("graph", "图谱构建", n_nodes >= 2 and n_edges >= 1,
               f"{n_nodes} 节点 / {n_edges} 边 / 簇数={len(m.get('communities', [])) if isinstance(m, dict) else '?'}",
               "" if n_nodes >= 3 else
               f"⚠️ 语料仅 {n_nodes} 个逻辑节点（论文样本/ 下 v1 与 v7 会被版本合并），"
               f"未达赛题『20 篇批量文献』规模，本条只证明链路通、不证明规模能力")

        rev = str(generate_review(ctx, G, m, None))
        import re as _re
        # 综述必须真的用上时间线/论文名，而不是「无数据无法描述」
        years_in_rev = _re.findall(r"(19|20)\d{2}", rev)
        degraded = ("无法" in rev and "时间线" in rev) or "没有具体的年份" in rev
        record("graph", "自动综述生成", len(rev) > 300 and bool(years_in_rev) and not degraded,
               f"{len(rev)}字 / 含年份 {sorted(set(years_in_rev))[:6]} / "
               f"退化为『无年份无法描述』={degraded} / 前60字：{rev.strip()[:60]!r}",
               "" if not degraded else "综述时间线退化——赛题要求『结合时间线』")

        fw = explore_future_structured(ctx, G)
        n_fw = len(fw) if isinstance(fw, list) else 0
        # n_fw >= 0 恒真，纯放水；改为要求真的抽出条目且带状态字段
        statuses = [f.get("status") for f in fw] if isinstance(fw, list) else []
        valid_status = [s for s in statuses if s in ("已解决", "开放", "存疑")]
        record("graph", "Future Work 结构化判定", n_fw > 0 and len(valid_status) == n_fw,
               f"抽出 {n_fw} 条 / 状态分布={statuses[:6]}"
               + (f" / 有效状态 {len(valid_status)}/{n_fw}" if n_fw else ""),
               "" if n_fw > 0 else "未抽出任何 Future Work 条目（可能为 0 条被判通过）")
    except Exception as e:
        record("graph", "图谱综述 + Future Work", False, f"异常：{type(e).__name__}: {e}")


# ================================================= 用例组 7: 外部 API

def group_extern(only: set, skip: set) -> None:
    section("用例组 G：外部 API（真实被引数 / arXiv）—— 不依赖 LLM Key，只依赖网络")
    if "extern" in skip or (only and "extern" not in only):
        return
    try:
        from services.citation_quality import fetch_citation_count
        r = fetch_citation_count("Attention Is All You Need")
        if not isinstance(r, dict):
            record("extern", "真实被引数（OpenAlex 链路）", False, f"返回非 dict：{r!r}")
            return
        cnt = r.get("cited_by_count")
        src = r.get("source")
        err = r.get("error_kind")
        unconfirmed = r.get("title_attribution_unconfirmed")

        # 限流属于外部环境限制，不是代码缺陷：此时应考核「系统是否诚实」
        if err == "rate_limited" or r.get("source_degraded"):
            honest = bool(unconfirmed or r.get("low_confidence"))
            record("extern", "被引数·限流下的诚实降级", honest,
                   f"source={src} / error_kind={err} / 匹配到『{r.get('matched_title')}』"
                   f" / title_attribution_unconfirmed={unconfirmed}"
                   f" / low_confidence={r.get('low_confidence')}",
                   "" if honest else "⚠️ 限流下未标记不可信 → 错误数值可能被直接展示")
            return

        ok = isinstance(cnt, int) and cnt > 1000 and not unconfirmed
        record("extern", "真实被引数（OpenAlex 链路）", ok,
               f"返回 {cnt} / source={src} / matched={r.get('matched_title')}",
               "" if ok else "真值应在数万量级，偏低说明选优仍有问题")
    except Exception as e:
        record("extern", "真实被引数", False, f"异常：{type(e).__name__}: {e}")


# ================================================================ 汇总输出

def summary() -> int:
    section("实测汇总")
    n_pass = sum(1 for r in ROWS if r["ok"] is True)
    n_fail = sum(1 for r in ROWS if r["ok"] is False)
    n_skip = sum(1 for r in ROWS if r["ok"] is None)
    print(f"{'组':<10}{'用例':<42}{'状态':<6}  证据")
    print("-" * 74)
    for r in ROWS:
        print(f"{r['group']:<10}{r['name'][:40]:<42}{r['status']:<6}  {r['evidence'][:60]}")
    print("-" * 74)
    print(f"PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}   （共 {len(ROWS)} 条）")

    fails = [r for r in ROWS if r["ok"] is False]
    if fails:
        print("\n❌ 失败项：")
        for r in fails:
            print(f"  - [{r['group']}] {r['name']}: {r['evidence'][:150]}")
    skips = [r for r in ROWS if r["ok"] is None]
    if skips:
        print("\n⏭️ 跳过项（需要真实 Key 或网络才能测）：")
        for r in skips:
            print(f"  - [{r['group']}] {r['name']}: {r['evidence'][:150]}")
    return n_fail


def main() -> int:
    args = sys.argv[1:]
    only, skip = set(), set()
    for i, a in enumerate(args):
        if a == "--only" and i + 1 < len(args):
            only = set(args[i + 1].split(","))
        elif a == "--skip" and i + 1 < len(args):
            skip = set(args[i + 1].split(","))

    env = load_env()
    section("环境自检")
    print(f"  Provider : {env['provider']}  (变量 {env['var']})")
    print(f"  Key      : 长度 {env['len']} / ASCII={env['ascii']}")
    print(f"  可用性   : {'✅ 可用' if env['usable'] else '❌ ' + env['reason']}")
    if not env["usable"]:
        print("\n  ⚠️  Key 不可用 —— 依赖 LLM 的用例将全部标为 SKIP（不会假装 PASS）。")
        print("     填入真实 Key 后重跑本脚本即可完成全部实测。")

    group_ocr(env, only, skip)
    group_qa(env, only, skip)
    group_guard(env, only, skip)
    group_compare(env, only, skip)
    group_write(env, only, skip)
    group_graph(env, only, skip)
    group_extern(only, skip)

    n_fail = summary()
    cleanup_tmp()
    print(f"\n🧹 已清理 {len(_TMP_DIRS)} 个临时目录，真实 storage/ 未被触碰。")
    return n_fail


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)
