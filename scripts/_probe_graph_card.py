"""图谱页探针：点「构建引用图谱」后逐秒观测 card05 的 DOM 与截图。

动机：成片图谱段（88–112s）里，旁白说「把论文之间的引用关系画成图」，但画面
停在「尚未构建图谱」；构建完成横幅弹出后，card05（构建按钮 + 图谱 + KPI）竟整块
消失。先弄清是产品缺陷还是录屏时序问题，再决定要不要补录。

用法：python scripts/_probe_graph_card.py --port 8631
"""
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "资料/_构建/usage/probe"
MAIN = '[data-testid="stMain"]'

PROBE = """(() => {
  const main = document.querySelector('[data-testid="stMain"]');
  const txt = main ? main.innerText : '';
  const cards = [...document.querySelectorAll('[data-testid="stElementContainer"]')]
    .filter(e => e.querySelector('iframe'));
  // 找所有带 border 的容器（卡片）
  const bordered = [...document.querySelectorAll('div')]
    .filter(d => { const s = getComputedStyle(d);
      return s.borderTopWidth !== '0px' && s.borderTopStyle === 'solid'
             && d.getBoundingClientRect().height > 40; })
    .map(d => ({ h: Math.round(d.getBoundingClientRect().height),
                 top: Math.round(d.getBoundingClientRect().top),
                 t: (d.innerText || '').slice(0, 40).replace(/\\n/g, ' ') }));
  return {
    scroll: Math.round(main ? main.scrollTop : -1),
    has_build_btn: !!([...document.querySelectorAll('button')]
      .find(b => b.innerText.trim() === '构建引用图谱')),
    has_kpi: txt.includes('逻辑节点'),
    has_empty: txt.includes('尚未构建图谱'),
    has_banner: txt.includes('图谱构建完成'),
    iframes: [...document.querySelectorAll(MAIN_IFRAME_SEL)].map(f => ({
      top: Math.round(f.getBoundingClientRect().top),
      h: Math.round(f.getBoundingClientRect().height) })),
    bordered: bordered.slice(0, 12),
    tail: txt.slice(-260)
  };
})()""".replace("MAIN_IFRAME_SEL", "'[data-testid=\"stMain\"] iframe'")


def snap(pg, tag):
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        pg.screenshot(path=str(OUT / ("p_%s.png" % tag)), full_page=False)
    except Exception as e:
        print("   !! 截图失败 %s" % e)
    return pg.evaluate(PROBE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8631")
    ap.add_argument("--secs", type=float, default=30.0)
    a = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.launch(args=["--force-device-scale-factor=1"])
        ctx = br.new_context(viewport={"width": 1920, "height": 1080},
                             device_scale_factor=1)
        pg = ctx.new_page()
        pg.goto("http://127.0.0.1:%s" % a.port,
                wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_selector('[data-testid="stSidebar"] label',
                             state="attached", timeout=180000)
        time.sleep(3.0)

        # 侧栏进图谱页
        labels = pg.locator('[data-testid="stSidebar"] label')
        ok = False
        for i in range(labels.count()):
            el = labels.nth(i)
            try:
                if el.inner_text().strip() == "引用图谱与综述":
                    el.scroll_into_view_if_needed(timeout=5000)
                    time.sleep(0.2)
                    b = el.bounding_box()
                    if b:
                        pg.mouse.click(b["x"] + b["width"] / 2,
                                       b["y"] + b["height"] / 2)
                    ok = True
                    break
            except Exception:
                continue
        print("进入图谱页：%s" % ok)
        time.sleep(3.0)

        s0 = snap(pg, "00_before")
        print("[构建前] scroll=%s build_btn=%s kpi=%s empty=%s banner=%s iframes=%s"
              % (s0["scroll"], s0["has_build_btn"], s0["has_kpi"],
                 s0["has_empty"], s0["has_banner"], s0["iframes"]))
        print("   bordered=%s" % json.dumps(s0["bordered"][:6], ensure_ascii=False))

        btn = pg.locator('%s button:has-text("构建引用图谱")' % MAIN).first
        if not btn.count():
            print("!! 找不到构建按钮")
            br.close()
            return 1
        btn.scroll_into_view_if_needed(timeout=5000)
        time.sleep(0.3)
        b = btn.bounding_box()
        pg.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        print("[点击] 构建引用图谱 @ %.1f" % time.time())

        t0 = time.time()
        n = 0
        while time.time() - t0 < a.secs:
            time.sleep(1.0)
            n += 1
            s = snap(pg, "t%02d" % n)
            print("[%5.1fs] scroll=%-4s btn=%-5s kpi=%-5s empty=%-5s banner=%-5s "
                  "iframes=%s cards=%d"
                  % (time.time() - t0, s["scroll"], s["has_build_btn"],
                     s["has_kpi"], s["has_empty"], s["has_banner"],
                     s["iframes"], len(s["bordered"])))
            if s["has_banner"] and n > 3:
                # 横幅出现后再跟 6 秒看是否稳定
                for k in range(6):
                    time.sleep(1.0)
                    n += 1
                    s2 = snap(pg, "t%02d" % n)
                    print("[%5.1fs] scroll=%-4s btn=%-5s kpi=%-5s empty=%-5s "
                          "banner=%-5s iframes=%s"
                          % (time.time() - t0, s2["scroll"], s2["has_build_btn"],
                             s2["has_kpi"], s2["has_empty"], s2["has_banner"],
                             s2["iframes"]))
                print("   cards=%s" % json.dumps(s2["bordered"][:8],
                                                 ensure_ascii=False))
                break
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
