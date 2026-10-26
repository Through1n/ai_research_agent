"""线上验收探针：摸清 Streamlit 线上的 DOM 结构（radio 选项、按钮文案）。"""
import asyncio, json, sys
from playwright.async_api import async_playwright

URL = "https://ai4s-research-agent.app.workbuddy.host/"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        pg.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

        t0 = asyncio.get_event_loop().time()
        await pg.goto(URL, wait_until="domcontentloaded", timeout=90000)
        # 等 Streamlit 壳渲染出侧边栏
        await pg.wait_for_timeout(12000)
        print(f"[probe] 打开到可用耗时 {asyncio.get_event_loop().time()-t0:.1f}s")
        print(f"[probe] title = {await pg.title()!r}")

        # 找所有 radio（Streamlit 侧边栏导航）
        radios = await pg.query_selector_all('[data-testid="stRadio"]')
        print(f"[probe] radio 数量 = {len(radios)}")
        for i, r in enumerate(radios):
            label = await r.inner_text()
            opts = await r.query_selector_all('input[type="radio"]')
            print(f"  radio[{i}] label={label.strip()[:40]!r} options={len(opts)}")
            for o in opts:
                v = await o.get_attribute("value") or await o.get_attribute("aria-label")
                print(f"      - {v!r}")

        # 找所有按钮
        btns = await pg.query_selector_all('button')
        print(f"[probe] button 数量 = {len(btns)}")
        for bt in btns[:40]:
            txt = (await bt.inner_text()).strip().replace("\n", " ")[:60]
            if txt:
                print(f"      btn: {txt!r}")

        # 页面可见文本量（判断是否真的渲染出内容）
        body = await pg.inner_text("body")
        print(f"[probe] body 可见文本长度 = {len(body)}")
        print(f"[probe] 前 400 字: {body[:400]!r}")

        print(f"[probe] JS 错误数 = {len(errs)}")
        for e in errs[:10]:
            print(f"      {e[:160]}")

        await pg.screenshot(path="资料/截图/online/_probe_home.png", full_page=False)
        await b.close()

asyncio.run(main())
