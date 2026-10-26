# -*- coding: utf-8 -*-
"""中英同步滚动视图「悬停即跳动 / 内容消失」复现与门禁（2026-10-26 用户实测）。

现场症状
  用户把鼠标移到「中英同步滚动（按比例）· 悬停/点击段落联动」条附近（即某段落行）时，
  原本渲染的正文整块消失，只剩空白栏。

根因（两条，均在本脚本内被量化钉住）
  ① `#bar` 用 position:fixed 浮在正文之上：实测 bar=[8,34]、首段 top=24，
     顶部 10px 被永久盖住 —— 用户移到「这个位置」看到的就是浮条压字。
  ② 段落 / 句子的 mouseenter 里调 scrollIntoView，而两栏共用 `#wrap` 这一层
     滚动容器：滚动把光标下的段落换成另一段 → 再触发 mouseenter → 再滚动，
     来回抖动直到正文整块被推出视口。Playwright 原始日志可复现该循环
     （反复 "…intercepts pointer events" 并在 data-i=0/2/5 之间跳）。

测量方法（避开工具自身的自动滚动干扰）
  Playwright 的 locator.hover() 会先把元素滚进视野，会把「工具滚动」和
  「页面滚动」混为一谈。因此这里一律：JS 把目标滚到视口中部 → 记基线 →
  用 mouse.move() 走真实坐标 → 再量。位移只可能来自页面自身逻辑。

用法
  python scripts/_verify_bilingual_hover.py           # 断言模式，失败退出码 1
  python scripts/_verify_bilingual_hover.py --dump    # 只打印现场量测
"""

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from services.translate import bilingual_html

VIEW_W, VIEW_H = 1180, 760
HOVER_JUMP_PX = 2.0

# 真实量级：英文长、中文短（与 LLM 译文比例一致），14 段与用户截图同量级
_EN = ("The dominant sequence transduction models are based on complex recurrent or "
       "convolutional neural networks that include an encoder and a decoder. The best "
       "performing models also connect the encoder and decoder through an attention "
       "mechanism. We propose a new simple network architecture, the Transformer, based "
       "solely on attention mechanisms, dispensing with recurrence and convolutions entirely.")
_ZH = ("主流序列转导模型基于包含编码器与解码器的复杂循环或卷积神经网络。性能最好的模型还通过注意力机制"
       "连接编码器与解码器。我们提出一种新的简单网络架构 Transformer，它仅基于注意力机制，完全摒弃"
       "循环与卷积。")
PAIRS = [(_EN, _ZH)] * 14

# 第 1 页现场：整页都是「标题 / 单位 / 邮箱」一行一段，align_pairs 只能给出 1 对。
# 单句段落必须仍然双向联动（旧实现在这里退化成功夫全废：没有 .sn ⇒ 悬停无反应）。
SHORT_PAIRS = [
    ("Attention Is All You Need", "注意力即一切所需"),
    ("Google Brain avaswani@google.com", "谷歌大脑 avaswani@google.com"),
    ("Google Brain noam@google.com", "谷歌大脑 noam@google.com"),
    ("Niki Parmar* Google Research nikip@google.com", "尼基·帕马尔* 谷歌研究 nikip@google.com"),
]

# 2026-10-26 用户截图里那类脏译文（模型把术语表块当正文抄回来）
DIRTY_ZH = ("【术语表】翻译时必须采用以下首选译法，保持全文一致，不要改用其它译法：\n"
            "- attention → 注意力\n- encoder → 编码器\n\n注意力就是你所需要的一切。")

FAILS = []


