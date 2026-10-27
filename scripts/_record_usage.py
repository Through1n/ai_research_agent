"""录制「真实使用」素材：用 Playwright 驱动真实应用，逐段落录成视频。

为什么需要：早期演示片是「静态截图 + 镜头平移」，评委会看不到产品真的在跑
（用户 2026-10-27 反馈："视频里很多地方都没有对产品的实际使用"）。本脚本用
真浏览器点真按钮、真输入、真等结果，录下真实交互过程。

三个关键工程点：
  1. **光标是注入的 DOM 元素**：Playwright 录屏不光标（它不移动真实 OS 光标），
     所以每次 mouse.move 后同步更新一个 fixed div 的位置，成片里才看得见"在点哪里"。
  2. **每段一个独立文件**：段与段之间需要不同的前置状态（加载演示数据 / 预建图谱），
     拼在一起录会互相污染；独立文件也让容错更简单（某段失败不影响其它段）。
  3. **marks.json 只记"可剪区间"**：构图由 build_demo_video_usage.py 决定，
     这里只如实记录每段的时长与关键时刻，避免两边各写一套时间逻辑。

用法：
  python scripts/_record_usage.py --port 8630              # 录全部段
  python scripts/_record_usage.py --port 8630 --only lib,qa
  python scripts/_record_usage.py --list
输出：资料/_构建/usage/raw_<name>.webm + 资料/_构建/usage/marks.json
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "资料/_构建/usage"
W, H = 1920, 1080

CURSOR_JS = """
(() => {
  if (document.getElementById('__mk_cur')) return;
  const d = document.createElement('div');
  d.id = '__mk_cur';
  d.style.cssText = 'position:fixed;left:0;top:0;width:26px;height:26px;'
    + 'z-index:2147483647;pointer-events:none;transform:translate(-3px,-2px);'
    + 'filter:drop-shadow(0 1px 2px rgba(0,0,0,.45));';
  d.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26">'
    + '<path d="M3 2 L3 20 L8.2 15.4 L11.4 22.4 L14.6 21 L11.4 14.2 L18 14.2 Z" '
    + 'fill="#ffffff" stroke="#111111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.documentElement.appendChild(d);
})();
"""


def _cur(pg, x, y):
    pg.evaluate("([x,y])=>{const d=document.getElementById('__mk_cur');"
                "if(d){d.style.left=x+'px';d.style.top=y+'px';}}", [x, y])


def move(pg, x, y, steps=14, dur=0.30):
    """把注入光标平滑移到 (x,y)——分步插值，成片里是"人手的移动"而不是瞬移。"""
    try:
        sx = float(pg.evaluate("()=>{const d=document.getElementById('__mk_cur');"
                               "return d?parseFloat(d.style.left||'0'):0;}"))
        sy = float(pg.evaluate("()=>{const d=document.getElementById('__mk_cur');"
                               "return d?parseFloat(d.style.top||'0'):0;}"))
    except Exception:
        sx, sy = 0.0, 0.0
    for i in range(1, steps + 1):
        u = i / float(steps)
        # smoothstep：起步慢、到位前减速
        e = u * u * (3 - 2 * u)
        _cur(pg, sx + (x - sx) * e, sy + (y - sy) * e)
        pg.mouse.move(sx + (x - sx) * e, sy + (y - sy) * e)
        time.sleep(dur / steps)


def click_el(pg, loc, wait=1.0, settle_timeout=180):
    """移动光标到元素中心 → 点击 → 等 Streamlit 跑完。

    ⚠️ 必须先 scroll_into_view：Playwright 的 bounding_box 给的是**视口坐标**，
        Streamlit 长页面里位于视口下方的按钮 y 会 > 1080，直接 mouse.click 点到的是
        视口外的空白（2026-10-27 实测：写作页「生成该图 Figure Caption」永远点不中）。
    """
    try:
        loc.scroll_into_view_if_needed(timeout=5000)
        time.sleep(0.20)
    except Exception:
        pass
    box = loc.bounding_box()
    if not box:
        return False
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    move(pg, x, y)
    time.sleep(0.16)
    pg.mouse.click(x, y)
    _settle(pg, wait, settle_timeout)
    return True


def _settle(pg, quiet=1.0, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        busy = 0
        for s in ('[data-testid="stStatusWidget"]', '[data-testid="stSpinner"]'):
            try:
                busy += pg.locator(s).count()
            except Exception:
                pass
        if busy == 0:
            break
        time.sleep(0.25)
    time.sleep(quiet)


def goto(pg, label, settle=1.4):
    labels = pg.locator('[data-testid="stSidebar"] label')
    for i in range(labels.count()):
        el = labels.nth(i)
        try:
            if el.inner_text().strip() == label:
                click_el(pg, el, wait=settle)
                pg.evaluate(CURSOR_JS)
                return True
        except Exception:
            continue
    return False


def by_text(pg, text, testid=None):
    sel = f'[data-testid="{testid}"] button' if testid else "button"
    loc = pg.locator(f'{sel}:has-text("{text}")')
    return loc.first if loc.count() else None


def wait_text(pg, text, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if text in pg.inner_text('[data-testid="stMain"]'):
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


_T0 = [0.0]          # 本段录像的起点（秒），用于打印 MARK 时刻做剪辑标定


def _elapsed(pg=None):
    """距本段开始录制的秒数——与成片素材时间轴同一基准，剪窗口时直接照着填。"""
    return time.time() - _T0[0]


# ------------------------------------------------------------------ 各段脚本
# ⚠️ 定位纪律（2026-10-27 踩坑）：**主区控件一律带作用域**。
# 侧栏「模型接入与账号」里也有 stSelectbox，`[data-testid="stSelectbox"]` 取 .first
# 会抓到它——当时点开模型下拉又把页面切走了，录出来整段是错的。
# 可靠写法：Streamlit 给带 key 的控件容器加了 `st-key-<key>` 类，配合 `[class*=...]` 精确命中。
MAIN = '[data-testid="stMain"]'


def pick(pg, key, index=0, settle=1.3, timeout=25):
    """点开指定 key 的 selectbox 并选第 index 项。

    ⚠️ 两处都要轮询（2026-10-27 实测）：文献库/版式阅读页的 selectbox 是首屏之后
        才挂载的，固定 sleep 后取一次 count 会拿到 0；下拉选项同样是异步渲染，
        点开 0.45s 后 `[role="option"]` 仍可能为空——那一段就录成了「Choose an option」
        的空白页（read 段整段作废）。
    """
    box = pg.locator(f'[class*="st-key-{key}"] [data-testid="stSelectbox"]').first
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if box.count():
                break
        except Exception:
            pass
        time.sleep(0.25)
    if not box.count():
        print("   （找不到 selectbox: %s）" % key)
        return False
    click_el(pg, box, wait=0.6)
    opts = pg.locator('[role="option"]')
    t0 = time.time()
    n = 0
    while time.time() - t0 < 12:
        n = opts.count()
        if n > index:
            break
        time.sleep(0.20)
    if n <= index:
        print("   （%s 下拉选项不足：%d 个，需要第 %d 项）" % (key, n, index + 1))
        return False
    click_el(pg, opts.nth(index), wait=settle)
    time.sleep(0.45)
    return True


def main_btn(pg, text):
    loc = pg.locator(f'{MAIN} button:has-text("{text}")')
    return loc.first if loc.count() else None


def tab(pg, text):
    """页签定位。

    ⚠️ Streamlit 1.63 的页签是 `[role="tab"]`；`[data-testid="stTabs"] button` 会把
        页签面板**内部**的所有按钮一起抓进来（实测 46 个 vs 6 个页签），
        `.first` 很可能命中面板里的普通按钮 —— 点了等于没切页签，录出来还是旧 tab
        （read 段的「中英同步滚动」就是这么失败的）。
    """
    loc = pg.locator(f'{MAIN} [role="tab"]:has-text("{text}")')
    if loc.count():
        return loc.first
    loc = pg.locator(f'{MAIN} [data-testid="stTabs"] button:has-text("{text}")')
    return loc.first if loc.count() else None


def do_intro(pg):
    """项目介绍：扫侧栏结构 → 进 Agent 页 → 分段浏览（每块停留 3s）。

    停留是必需的：这段旁白有 30s（项目定位 + 把四件事串成流水线），
    录成一路匀速下滚会既看不清内容、又只能用极慢的放慢倍数去凑时长。
    """
    time.sleep(1.2)
    labels = pg.locator('[data-testid="stSidebar"] label')
    for i in range(min(7, labels.count())):
        el = labels.nth(i)
        b = el.bounding_box()
        if b:
            move(pg, b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, dur=0.5)
            time.sleep(0.55)
    goto(pg, "智能总助手")
    time.sleep(2.2)
    # 逐块浏览：滚一段停一下，成片里是"人在看页面"而不是"页面在飘"
    for dy, wait in ((420, 3.4), (430, 3.4), (-260, 2.6), (760, 3.4)):
        pg.mouse.wheel(0, dy)
        time.sleep(wait)


def do_lib(pg):
    """文献库：选论文 → 全方位解析 → 逐块浏览标题树/表格/插图/参考文献。"""
    if not goto(pg, "文献库管理"):
        return
    time.sleep(1.6)
    print("   struct_sel=%s" % pick(pg, "struct_sel"), flush=True)
    btn = main_btn(pg, "全方位解析")
    if btn:
        click_el(pg, btn, wait=1.2, settle_timeout=240)
        print("   标题树=%s" % wait_text(pg, "标题树", 240), flush=True)
        _settle(pg, 2.2, 240)
    # 解析结果是长页面：逐块停住看，比一口气滚到底更能说明"真的解析出来了"
    for dy, wait in ((450, 3.2), (470, 3.2), (470, 3.2), (500, 3.2)):
        pg.mouse.wheel(0, dy)
        time.sleep(wait)


def do_qa(pg):
    """问答：点示例问题 → 提问 → 等答案 + 引用校验 + 溯源卡。"""
    if not goto(pg, "跨文献问答与溯源"):
        return
    time.sleep(1.2)
    pill = pg.locator('[data-testid="stButtonGroup"] button').first
    if pill.count():
        click_el(pg, pill, wait=1.5)
    q = pg.locator('input[aria-label="请输入问题"]').first
    if q.count():
        b = q.bounding_box()
        if b:
            move(pg, b["x"] + 150, b["y"] + b["height"] / 2)
            time.sleep(0.5)
    btn = main_btn(pg, "提问")
    if btn:
        click_el(pg, btn, wait=1.0, settle_timeout=60)
        wait_text(pg, "答案来源", 300)
        _settle(pg, 2.2, 300)
    pg.mouse.wheel(0, 300)
    time.sleep(2.2)


def do_read(pg):
    """阅读：选论文 → 原生渲染（版式还原）→ 中英同步滚动 → 逐段翻译 → 滚到双栏结果。

    两处必须「滚到结果区」，否则白等：结果出来了但视口还停在上方控件上，
    录下来只有一堆按钮（2026-10-27 第一次录废在这一点）。
    """
    if not goto(pg, "版式阅读与智能翻译"):
        return
    time.sleep(1.6)
    # index=1：选项表是 [""] + 文献库文件，第 0 项是空占位（"Choose an option"）。
    # 选它等于「不选」，页面会退回「请先在文献库导入论文」——第一次录制就栽在这。
    print("   read_sel=%s" % pick(pg, "read_sel", index=1), flush=True)
    time.sleep(0.9)

    btn = main_btn(pg, "渲染该页")
    if btn:
        click_el(pg, btn, wait=1.8, settle_timeout=120)
        print("   原生渲染=%s" % wait_text(pg, "已渲染第", 120), flush=True)
    time.sleep(1.2)
    # 版面 iframe 在「已渲染第 N 页」这行下面，但要滚一点才进视口。滚太少看不见，
    # 滚 420 又会把它顶出视口（2026-10-27 两次实测）。380 + 停 5s 让评委看清双栏版面。
    pg.mouse.wheel(0, 380)
    time.sleep(5.0)                     # 停住看原生版面（窗口 A）
    print("   MARK_A@%.1f" % _elapsed(pg), flush=True)

    t = tab(pg, "中英同步滚动")
    if t:
        click_el(pg, t, wait=1.6)
    time.sleep(1.0)
    # 点击前的 iframe 指纹（"top:height"）：后面判断"哪个 iframe 是这次新出来的"要用。
    # 放在 if 外面，按钮没找到时也有值（空列表），不会 NameError。
    _before = pg.evaluate(
        "() => [...document.querySelectorAll('[data-testid=\"stMain\"] iframe')]"
        ".map(f => Math.round(f.getBoundingClientRect().top) + ':'"
        " + Math.round(f.getBoundingClientRect().height))")
    btn = main_btn(pg, "加载并逐段翻译")
    if btn:
        click_el(pg, btn, wait=1.0, settle_timeout=60)
        # 等**新出现的高 iframe**，不猜文案、也不假设 iframe 只有一个：
        # ① 文案会骗人 —— 原等待串「句级联动」被同日加上的卡片描述
        #    「…中英对照（句级联动 · 左右同步滚动）」命中，从页面加载那刻就恒真；
        # ② 数量也会骗人 —— st.tabs 的各面板都在 DOM 里，切到「中英同步滚动」后
        #    「原生渲染」那一页的 900px iframe 仍然存在，数个数同样会瞬间满足。
        # 所以取点击前的 iframe 指纹，等一个**新的、高度 >700** 的 iframe 出现。
        try:
            pg.wait_for_function(
                "(pre) => {"
                "  const fs = [...document.querySelectorAll('[data-testid=\"stMain\"] iframe')];"
                "  return fs.some(f => {"
                "    const r = f.getBoundingClientRect();"
                "    const k = Math.round(r.top) + ':' + Math.round(r.height);"
                "    return !pre.includes(k) && r.height > 700;"
                "  });"
                "}", arg=_before, timeout=300000)
        except Exception:
            print("   !! 双栏 iframe 300s 未出现", flush=True)
        _settle(pg, 3.0, 300)
    # ⚠️ 双栏正文在 iframe 里，而 st.tabs 的每个面板都留在 DOM 中：切到「中英同步滚动」
    #    之后，「原生渲染」那一页的 900px iframe 依然存在，且排在前面。按"第一个高的"
    #    取会滚到原生版面去（2026-10-27 第二次录制就是这么录错的，双栏窗口里全是控件）。
    #    取**点击后新出现的那个**（不在 _before 指纹里、高度 >700）；取不到才退回最后一个。
    try:
        info = pg.evaluate(
            "(pre) => {"
            "  const fs = [...document.querySelectorAll('[data-testid=\"stMain\"] iframe')];"
            "  const fresh = fs.filter(f => {"
            "    const r = f.getBoundingClientRect();"
            "    const k = Math.round(r.top) + ':' + Math.round(r.height);"
            "    return !pre.includes(k) && r.height > 700;"
            "  });"
            "  const pick = fresh.length ? fresh[fresh.length - 1]"
            "                    : fs.filter(f => f.getBoundingClientRect().height > 700).pop();"
            "  if (!pick) return null;"
            "  const r = pick.getBoundingClientRect();"
            "  return {y: r.top + window.scrollY, h: r.height};"
            "}", arg=_before)
        if info and info.get("h", 0) > 700:
            dy = int(info["y"] - 110)
            if dy > 0:
                pg.mouse.wheel(0, dy)
        else:
            pg.mouse.wheel(0, 900)
    except Exception:
        pg.mouse.wheel(0, 900)
    time.sleep(8.0)                     # 停住看中英对照（窗口 B：这段旁白有 5s）
    print("   MARK_B@%.1f" % _elapsed(pg), flush=True)


def do_graph(pg):
    """图谱：点「构建引用图谱」跑真实构建 → KPI 出来后滚动看结构。"""
    if not goto(pg, "引用图谱与综述"):
        return
    time.sleep(1.6)
    btn = main_btn(pg, "构建引用图谱")
    if btn:
        click_el(pg, btn, wait=1.0, settle_timeout=60)
        wait_text(pg, "逻辑节点", 180)
        _settle(pg, 2.2, 180)
    pg.mouse.wheel(0, 460)
    time.sleep(2.4)
    t = pg.locator(f'{MAIN} [role="tab"]').last
    if t.count():
        click_el(pg, t, wait=1.8)
    time.sleep(2.0)


def do_search(pg):
    """检索：输入关键词 → 搜索 arXiv → 等结果列表真的出现。"""
    if not goto(pg, "论文检索与追踪"):
        return
    time.sleep(1.2)
    box = pg.locator('input[aria-label="标题 / 关键词 / ID"]').first
    if box.count():
        b = box.bounding_box()
        if b:
            move(pg, b["x"] + 80, b["y"] + b["height"] / 2)
            time.sleep(0.35)
            pg.mouse.click(b["x"] + 80, b["y"] + b["height"] / 2)
            time.sleep(0.35)
            pg.keyboard.type("attention is all you need", delay=80)
            time.sleep(0.5)
    pg.evaluate(CURSOR_JS)          # 输入会触发重绘，光标元素要补回来
    btn = main_btn(pg, "搜索 arXiv")
    if btn:
        click_el(pg, btn, wait=1.0, settle_timeout=60)
        # 后台检索：状态条秒退，唯一可靠信号是结果卡上的「下载并入库」按钮
        print("   检索结果=%s" % wait_text(pg, "下载并入库", 200), flush=True)
        _settle(pg, 2.6, 200)
    pg.mouse.wheel(0, 340)
    time.sleep(2.2)


def do_write(pg):
    """写作：载入示例数据 → 滚到推荐图 → 生成 Figure Caption → 等结果。"""
    if not goto(pg, "写作辅助与可视化"):
        return
    time.sleep(1.3)
    btn = main_btn(pg, "载入示例数据")
    if btn:
        click_el(pg, btn, wait=2.0, settle_timeout=90)
    print("   推荐图=%s" % wait_text(pg, "自动推荐图表", 90), flush=True)
    _settle(pg, 1.4, 60)
    # Caption 按钮在推荐图下方、首屏之外：先滚下去（滚轮在视频里是自然动作），
    # click_el 里还有 scroll_into_view 兜底。
    pg.mouse.wheel(0, 720)
    time.sleep(1.5)
    pg.mouse.wheel(0, 520)
    time.sleep(1.6)
    btn = main_btn(pg, "生成该图 Figure Caption")
    if btn:
        click_el(pg, btn, wait=1.0, settle_timeout=60)
        # 带冒号：按钮文案「生成该图 Figure Caption」不含冒号，结果块是「Figure Caption: …」
        print("   Caption=%s" % wait_text(pg, "Figure Caption:", 200), flush=True)
        _settle(pg, 2.6, 200)
    print("   MARK@%.1f" % _elapsed(pg), flush=True)
    pg.mouse.wheel(0, 200)
    time.sleep(2.6)


SECTIONS = [("intro", do_intro), ("lib", do_lib), ("qa", do_qa), ("read", do_read),
            ("graph", do_graph), ("search", do_search), ("write", do_write)]


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8630")
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        print("段落：" + ", ".join(n for n, _ in SECTIONS))
        return 0

    want = [s.strip() for s in a.only.split(",") if s.strip()]
    todo = [(n, f) for n, f in SECTIONS if not want or n in want]

    OUT.mkdir(parents=True, exist_ok=True)
    marks_path = OUT / "marks.json"
    marks = {}
    if marks_path.exists():
        marks = json.loads(marks_path.read_text(encoding="utf-8"))

    url = f"http://127.0.0.1:{a.port}"
    with sync_playwright() as p:
        br = p.chromium.launch(args=["--force-device-scale-factor=1"])
        for name, fn in todo:
            vdir = OUT / f"v_{name}"
            if vdir.exists():
                shutil.rmtree(vdir, ignore_errors=True)
            vdir.mkdir(parents=True, exist_ok=True)
            ctx = br.new_context(viewport={"width": W, "height": H},
                                 record_video_dir=str(vdir),
                                 record_video_size={"width": W, "height": H},
                                 device_scale_factor=1)
            pg = ctx.new_page()
            t0 = time.time()
            _T0[0] = t0
            pg.goto(url, wait_until="domcontentloaded", timeout=90000)
            # 等首屏渲染（侧栏 label 出现）
            # state="attached"：默认的 visible 会挑到侧栏第一个隐藏的 widget label
            # （文件上传器等），等到 180s 也"没可见"，实际页面早就好了。
            try:
                pg.wait_for_selector('[data-testid="stSidebar"] label',
                                     state="attached", timeout=180000)
            except Exception:
                print("  !! %s 首屏超时" % name)
            _settle(pg, 2.0, 180)
            pg.evaluate(CURSOR_JS)
            _cur(pg, 960, 540)
            marks[name] = {"t_drive_start": round(time.time() - t0, 2)}
            print("→ 录制 %s" % name, flush=True)
            marks[name]["t_action"] = round(time.time() - t0, 2)
            try:
                fn(pg)
            except Exception as e:
                print("  !! %s 脚本异常：%s" % (name, str(e)[:160]))
            marks[name]["t_end"] = round(time.time() - t0, 2)
            raw = pg.video.path()
            ctx.close()
            dst = OUT / f"raw_{name}.webm"
            try:
                shutil.move(raw, dst)
            except Exception as e:
                print("  !! 取视频失败：%s" % str(e)[:120])
            print("   %s  %.1fs  -> %s" % (name, marks[name]["t_end"], dst.name), flush=True)
        br.close()

    marks_path.write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nmarks -> %s" % marks_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
