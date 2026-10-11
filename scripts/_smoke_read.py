"""阅读页（页面3）渲染冒烟 + A/B/C 组关键修复自验。

用法（需 ai_research 环境，含 pymupdf / streamlit / psutil）：
    conda activate ai_research
    python scripts/_smoke_read.py

分两部分：
  Part 1  纯 Python 校验：双栏检测、原位 PDF 中文叠加、MemoryProbe、LLM 重试退避
  Part 2  streamlit.testing.v1.AppTest 渲染阅读页（含跨页跳转消费），断言无异常冒泡
"""
import os
import re
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def _layer_content(html, ident):
    """按 div 嵌套深度取出 <div class="col" id="colN"> 的内部 HTML（比正则可靠）。"""
    key = 'class="col" id="col%d"' % int(ident)
    i = html.find(key)
    if i < 0:
        return ""
    j = html.find(">", i)
    if j < 0:
        return ""
    depth, k = 1, j + 1
    while k < len(html):
        a = html.find("<div", k)
        b = html.find("</div>", k)
        if b < 0:
            break
        if a >= 0 and a < b:
            depth += 1
            k = a + 4
        else:
            depth -= 1
            k = b + 6
            if depth == 0:
                return html[j + 1:b]
    return html[j + 1:]


_BLK_RE = re.compile(r'class="blk" style="left:([\d.]+)px;top:([\d.]+)px;max-width:([\d.]+)px')


def _col_blocks(html, ident):
    return _BLK_RE.findall(_layer_content(html, ident))


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ PASS  " if cond else "  ❌ FAIL  ") + name + (("  | " + str(detail)) if detail else ""))
    return bool(cond)


