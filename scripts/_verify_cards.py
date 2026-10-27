# -*- coding: utf-8 -*-
"""全站卡片头行门禁 · 真实浏览器 DOM（不是合成页，也不是 AppTest）

=============================================================================
为什么必须用真实浏览器（2026-10-27 的惨痛实测）
=============================================================================
卡片头行的选择器曾写成：

    [class*="st-key-card"] > [data-testid="stVerticalBlock"]
      > [data-testid="stElementContainer"]:first-child

多写了一级 `stVerticalBlock`，于是**从来没匹配上**（querySelectorAll 命中 0 个）。
肉眼却一直"看着有分隔线"——那其实是 `.ai-section` 的 48px 上边距在视觉上把
标题和下面的控件分开了。合成页门禁（_verify_theme.py）用自己拼的 DOM，也复现
不出这个假象；只有对着真实 Streamlit 1.63 渲染出的结构量 computed style 才看得见。

守三条（每条都必须真实命中，命中数 0 视为失明而不是通过）：
  1 头行真存在：每张 `st-key-cardNN` 的**直接**首个子元素 border-bottom = 1px
  2 卡内标题不重复留白：该子元素里的 `.ai-section` margin-top = 0px
  3 卡片没被漏掉：每页命中卡片数 ≥ 期望值（改造过的页不许退回裸区块）

反向对照（硬性纪律）：收尾把所有头行的 border-bottom 改成 none，再扫一遍，
必须翻红。不翻红 = 门禁读的不是它以为的那个元素。

用法
----
    python scripts/_verify_cards.py --port 8640            # 校验
    python scripts/_verify_cards.py --port 8640 --dump     # 顺带打印每张卡的实测值
前置条件：文献库非空（点「一键加载演示数据（6 篇）」）。版式阅读页在空库时
早退成空态卡片，本门禁会如实翻红——那是数据问题，不是卡片问题。

退出码：全过 0，任一失败 1
"""
import argparse
import sys
import time

from playwright.sync_api import sync_playwright

MAIN = '[data-testid="stMain"]'

PAGES = [
    ("智能总助手", 1),
    ("文献库管理", 5),          # card23 上传 / card24 元数据 / card25 结构 / card07 删除 / card03
    ("跨文献问答与溯源", 1),      # card22 输入区（card10 答案区只在有结果时出现）
    ("版式阅读与智能翻译", 11),   # card36 论文来源 + card26~card35（六个页签的控制区）
    ("论文检索与追踪", 4),        # card13/14/15/16/17 里当前页签可见的那几张
    ("引用图谱与综述", 2),        # card05 图谱 + card06 综述
    ("写作辅助与可视化", 4),      # card18 框架 / card19 CSV / card20 拓扑 / card21 翻译
]

# 在页面上下文中执行：逐张卡片读 computed style
PROBE = r"""
() => {
  const out = [];
  document.querySelectorAll('[data-testid="stVerticalBlock"][class*="st-key-card"]')
    .forEach(el => {
      const m = (el.className || '').match(/st-key-(card\d+)/);
      const key = m ? m[1] : '?';
      const first = el.firstElementChild;
      if (!first) { out.push({key, tag: '(无子元素)', border: 'none', secTop: null, sec: null}); return; }
      const cs = getComputedStyle(first);
      const sec = first.querySelector('.ai-section');
      const scs = sec ? getComputedStyle(sec) : null;
      out.push({
        key,
        tag: first.getAttribute('data-testid') || first.tagName,
        border: cs.borderBottomWidth + ' ' + cs.borderBottomStyle,
        secTop: scs ? scs.marginTop : null,
        sec: sec ? (sec.innerText || '').split('\n')[0].trim().slice(0, 20) : null,
      });
    });
  return out;
}
"""

# 反向对照：把头行边框抹掉，断言门禁会翻红
BREAK = r"""
() => {
  let n = 0;
  document.querySelectorAll('[data-testid="stVerticalBlock"][class*="st-key-card"]')
    .forEach(el => {
      const first = el.firstElementChild;
      if (!first) return;
      first.style.borderBottom = 'none';
      n++;
    });
  return n;
}
"""