def _ck(name, ok, detail=""):
    print(("  OK   " if ok else "  FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)
    return ok


_JS_PROBE = """() => {
  const wrap = document.getElementById('wrap');
  const l = document.getElementById('l'), r = document.getElementById('r');
  const bar = document.getElementById('bar');
  const rect = el => { const b = el.getBoundingClientRect();
    return {top: +b.top.toFixed(1), bottom: +b.bottom.toFixed(1)}; };
  const lp = l.querySelectorAll('p'), rp = r.querySelectorAll('p');
  return {
    scrollTop: +wrap.scrollTop.toFixed(1),
    scrollHeight: wrap.scrollHeight,
    clientHeight: wrap.clientHeight,
    lCount: lp.length, rCount: rp.length,
    lFirst: lp.length ? rect(lp[0]) : null,
    rFirst: rp.length ? rect(rp[0]) : null,
    lText: (l.innerText || '').length, rText: (r.innerText || '').length,
    bar: bar ? rect(bar) : null,
    viewH: window.innerHeight
  };
}"""


def _probe(pg):
    return pg.evaluate(_JS_PROBE)


def _center_on(pg, selector, nth=0):
    """把目标滚到视口中部（页面自身滚动），返回其中心坐标 —— 供 mouse.move 用。"""
    return pg.evaluate(
        """([sel, n]) => {
      const el = document.querySelectorAll(sel)[n];
      if (!el) return null;
      el.scrollIntoView({block:'center'});
      const b = el.getBoundingClientRect();
      return {x: b.left + b.width / 2, y: b.top + b.height / 2};
    }""",
        [selector, nth],
    )


def _hover_and_measure(pg, selector, nth, label):
    """滚到中部 → 基线 → 真实坐标悬停 → 再量。返回 (baseline, after)。"""
    pt = _center_on(pg, selector, nth)
    if not pt:
        return None, None
    pg.wait_for_timeout(120)
    base = _probe(pg)
    # 先落在外侧再移入，确保 mouseenter 真的派发一次
    pg.mouse.move(pt["x"] - 30, pt["y"])
    pg.mouse.move(pt["x"], pt["y"])
    pg.wait_for_timeout(280)
    return base, _probe(pg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--html", default="",
                    help="载入外部 HTML 文件而非现做（反向验证用：把 bug 注回去看门禁是否翻红）")
    args = ap.parse_args()

    from ui import design as _design

    if args.html:
        html = Path(args.html).read_text(encoding="utf-8")
    else:
        html = bilingual_html(PAIRS)
        # 生产路径一定会经 embed_html → design.inject_into_head 往 </head> 追加
        # 令牌样式（含 html,body{margin:0;padding:0}），它排在本文件样式之后，
        # 同权重属性会覆盖。探针必须先注入再量，否则测的不是用户看到的 DOM。
        html = _design.inject_into_head(html)
    tmp = Path(tempfile.gettempdir()) / "_bi_hover_probe.html"
    tmp.write_text(html, encoding="utf-8")
    print(f"物化 HTML -> {tmp}  ({len(html)} 字符)")

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": VIEW_W, "height": VIEW_H})
        pg.goto(tmp.as_uri(), wait_until="load")
        pg.wait_for_timeout(300)

        base = _probe(pg)
        print("① 初始：", base)
        _ck("两栏各 14 段", base["lCount"] == 14 and base["rCount"] == 14,
            f"l={base['lCount']} r={base['rCount']}")
        _ck("只有 #wrap 一层滚动", base["scrollHeight"] > base["clientHeight"],
            f"scrollHeight={base['scrollHeight']} clientHeight={base['clientHeight']}")

        # ⓪ 结构性契约（静态，不依赖偶然几何）：mouseenter 处理器里不得出现滚动调用。
        #    几何断言只在「对侧段落恰好越界」时触发，会漏掉大半场景；这条把
        #    「悬停不滚动」钉成不变量，任何把 scrollIntoView 塞回悬停路径的改动都会翻红。
        CHUNK = 400
        offenders = []
        start = 0
        while True:
            k = html.find("addEventListener('mouseenter'", start)
            if k < 0:
                break
            tail = html[k:k + CHUNK]
            # 处理器的边界取「下一个 addEventListener(」，不能用 ');' —— 处理器体里
            # highlight(idx); 的 ');' 会先把扫描截断，导致 scrollIntoView 逃过检查。
            nxt = tail.find("addEventListener(", 1)
            if nxt > 0:
                tail = tail[:nxt]
            if "scrollIntoView" in tail:
                offenders.append(tail[:70].replace("\n", " "))
            start = k + 1
        _ck("★ 结构性契约：悬停处理器内无 scrollIntoView", not offenders,
            f"命中 {len(offenders)} 处" + (f"：{offenders[0]}" if offenders else ""))
        _ck("仍保留点击跳转能力（页面内存在 scrollIntoView）",
            "scrollIntoView" in html)

        # ① 提示条不得压住正文（用户「移到这个位置」的那条）
        bar = base["bar"]
        if bar and base["lFirst"]:
            overlap = not (bar["bottom"] <= base["lFirst"]["top"]
                           or bar["top"] >= base["lFirst"]["bottom"])
            _ck("顶部提示条不遮挡第一段", not overlap,
                f"bar=[{bar['top']},{bar['bottom']}] lFirst.top={base['lFirst']['top']}")

        # ② 悬停段落：首段 / 中段 / 末段，左右两栏都要保持「只高亮、不滚动」
        for sel, label in (("#l p", "左栏"), ("#r p", "右栏")):
            for nth in (0, 9, 13):
                b0, b1 = _hover_and_measure(pg, sel, nth, label)
                if b0 is None:
                    _ck(f"定位 {label} 第 {nth} 段", False)
                    continue
                if args.dump:
                    print(f"   悬停{label}#{nth}: scrollTop {b0['scrollTop']} -> {b1['scrollTop']}")
                _ck(f"悬停{label}第 {nth} 段不滚动页面",
                    abs(b1["scrollTop"] - b0["scrollTop"]) <= HOVER_JUMP_PX,
                    f"scrollTop {b0['scrollTop']} -> {b1['scrollTop']}")
                _ck(f"悬停{label}第 {nth} 段后两栏内容仍在",
                    b1["lCount"] == 14 and b1["rCount"] == 14
                    and b1["lText"] > 200 and b1["rText"] > 100,
                    f"lCount={b1['lCount']} lText={b1['lText']} rText={b1['rText']}")
                _ck(f"悬停{label}第 {nth} 段已双向高亮",
                    pg.evaluate("() => document.querySelectorAll('p.active').length") == 2)

        # ③ 悬停句子（.sn）同样不得滚动。
        #    句级对齐不等长时（英 5 句 vs 中 3 句）对侧可能没有同 data-sidx 的句子，
        #    这是既有的优雅降级；门禁只对「两侧都存在」的句子断言双向高亮。
        pair_idx = pg.evaluate(
            """() => {
          const l = document.querySelectorAll('#l .sn');
          for (let i = 0; i < l.length; i++){
            const k = l[i].getAttribute('data-sidx');
            if (document.querySelector('#r .sn[data-sidx="' + k + '"]')) return +k;
          }
          return -1;
        }"""
        )
        _ck("存在两侧都有的句子（可测双向高亮）", pair_idx >= 0, f"sidx={pair_idx}")
        if pair_idx >= 0:
            pt = pg.evaluate(
                """(k) => {
              const el = document.querySelector('#l .sn[data-sidx="' + k + '"]');
              el.scrollIntoView({block:'center'});
              /* 跨行内联元素的联合包围盒中心常常落在某行末尾的空白里（命中外层 p
                 而不是 span），因此取第一个行盒（getClientRects()[0]）的中心。 */
              const rs = el.getClientRects();
              const b = rs.length ? rs[0] : el.getBoundingClientRect();
              return {x: b.left + Math.min(b.width / 2, 40), y: b.top + b.height / 2};
            }""",
                pair_idx,
            )
            pg.wait_for_timeout(120)
            b0 = _probe(pg)
            pg.mouse.move(pt["x"] - 20, pt["y"])
            pg.mouse.move(pt["x"], pt["y"])
            pg.wait_for_timeout(280)
            b1 = _probe(pg)
            if args.dump:
                print(f"   悬停句子 sidx={pair_idx}: scrollTop {b0['scrollTop']} -> {b1['scrollTop']}")
            _ck("悬停句子不滚动页面",
                abs(b1["scrollTop"] - b0["scrollTop"]) <= HOVER_JUMP_PX,
                f"scrollTop {b0['scrollTop']} -> {b1['scrollTop']}")
            _ck("悬停句子已双向高亮",
                pg.evaluate("() => document.querySelectorAll('.sn.on').length") >= 2,
                f"on={pg.evaluate('() => document.querySelectorAll(`.sn.on`).length')}")

        # ④ 悬停提示条本身
        if base["bar"]:
            pg.evaluate("() => { document.getElementById('wrap').scrollTop = 0; }")
            pg.wait_for_timeout(120)
            b0 = _probe(pg)
            pg.mouse.move(VIEW_W // 2, (bar["top"] + bar["bottom"]) / 2)
            pg.wait_for_timeout(280)
            b1 = _probe(pg)
            _ck("悬停提示条不改变页面",
                abs(b1["scrollTop"] - b0["scrollTop"]) <= HOVER_JUMP_PX
                and b1["lCount"] == 14 and b1["lText"] > 200,
                f"scrollTop={b1['scrollTop']} lText={b1['lText']}")

        # ⑤ 点击仍要能跳转（联动不能因为修 bug 被砍掉）
        pg.evaluate("() => { document.getElementById('wrap').scrollTop = 0; }")
        pg.wait_for_timeout(120)
        pt = _center_on(pg, "#l p", 11)
        pg.mouse.click(pt["x"], pt["y"])
        pg.wait_for_timeout(400)
        after_click = _probe(pg)
        _ck("点击段落把对侧同段滚入视野", after_click["scrollTop"] > 0,
            f"scrollTop={after_click['scrollTop']}")

        # ⑥ 短行/单句段落（第 1 页现场：标题、单位、邮箱，一行一段）也必须双向联动。
        #    这类段落 align_pairs 只能给出 1 对，旧实现直接返回裸文本 ⇒ 一个 .sn 都没有
        #    ⇒ 悬停英文右栏毫无反应（用户 2026-10-26 原话「右边没有对应合适的反应」）。
        short_html = _design.inject_into_head(bilingual_html(SHORT_PAIRS))
        tmp2 = Path(tempfile.gettempdir()) / "_bi_hover_probe_short.html"
        tmp2.write_text(short_html, encoding="utf-8")
        pg2 = br.new_page(viewport={"width": VIEW_W, "height": VIEW_H})
        pg2.goto(tmp2.as_uri(), wait_until="load")
        pg2.wait_for_timeout(300)
        n_sn = pg2.evaluate(
            "() => [document.querySelectorAll('#l .sn').length,"
            "       document.querySelectorAll('#r .sn').length]")
        _ck("单句段落两侧都有句单元（.sn）", n_sn[0] == len(SHORT_PAIRS) and n_sn[1] == len(SHORT_PAIRS),
            f"l={n_sn[0]} r={n_sn[1]} 期望={len(SHORT_PAIRS)}")
        if n_sn[0] == len(SHORT_PAIRS):
            # 必须悬在**句单元**上而不是 <p> 的几何中心：短行的 <p> 撑满整栏宽度，
            # 中心点落在一行文字右侧的空白里，命中的是 <p> 而不是 .sn（实测 .sn.on=0）。
            pt = pg2.evaluate(
                """() => {
              const el = document.querySelectorAll('#l .sn')[1];
              el.scrollIntoView({block:'center'});
              const rs = el.getClientRects();
              const b = rs.length ? rs[0] : el.getBoundingClientRect();
              return {x: b.left + Math.min(b.width / 2, 30), y: b.top + b.height / 2};
            }""")
            pg2.mouse.move(pt["x"] - 20, pt["y"])
            pg2.mouse.move(pt["x"], pt["y"])
            pg2.wait_for_timeout(320)
            _ck("悬停短行英文→右栏同段高亮（p.active 各一）",
                pg2.evaluate("() => document.querySelectorAll('p.active').length") == 2)
            _ck("悬停短行英文→对侧句单元同时高亮（.sn.on）",
                pg2.evaluate("() => document.querySelectorAll('.sn.on').length") >= 2,
                f"on={pg2.evaluate('() => document.querySelectorAll(`.sn.on`).length')}")
            # ⑦ 高亮必须**看得见**：8% 透明度的令牌在 760px iframe 里等于没反应，
            #    这里直接量计算样式的 alpha，钉住「悬停反馈可见」这条交互契约。
            bg = pg2.evaluate(
                "() => { const e = document.querySelector('.sn.on');"
                "        return e ? getComputedStyle(e).backgroundColor : ''; }")
            alpha = 0.0
            if "rgba" in bg:
                try:
                    alpha = float(bg.rsplit(",", 1)[1].strip().rstrip(")"))
                except Exception:
                    alpha = 0.0
            _ck("句级高亮底色可见（alpha ≥ 0.10）", alpha >= 0.10, f"background={bg or '(空)'}")
            # ⑧ 污染译文不得上屏（渲染层兜底清洗）
            dirty_html = bilingual_html([("Attention is all you need.", DIRTY_ZH)])
            _ck("回显术语表块的脏译文已被清洗", "【术语表】" not in dirty_html
                and "注意力就是你所需要的一切" in dirty_html)
        pg2.close()

        br.close()

    print()
    if args.dump:
        print("（--dump 模式，不做结论）")
        return 0
    if FAILS:
        print(f"FAIL {len(FAILS)} 项未通过：" + " / ".join(FAILS))
        return 1
    print("OK 全部通过：悬停只高亮不滚动、提示条不遮挡正文、点击仍可跳转")
    return 0


if __name__ == "__main__":
    sys.exit(main())