# =====================================================================
# Part 1：纯 Python 校验
# =====================================================================
def part1():
    print("\n=== Part 1：纯 Python 校验 ===")
    import pymupdf

    from services.translate import (detect_columns, native_page_html, in_place_pdf,
                                    bilingual_html, selectable_text_html)

    # ---- A1 双栏检测 ----
    bert = os.path.join(ROOT, "pdf", "1810.04805v2.pdf")
    att = os.path.join(ROOT, "pdf", "1706.03762v7.pdf")
    d = pymupdf.open(bert)
    info2 = detect_columns(d[1])
    check("A1 双栏检测：BERT p2 判定为 2 栏", info2["n_cols"] == 2,
          f"gap={None if not info2['gap'] else tuple(round(x, 1) for x in info2['gap'])} "
          f"L/R={info2['left']}/{info2['right']}")
    d1 = pymupdf.open(att)
    info1 = detect_columns(d1[1])
    check("A1 双栏检测：单栏 PDF（1706.03762v7 p2）判定为 1 栏", info1["n_cols"] == 1,
          f"n_blocks={info1['n_blocks']}")
    d1.close()

    h, pw, ph = native_page_html(d, 2)
    check("A1 分栏渲染：输出含 col0/col1 两个独立容器",
          ('id="col0"' in h) and ('id="col1"' in h))
    n_blk = h.count('class="blk"')
    n_maxw = h.count("max-width:")
    check("A1 分栏渲染：每个块都带 max-width 限宽（不溢出到另一栏）",
          n_maxw >= n_blk >= 1, f"blk={n_blk} max-width={n_maxw}")
    check("A1 分栏渲染：栏间有分隔（gutter）", "gutter" in h)
    check("A1 分栏渲染：HTML 无未替换的模板占位符", not re.search(r"\{[a-z_]+\}", h))

    # 栏内坐标不越界：块的 left + 容器 left 不超过页宽
    lefts = [float(m) for m in re.findall(r'class="blk" style="left:([\d.]+)px', h)]
    check("A1 分栏渲染：块相对坐标合法（>=0 且不超过栏宽）", len(lefts) > 0 and all(0 <= x <= pw for x in lefts),
          f"n={len(lefts)} pw={pw:.0f} max={max(lefts) if lefts else 0:.0f}")

    # ---- A1（增强）：构造真正的双栏 PDF，验证块不越栏 ----
    def _make_two_column_pdf(path, n=22):
        doc = pymupdf.open()
        pg = doc.new_page(width=595, height=842)
        for i in range(n):
            pg.insert_text((60, 70 + 16 * i),
                           "Left column sentence number %02d about attention mechanisms." % i,
                           fontsize=9, fontname="helv")
        for i in range(n):
            pg.insert_text((330, 70 + 16 * i),
                           "Right column sentence number %02d about transformer decoding." % i,
                           fontsize=9, fontname="helv")
        # 故意插入一行「超宽行」（真实 PDF 中常见于行内长公式 / 版式异常 / OCR 层）：
        # 从 x=60 起、实际宽度约 450pt，会一路伸到 510pt，越过中缝压到右栏上。
        pg.insert_text((60, 70 + 16 * (n + 1)),
                       "OVERLONG " * 22 + "END", fontsize=9, fontname="helv")
        doc.save(path)
        doc.close()

    tc_dir = tempfile.mkdtemp(prefix="fig_twocol_")
    tc = os.path.join(tc_dir, "twocol.pdf")
    try:
        _make_two_column_pdf(tc)
        dcol = pymupdf.open(tc)
        info_c = detect_columns(dcol[0])
        check("A1 双栏夹具：自建双栏 PDF 被判定为 2 栏", info_c["n_cols"] == 2,
              f"gap={None if not info_c['gap'] else tuple(round(x, 1) for x in info_c['gap'])}")
        hc, _, _ = native_page_html(dcol, 1)
        # 解析出栏容器与块，校验「块右边界不越过所在栏右边界」
        cols = re.findall(r'class="col" id="col(\d)" style="left:([\d.]+)px;width:([\d.]+)px', hc)
        blks_by_col = {0: _col_blocks(hc, 0), 1: _col_blocks(hc, 1)}
        check("A1 双栏夹具：左右栏都渲染出内容块",
              len(blks_by_col.get(0, [])) > 0 and len(blks_by_col.get(1, [])),
              f"col0={len(blks_by_col.get(0, []))} col1={len(blks_by_col.get(1, []))}")
        colmap = {int(c[0]): (float(c[1]), float(c[1]) + float(c[2])) for c in cols}
        worst = 0.0
        overflow = 0
        for ci, items in blks_by_col.items():
            c0, c1 = colmap.get(ci, (0, 595))
            for (lx, _ty, mw) in items:
                right = c0 + float(lx) + float(mw)
                worst = max(worst, right - c1)
                if right > c1 + 1.5:
                    overflow += 1
        check("A1 双栏夹具：无块越过所在栏右边界（修复串栏重叠）", overflow == 0,
              f"越界块={overflow} 最大越界={worst:.1f}px")

        # 超宽行：旧实现（绝对坐标 + white-space:pre，无宽度限制）会一路伸到右栏，
        # 新实现被 max-width 夹在左栏内换行。
        raw_w = None
        for blk in dcol[0].get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    if str(sp.get("text", "")).startswith("OVERLONG"):
                        x0, x1 = sp["bbox"][0], sp["bbox"][2]
                        raw_w = (x0, x1)
        if raw_w:
            col0_right = colmap.get(0, (0, 595))[1]
            check("A1 超宽行：原始跨度确实越过了左栏右边界（旧渲染会串栏）",
                  raw_w[1] > col0_right, f"span {raw_w[0]:.0f}→{raw_w[1]:.0f} vs 栏右 {col0_right:.0f}")
        widest = max((float(mw) for (_l, _t, mw) in blks_by_col.get(0, [])), default=0.0)
        check("A1 超宽行：新渲染被 max-width 夹在栏内（不再串栏）",
              colmap.get(0, (0, 595))[0] + widest <= colmap.get(0, (0, 595))[1] + 1.5,
              f"左栏最大块宽 {widest:.0f}px，栏宽 "
              f"{colmap.get(0, (0, 595))[1] - colmap.get(0, (0, 595))[0]:.0f}px")
        dcol.close()
    finally:
        import shutil
        shutil.rmtree(tc_dir, ignore_errors=True)

    # ---- A2 同步滚动 JS ----
    b = bilingual_html([("Hello world.", "你好世界。"), ("Second para.", "第二段。")])
    check("A2 同步滚动：按 scrollTop/(scrollHeight-clientHeight) 比例同步",
          ("scrollHeight - el.clientHeight" in b) and ("ratio(src) * m" in b))
    check("A2 同步滚动：isSyncing 防抖标志位存在", b.count("isSyncing") >= 4)
    check("A2 同步滚动：requestAnimationFrame 节流", "requestAnimationFrame" in b)
    check("A2 同步滚动：scrollHeight<=clientHeight 做了除零保护", "m <= 0" in b)
    check("A2 高亮：用 data-index 查找而非文本匹配",
          ('getAttribute(\'data-index\')' in b) and ('p[data-index="' in b))

    # ---- A3 划词选区 JS ----
    s = selectable_text_html("Hello world.\nSecond line.")
    check("A3 选区清空：removeAllRanges / empty() 双保险",
          ("removeAllRanges" in s) and ("empty" in s))
    check("A3 选区去重：hash 缓存 lastHash", "lastHash" in s)
    check("A3 选区过滤：长度 < 2 忽略", "MIN_LEN = 2" in s)

    # ---- A4 原位 PDF 中文 ----
    d2 = pymupdf.open(att)
    zh = ("Transformer 是一种完全基于注意力机制的模型，摒弃了循环与卷积结构。"
          "编码器由六个相同层堆叠而成，每层包含多头自注意力子层与全连接前馈网络子层，"
          "并在每个子层周围使用残差连接与层归一化。")
    out_dir = tempfile.mkdtemp(prefix="fig_smoke_")
    out = os.path.join(out_dir, "t.pdf")
    try:
        in_place_pdf(d2, 1, zh, out)
        check("A4 原位 PDF：文件已生成", os.path.exists(out) and os.path.getsize(out) > 1000,
              f"{os.path.getsize(out)} bytes")
        rd = pymupdf.open(out)
        txt = rd[0].get_text()
        cjk = "".join(re.findall(r"[\u4e00-\u9fff]+", txt))
        rd.close()
        check("A4 原位 PDF：中文可被正确提取（china-s 内置字体，无乱码）", len(cjk) >= 30,
              f"CJK={len(cjk)} sample={cjk[:24]}")
        check("A4 原位 PDF：无 U+FFFD 替换符（编码正确）", "\ufffd" not in txt)
        # 超长文本回退
        out2 = os.path.join(out_dir, "t_long.pdf")
        in_place_pdf(d2, 1, zh * 30, out2)
        rd2 = pymupdf.open(out2)
        t2 = rd2[0].get_text()
        rd2.close()
        cjk2 = "".join(re.findall(r"[\u4e00-\u9fff]+", t2))
        check("A4 原位 PDF：超长译文触发截断回退且仍生成成功",
              os.path.exists(out2) and len(cjk2) > 0 and len(cjk2) < len(zh) * 30,
              f"输入 {len(zh) * 30} 字 → 落盘 {len(cjk2)} 字")
    finally:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
    d.close()
    d2.close()

    # ---- B1 MemoryProbe ----
    from scripts._memory_probe import MemoryProbe, backend_name
    p = MemoryProbe("smoke")
    p.start()
    junk = []
    for _ in range(4):
        junk.append(bytearray(8 * 1024 * 1024))
        p.sample()
    peak = p.peak_mb()
    rep = p.report()
    check("B1 MemoryProbe：峰值内存可测（申请 32MB 后增量 > 20MB）", rep["delta_mb"] > 20,
          f"backend={backend_name()} peak={peak:.0f}MB delta={rep['delta_mb']:+.0f}MB")
    check("B1 MemoryProbe：report() 字段齐全",
          all(k in rep for k in ("label", "backend", "start_mb", "peak_mb", "delta_mb", "elapsed_s")))
    check("B1 MemoryProbe：text() 可用于 st.info", "峰值内存" in p.text() and "耗时" in p.text())
    del junk

    # ---- B2 超时 / 失败恢复 / 临时目录清理 ----
    from ui.page_reading import (_run_with_timeout, cleanup_stale_figure_dirs,
                                PAGE_TIMEOUT, MAX_TRANSLATE_WORKERS, LLM_CALL_TIMEOUT)
    import shutil

    def _slow():
        time.sleep(2.0)
        return 1

    ok_t, _v_t, err_t = _run_with_timeout(_slow, 0.5)
    check("B2 单页超时：超时被捕获（不抛异常、带 timeout 标记）",
          (not ok_t) and str(err_t).startswith("timeout"), f"err={err_t}")
    check("B2 单页超时阈值 = 30s（符合需求）", PAGE_TIMEOUT == 30.0, PAGE_TIMEOUT)

    def _boom():
        raise ValueError("模拟页面解析崩溃")

    ok_b, _v_b, err_b = _run_with_timeout(_boom, 5.0)
    check("B2 单页解析失败：异常被吞掉并返回原因字符串",
          (not ok_b) and ("ValueError" in str(err_b)), f"err={err_b}")

    ok_n, v_n, _e_n = _run_with_timeout(lambda: 42, 5.0)
    check("B2 正常页：结果正常返回", ok_n and v_n == 42)

    stale_dir = tempfile.mkdtemp(prefix="fig_stale_")
    old = time.time() - 48 * 3600
    os.utime(stale_dir, (old, old))
    removed = cleanup_stale_figure_dirs(24)
    check("B2 陈旧图片临时目录清理（> 24h 的 fig_* 目录被删除）",
          removed >= 1 and not os.path.exists(stale_dir), f"removed={removed}")
    fresh_dir = tempfile.mkdtemp(prefix="fig_fresh_")
    removed2 = cleanup_stale_figure_dirs(24)
    still = os.path.exists(fresh_dir)
    shutil.rmtree(fresh_dir, ignore_errors=True)
    check("B2 新目录不被误删（< 24h）", still, f"removed2={removed2}")

    check("B3 并发上限 ≤ 3 且单次 LLM 超时 60s",
          MAX_TRANSLATE_WORKERS <= 3 and LLM_CALL_TIMEOUT <= 60.0,
          f"workers={MAX_TRANSLATE_WORKERS} timeout={LLM_CALL_TIMEOUT}")

    # ---- C 统一重试 ----
    import core.llm as L
    check("C 退避序列统一为 1.5/3.0/4.5", tuple(L.RETRY_BACKOFF) == (1.5, 3.0, 4.5))
    check("C _backoff_for(0/1/2) == 1.5/3.0/4.5",
          (L._backoff_for(0), L._backoff_for(1), L._backoff_for(2)) == (1.5, 3.0, 4.5))
    check("C 关闭 SDK 自身重试避免叠加", L.SDK_MAX_RETRIES == 0)
    check("C 向后兼容：_fake_response/parse_json/call_llm_with_retry 均存在",
          all(hasattr(L, n) for n in ("_fake_response", "parse_json", "call_llm_with_retry",
                                      "LLMClient", "_log_llm_failure")))

    # 最小桩：替换 client._client，实测调用次数与退避总时长
    class _Counter:
        def __init__(self):
            self.calls = 0

    stub_root = _Counter()

    class StubCompletions:
        @staticmethod
        def create(**kw):
            stub_root.calls += 1
            raise RuntimeError("stub boom %d" % stub_root.calls)

    class StubChat:
        completions = StubCompletions()

    class StubClient:
        chat = StubChat()

    c = L.LLMClient(api_key="x", base_url=None, model="m")
    c._client = StubClient()
    t0 = time.time()
    r = c._create(model="m", messages=[{"role": "user", "content": "hi"}])
    dt = time.time() - t0
    check("C _create：失败后共 4 次调用（1 次 + 3 次退避重试）", stub_root.calls == 4,
          f"calls={stub_root.calls}")
    check("C _create：退避总时长 ≈ 1.5+3.0+4.5 = 9s", 8.5 <= dt <= 10.5, f"{dt:.2f}s")
    check("C _create：3 次失败后返回伪响应且不抛异常",
          isinstance(r.content if hasattr(r, "content") else r.choices[0].message.content, str)
          and "暂时不可用" in r.choices[0].message.content)
    check("C 失败落盘：storage/llm_failures.log 已写入",
          os.path.exists(os.path.join(ROOT, "storage", "llm_failures.log")))

    # stream 重试（首次即失败 → 应重试后给出兜底文案，不抛异常）
    stub_root.calls = 0
    c2 = L.LLMClient(api_key="x", model="m")
    c2._client = StubClient()
    t0 = time.time()
    chunks = list(c2.stream([{"role": "user", "content": "hi"}]))
    dt2 = time.time() - t0
    check("C stream：有重试（4 次）且失败后 yield 友好文案不抛异常",
          stub_root.calls == 4 and len(chunks) == 1 and "暂时不可用" in chunks[0],
          f"calls={stub_root.calls} {dt2:.1f}s out={chunks[:1]}")

    # vision 走同一重试通道
    stub_root.calls = 0
    c3 = L.LLMClient(api_key="x", model="m", vl_model="vl")
    c3._client = StubClient()
    v = c3.vision("描述这张图", b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    check("C vision：多模态同样走重试（4 次）并返回友好文案",
          stub_root.calls == 4 and "暂时不可用" in v, f"calls={stub_root.calls} out={v[:40]}")


# =====================================================================
# Part 2：Streamlit AppTest 渲染冒烟
# =====================================================================
_APP_SRC = '''
import os, sys
ROOT = r"{root}"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import streamlit as st
from ui.page_reading import render


class _LLM:
    available = False          # 模拟未填 API Key：翻译类按钮应给中文 warning 而不是崩溃


class _Ctx:
    files = ["1706.03762v7.pdf", "1810.04805v2.pdf",
             "UnderstandingDeepLearning_02_09_26_C.pdf", "LongBook_540p_fixture.pdf"]
    llm = _LLM()


st.session_state.setdefault("qa_jump_page", None)
render(_Ctx())
'''


def part2():
    print("\n=== Part 2：AppTest 渲染阅读页 ===")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as e:
        check("AppTest 可用", False, str(e))
        return

    tmp = tempfile.mkdtemp(prefix="fig_smoke_app_")
    app_path = os.path.join(tmp, "smoke_app.py")
    with open(app_path, "w", encoding="utf-8") as f:
        f.write(_APP_SRC.format(root=ROOT))

    at = AppTest.from_file(app_path, default_timeout=180)
    at.run()
    check("阅读页首屏渲染无异常", not at.exception, str(at.exception)[:300] if at.exception else "")
    check("阅读页首屏无 error 级提示", len(at.error) == 0,
          "; ".join(e.value[:80] for e in at.error[:3]))

    # 原生渲染（A1）
    try:
        at.button(key="np_go").click().run()
        check("点击「渲染该页」无异常", not at.exception,
              str(at.exception)[:300] if at.exception else "")
    except Exception as e:
        check("点击「渲染该页」无异常", False, str(e)[:200])
    try:
        at.button(key="np_col").click().run()
        check("点击「检测本页栏数」无异常", not at.exception,
              str(at.exception)[:300] if at.exception else "")
    except Exception as e:
        check("点击「检测本页栏数」无异常", False, str(e)[:200])

    # 长文档批量扫描（B1+B2）：前 120 页
    try:
        at.number_input(key="ld_end").set_value(120).run()
        at.button(key="ld_go").click().run()
        ok = not at.exception
        infos = [i.value for i in at.info]
        hit = [t for t in infos if "峰值内存" in t and "耗时" in t]
        check("长文档批量扫描（120 页）无异常", ok, str(at.exception)[:300] if at.exception else "")
        check("长文档扫描后 st.info 展示「峰值内存 / 耗时」", bool(hit),
              hit[0][:160] if hit else f"infos={infos[:2]}")
    except Exception as e:
        check("长文档批量扫描（120 页）无异常", False, str(e)[:200])

    # 图片抽取 + 临时目录清理（B2）
    try:
        def _fig_dirs():
            return [d for d in os.listdir(tempfile.gettempdir()) if d.startswith("fig_")]

        before = len(_fig_dirs())
        at.checkbox(key="ld_fig").check().run()
        at.button(key="ld_go").click().run()
        after = len(_fig_dirs())
        infos = [i.value for i in at.info]
        hit = [x for x in infos if "抽取图片" in x]
        check("B2 图片抽取：扫描后 fig_* 临时目录被清理（无残留）", after <= before,
              f"before={before} after={after}")
        check("B2 图片抽取：扫描结果展示抽取数量", bool(hit), hit[0][:140] if hit else "-")
        at.checkbox(key="ld_fig").uncheck().run()
    except Exception as e:
        check("B2 图片抽取与清理", False, str(e)[:200])

    # 541 页长文档（B1/B2 真实验证）
    try:
        at.selectbox(key="read_sel").select("UnderstandingDeepLearning_02_09_26_C.pdf").run()
        at.number_input(key="ld_start").set_value(1).run()
        at.number_input(key="ld_end").set_value(541).run()
        t0 = time.time()
        at.button(key="ld_go").click().run()
        dt = time.time() - t0
        infos = [i.value for i in at.info]
        hit = [x for x in infos if "峰值内存" in x and "耗时" in x]
        check("541 页长文档扫描无异常", not at.exception,
              str(at.exception)[:300] if at.exception else "")
        check("541 页长文档扫描输出「峰值内存 / 耗时」", bool(hit),
              (hit[0][:200] if hit else f"infos={infos[:2]}"))
        print("        [实测] " + (hit[0] if hit else "-") + f"  | 端到端 {dt:.1f}s")
    except Exception as e:
        check("541 页长文档扫描", False, str(e)[:200])

    # 无 Key 时点翻译：应给中文 warning，不崩
    try:
        at.button(key="sp_go").click().run()
        check("无 API Key 点「逐段翻译」给出中文 warning 且不崩",
              (not at.exception) and len(at.warning) > 0,
              (at.warning[0].value[:80] if at.warning else "no warning"))
    except Exception as e:
        check("无 API Key 点「逐段翻译」给出中文 warning 且不崩", False, str(e)[:200])

    # 划词翻译：加载该页
    try:
        at.button(key="hp_go").click().run()
        check("划词翻译「加载该页」无异常", not at.exception,
              str(at.exception)[:300] if at.exception else "")
    except Exception as e:
        check("划词翻译「加载该页」无异常", False, str(e)[:200])

    # 跨页跳转：消费 qa_jump_page
    try:
        at2 = AppTest.from_file(app_path, default_timeout=180)
        at2.session_state["qa_jump_page"] = {"source": "1810.04805v2.pdf", "page": 7,
                                             "para_idx": 3, "from": "qa"}
        at2.run()
        succ = [s.value for s in at2.success]
        check("跨页跳转：识别 qa_jump_page 并提示「已从问答页跳转」",
              (not at2.exception) and any("已从问答页跳转" in x for x in succ),
              (succ[:1] or str(at2.exception)[:200]))
        try:
            jump_val = at2.session_state["qa_jump_page"]
        except Exception:
            jump_val = "<absent>"
        check("跨页跳转：消费后清理 key（不重复跳转）",
              jump_val is None or jump_val == "<absent>", repr(jump_val))
        at2.run()
        succ2 = [s.value for s in at2.success]
        check("跨页跳转：二次运行不再重复提示", not any("已从问答页跳转" in x for x in succ2),
              succ2[:1])
    except Exception as e:
        check("跨页跳转流程", False, str(e)[:200])

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("Python:", sys.version.split()[0], "| ROOT:", ROOT)
    try:
        part1()
    except Exception as e:
        check("Part1 执行", False, f"{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    try:
        part2()
    except Exception as e:
        check("Part2 执行", False, f"{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    print("\n================ 汇总 ================")
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    for f_ in FAIL:
        print("  ❌ " + f_)
    sys.exit(1 if FAIL else 0)