def _scan(pg, dump=False):
    """返回 [(key, tag, border, secTop, sec)]，并只保留 stMain 里的卡片。"""
    rows = pg.evaluate(PROBE)
    if dump:
        for r in rows:
            print("    %-7s %-18s border=%-12s secMarginTop=%-6s %s"
                  % (r["key"], r["tag"], r["border"], r["secTop"], r["sec"] or "-"))
    return rows


def _judge(rows):
    """返回失败原因列表。空列表 = 全过。"""
    bad = []
    if not rows:
        return ["页面上 0 张卡片（选择器失明？）"]
    for r in rows:
        if r["tag"] != "stElementContainer":
            # 首块是列布局（stLayoutWrapper）⇒ 卡里没有 .ai-section ⇒ 没有卡头。
            # 这是结构缺陷（把 section_header 放进卡内首行即可），不是 CSS 能补的：
            # 放宽选择器会让「首块是按钮行」的卡在自己下面画一条分隔线，语义错误。
            bad.append("%s 首块是 %s 而非标题元素 —— 这张卡没有卡头"
                       % (r["key"], r["tag"]))
            continue
        if r["border"] != "1px solid":
            bad.append("%s 头行无下边框（实测 %r）" % (r["key"], r["border"]))
        if r["secTop"] is not None and r["secTop"] != "0px":
            bad.append("%s 卡内标题上边距未归零（实测 %r，应为 0px）" % (r["key"], r["secTop"]))
    return bad


def _nav(pg, label):
    labels = pg.locator('[data-testid="stSidebar"] label')
    for i in range(labels.count()):
        try:
            if labels.nth(i).inner_text().strip() == label:
                labels.nth(i).click()
                break
        except Exception:
            continue
    else:
        return False
    # 等渲染真正结束：只看「卡片数稳定」不够 —— 文献库页签的表格要一秒钟才画完，
    # 那段时间卡片数（4 张）本来就稳定，采样早了会误判「改造被回退」。
    # 双条件：Streamlit 的运行指示器消失 + 卡片数连续 6 次（3s）不变。
    last, stable = -1, 0
    for _ in range(60):
        time.sleep(0.5)
        running = pg.evaluate(
            "() => !!document.querySelector('[data-testid=\"stStatusWidget\"]')")
        n = pg.evaluate(
            "() => document.querySelectorAll("
            "'[data-testid=\"stVerticalBlock\"][class*=\"st-key-card\"]').length")
        if n == last and not running:
            stable += 1
            if stable >= 6:
                break
        else:
            last, stable = n, 0
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8640")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()

    fails = 0
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        pg.goto("http://127.0.0.1:%s" % a.port, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_selector('[data-testid="stSidebar"] label', state="attached", timeout=120000)
        time.sleep(2.0)

        for label, expect in PAGES:
            print("\n--- %s（期望 ≥%d 张）" % (label, expect))
            if not _nav(pg, label):
                print("  !! 侧栏找不到该页")
                fails += 1
                continue
            rows = _scan(pg, dump=a.dump)
            bad = _judge(rows)
            if len(rows) < expect:
                bad.append("卡片数 %d 少于期望 %d（改造被回退了？）" % (len(rows), expect))
            if bad:
                fails += 1
                print("  [FAIL] %d 项" % len(bad))
                for b in bad:
                    print("    - " + b)
            else:
                print("  [PASS] %d 张卡，头行全部 1px、卡内标题边距 0" % len(rows))

        # ---------------- 反向对照：抹掉边框必须翻红 ----------------
        print("\n--- 反向对照：抹掉头行边框，断言门禁翻红")
        n = pg.evaluate(BREAK)
        rows = _scan(pg)
        bad = _judge(rows)
        if n and bad:
            print("  [PASS] 已抹掉 %d 处边框，门禁翻红 %d 项（看得见）" % (n, len(bad)))
        else:
            fails += 1
            print("  [FAIL] 抹掉 %d 处边框后门禁仍通过 —— 门禁失明" % n)

        br.close()

    print("\n" + "=" * 78)
    print(("✅ 全部通过" if not fails else "❌ 失败项 %d" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
