"""首屏杂乱度实测：量「用户第一眼到底看到多少东西」。

为什么不用「屏数」当主指标：Streamlit 的折叠区（expander）、未激活的 tab 内容
不进 scrollHeight，屏数会把「塞在小折叠里的 12 张卡片」和「首屏平铺 4 个区块」
算成同一个数——实测改版前后 p1_library 屏数只差 0.3，但平铺区块数差 4 倍。

本脚本量的四件事（全部只取**用户可见**的元素：display/visibility/opacity 不过滤）：
    1. 可见元素总数      —— 密度直觉
    2. 可见文字字符数    —— 信息量
    3. 色块数（stAlert 等有底色的容器）—— 视觉噪音源
    4. 交互控件数（按钮/输入框/胶囊/折叠区）—— 操作噪音源

用法：
    python scripts/_measure_clutter.py --port 8512 --serve
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    ("p0_agent", "智能总助手"),
    ("p1_library", "文献库管理"),
    ("p2_qa", "跨文献问答与溯源"),
    ("p3_reading", "版式阅读与智能翻译"),
    ("p4_search", "论文检索与追踪"),
    ("p5_graph", "引用图谱与综述"),
    ("p6_writing", "写作辅助与可视化"),
]

# 在页面上下文中执行：数可见元素 / 可见文字 / 色块 / 交互控件
PROBE = r"""
() => {
  const main = document.querySelector('[data-testid="stMain"]')
             || document.querySelector('[data-testid="stAppViewContainer"]')
             || document.body;
  const vis = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const all = Array.from(main.querySelectorAll('*'));
  const shown = all.filter(vis);
  const text = shown.map(el => (el.innerText || '')).join('');
  const alerts = shown.filter(el => {
    const t = (el.getAttribute('data-testid') || '');
    if (t.startsWith('stAlert')) return true;
    const s = getComputedStyle(el);
    const bg = s.backgroundColor || '';
    return bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent'
           && el.getBoundingClientRect().height > 28;
  }).length;
  const ctrls = shown.filter(el => {
    const t = (el.getAttribute('data-testid') || '');
    return t.startsWith('stButton') || t.startsWith('stTextInput')
        || t.startsWith('stTextArea') || t.startsWith('stSelectbox')
        || t.startsWith('stMultiSelect') || t.startsWith('stExpander')
        || t.startsWith('stTabs') || t.startsWith('stToggle')
        || t.startsWith('stRadio') || t.startsWith('stCheckbox')
        || t.startsWith('stDownload') || t.startsWith('stFileUploader');
  }).length;
  const heads = shown.filter(el => {
    const s = getComputedStyle(el);
    const fs = parseFloat(s.fontSize || '0');
    return fs >= 22 && el.children.length <= 3 && (el.innerText || '').trim().length > 0;
  }).length;
  const side = document.querySelector('[data-testid="stSidebar"]');
  const sideItems = side ? Array.from(side.querySelectorAll('label')).filter(vis).length : 0;
  const sideText = side ? (side.innerText || '') : '';
  return {
    elements: shown.length,
    chars: text.length,
    blocks: alerts,
    controls: ctrls,
    bigHeads: heads,
    sideItems: sideItems,
    sideEmoji: (sideText.match(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu) || []).length,
  };
}
"""


def wait_http(url, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8512)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    proc = None
    try:
        url = f"http://127.0.0.1:{args.port}/"
        proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app.py",
             "--server.port", str(args.port), "--server.headless", "true",
             "--browser.gatherUsageStats", "false"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_http(url):
            print("[FAIL] 服务没起来")
            return 1
        from playwright.sync_api import sync_playwright
        rows = []
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector('[data-testid="stSidebar"]', timeout=180000)
            page.wait_for_function("() => !document.body.innerText.includes('Please wait')",
                                   timeout=120000)
            page.wait_for_timeout(2500)
            for slug, label in PAGES:
                try:
                    page.locator('[data-testid="stSidebar"]').get_by_text(
                        label, exact=False).first.click(timeout=20000)
                    page.wait_for_timeout(2200)
                    page.wait_for_function(
                        "() => !document.body.innerText.includes('Please wait')", timeout=90000)
                    page.wait_for_timeout(800)
                except Exception as exc:
                    print(f"  [WARN] 切页 {label} 失败：{type(exc).__name__}")
                m = page.evaluate(PROBE)
                m["page"] = slug
                rows.append(m)
                print(f"  {slug:12} 元素={m['elements']:5d} 文字={m['chars']:6d} "
                      f"色块={m['blocks']:3d} 控件={m['controls']:3d} "
                      f"侧栏项={m['sideItems']:3d} 侧栏emoji={m['sideEmoji']:3d}")
            b.close()
        if args.out:
            out = ROOT / args.out
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  -> {out}")
    finally:
        if proc is not None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
