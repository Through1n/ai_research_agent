"""话术 19：线上环境全功能验收。
对 https://ai4s-research-agent.app.workbuddy.host/ 做逐页面验收 + 完整演示路径 + 线上特有问题检查。
输出：docs/shots/online/*.png 截图 + JSON 结果（由报告消费）。
"""
import asyncio, json, time, os, sys
from playwright.async_api import async_playwright

URL = "https://ai4s-research-agent.app.workbuddy.host/"
OUT = "docs/shots/online"
os.makedirs(OUT, exist_ok=True)

MENU = [
    "🤖 0. 智能总助手", "📁 1. 文献库管理", "💬 2. 跨文献问答与溯源",
    "📖 3. 版式阅读与智能翻译", "🔍 4. 论文检索与追踪", "🕸️ 5. 引用图谱与综述",
    "✍️ 6. 写作辅助与可视化", "🏗️ A. 系统架构设计", "💡 B. 技术创新亮点",
]

R = {}   # 结果收集


def log(*a):
    print(*a, flush=True)


async def wait_for_nav(pg, timeout=180):
    """轮询等侧边栏导航 radio 出现（首屏要跑完 import + Container 初始化）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            radios = await pg.query_selector_all('[data-testid="stRadio"]')
            for r in radios:
                opts = await r.query_selector_all('input[type="radio"]')
                if len(opts) >= 7:
                    return time.time() - t0, radios
        except Exception:
            pass
        await pg.wait_for_timeout(1500)
    return None, None


async def nav_to(pg, label, idx):
    """点侧边栏导航项，等页面重渲染完成。"""
    t0 = time.time()
    clicked = False
    for r in await pg.query_selector_all('[data-testid="stRadio"]'):
        opts = await r.query_selector_all('input[type="radio"]')
        if len(opts) < 7:
            continue
        for o in opts:
            v = (await o.get_attribute("value")) or ""
            if label.split(". ")[-1][:6] in v or label in v:
                await o.click(force=True)
                clicked = True
                break
        if clicked:
            break
    if not clicked:
        # 退化：点包含关键字的 label
        loc = pg.locator(f'label:has-text("{label.split(". ",1)[-1]}")').first
        if await loc.count():
            await loc.click(force=True)
            clicked = True
    # 等重渲染：等 body 文本量稳定
    prev, stable = -1, 0
    for _ in range(40):
        await pg.wait_for_timeout(900)
        try:
            cur = len(await pg.inner_text("body"))
        except Exception:
            cur = 0
        stable = stable + 1 if cur == prev and cur > 0 else 0
        prev = cur
        if stable >= 3:
            break
    return clicked, time.time() - t0


async def snap(pg, name, full=False):
    p = os.path.join(OUT, name)
    await pg.screenshot(path=p, full_page=full)
    return p


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 1000})
        jserr, pageerr = [], []
        pg.on("pageerror", lambda e: pageerr.append(str(e)))
        pg.on("console", lambda m: jserr.append(f"{m.type}: {m.text[:200]}") if m.type == "error" else None)

        # ---------- 1. 首屏 ----------
        t0 = time.time()
        await pg.goto(URL, wait_until="domcontentloaded", timeout=90000)
        dt, radios = await wait_for_nav(pg)
        R["first_paint_sec"] = round(time.time() - t0, 1)
        R["nav_appeared_sec"] = round(dt, 1) if dt else None
        R["title"] = await pg.title()
        log(f"[1] 首屏可用 {R['first_paint_sec']}s / 导航出现 {R['nav_appeared_sec']}s / title={R['title']!r}")
        await snap(pg, "o0_home.png")
        R["home_body_len"] = len(await pg.inner_text("body"))
        log(f"[1] body 可见文本 {R['home_body_len']} 字")

        # ---------- 2. 逐页面 ----------
        pages = []
        for i, m in enumerate(MENU):
            ok, dt = await nav_to(pg, m, i)
            body = ""
            try:
                body = await pg.inner_text("body")
            except Exception:
                pass
            shot = await snap(pg, f"o{i+1}_{m.split('. ')[-1][:6]}.png")
            pages.append({"menu": m, "clicked": ok, "render_sec": round(dt, 1),
                          "body_len": len(body), "shot": shot})
            log(f"[2] {m[:22]:24s} clicked={ok} {dt:5.1f}s body={len(body):5d} 字")
        R["pages"] = pages
        R["js_errors_after_nav"] = jserr[:8]
        R["page_errors"] = pageerr[:8]

        # ---------- 3. 演示路径 ----------
        demo = {}
        # 3a. 一键加载演示数据（记录首次耗时：含 ONNX 模型下载）
        loc = pg.locator('button:has-text("一键加载演示数据")').first
        if await loc.count():
            t1 = time.time()
            await loc.click(force=True)
            # 等成功提示或文献库出现论文
            ok = False
            for _ in range(160):   # 最长 ~4 分钟
                await pg.wait_for_timeout(2000)
                try:
                    txt = await pg.inner_text("body")
                except Exception:
                    txt = ""
                if "演示数据已就绪" in txt or "演示数据加载完成" in txt:
                    ok = True
                    break
                if "失败" in txt and "演示数据" in txt:
                    break
            demo["load_demo_sec"] = round(time.time() - t1, 1)
            demo["load_demo_ok"] = ok
            log(f"[3a] 加载演示数据 {demo['load_demo_sec']}s ok={ok}")
        else:
            demo["load_demo_sec"] = None
            demo["load_demo_ok"] = False
            log("[3a] 未找到演示数据按钮")
        await snap(pg, "o10_demo_loaded.png")
        demo["body_after_demo_len"] = len(await pg.inner_text("body"))
        R["demo"] = demo

        # 3b. 问答（无 Key → 应走离线降级，记录耗时与是否出现标识条）
        qa = {}
        ok, dt = await nav_to(pg, "💬 2. 跨文献问答与溯源", 99)
        ta = pg.locator('textarea').first
        if await ta.count():
            t2 = time.time()
            await ta.fill("Transformer 的核心创新是什么？")
            await pg.keyboard.press("Enter")
            answered = False
            for _ in range(90):
                await pg.wait_for_timeout(1500)
                try:
                    txt = await pg.inner_text("body")
                except Exception:
                    txt = ""
                if "离线占位" in txt or "【1】" in txt or "来源" in txt:
                    answered = True
                    break
            qa["ask_sec"] = round(time.time() - t2, 1)
            qa["answered"] = answered
            body = await pg.inner_text("body")
            qa["has_offline_banner"] = "离线占位" in body
            qa["has_citation_mark"] = "【" in body
            log(f"[3b] 问答 {qa['ask_sec']}s answered={answered} 离线标识={qa['has_offline_banner']} 引用角标={qa['has_citation_mark']}")
        await snap(pg, "o11_qa.png", full=True)
        R["qa"] = qa

        # 3c. 引用图谱（演示模式示意图谱）
        graph = {}
        ok, dt = await nav_to(pg, "🕸️ 5. 引用图谱与综述", 99)
        gl = pg.locator('button:has-text("演示模式示意图谱")').first
        if await gl.count():
            t3 = time.time()
            await gl.click(force=True)
            done = False
            for _ in range(90):
                await pg.wait_for_timeout(1500)
                try:
                    txt = await pg.inner_text("body")
                except Exception:
                    txt = ""
                if "演示模式示意图谱" in txt and ("节点" in txt or "图谱构建完成" in txt):
                    done = True
                    break
            graph["demo_graph_sec"] = round(time.time() - t3, 1)
            graph["done"] = done
            log(f"[3c] 演示图谱 {graph['demo_graph_sec']}s done={done}")
        await snap(pg, "o12_graph.png", full=True)
        R["graph"] = graph

        # 3d. 写作页 Figure Caption
        cap = {}
        ok, dt = await nav_to(pg, "✍️ 6. 写作辅助与可视化", 99)
        cl = pg.locator('button:has-text("载入示例数据")').first
        if await cl.count():
            t4 = time.time()
            await cl.click(force=True)
            await pg.wait_for_timeout(6000)
            cap["load_csv_sec"] = round(time.time() - t4, 1)
            body = await pg.inner_text("body")
            cap["csv_loaded"] = "示例" in body or len(body) > 500
            log(f"[3d] 载入示例数据 {cap['load_csv_sec']}s")
        await snap(pg, "o13_writing.png", full=True)
        R["caption"] = cap

        # ---------- 4. 线上特有：时区 ----------
        R["client_timezone"] = await pg.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        R["server_date_header"] = None
        try:
            resp = await pg.request.get(URL)
            R["server_date_header"] = resp.headers.get("date")
        except Exception:
            pass
        log(f"[4] 浏览器时区={R['client_timezone']} 服务器 Date 头={R['server_date_header']}")

        R["js_errors_total"] = len(jserr)
        R["page_errors_total"] = len(pageerr)
        # 全量留档：js_errors_after_nav 是"导航出现那一刻"的快照，会漏掉演示路径
        # 阶段新增的错误（实测 2 条就是这么漏的，见 _probe_js_errors.py）。
        R["js_errors_all"] = jserr[:20]
        R["page_errors_all"] = pageerr[:20]
        log(f"[汇总] JS 错误 {len(jserr)} / 页面错误 {len(pageerr)}")
        for e in jserr[:6]:
            log(f"    [js] {e}")
        for e in pageerr[:6]:
            log(f"    [page] {e}")

        await b.close()

    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)
    log("✓ 结果写入 docs/shots/online/result.json")


asyncio.run(main())
