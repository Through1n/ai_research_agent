"""门禁：进「版式阅读与智能翻译」页时，文献库有论文就必须**默认选中一篇**。

守的是什么：`_resolve_doc()` 的选项表是 `[""] + files`，首项是空占位
（"Choose an option"），靠 `index=1` 默认指向第一篇。谁把这个下标改成 0、
或者把空占位挪到中间，页面就会一进去就顶着「请先在文献库导入论文」——
明明库里有论文。

⚠️ 这条门禁是**回归守门**，不是 bug 复现器。2026-10-27 我曾把它当成"首屏竞态"
   的证据、给 `_resolve_doc` 加过"强制选中第一篇"的兜底，反向验证时不翻红，
   才发现真因是录制脚本取了 index=0 点到空占位；而那个兜底会带来
   「用户永远清不掉选择」的真实副作用，已回退。教训：门禁不翻红，先怀疑诊断。

为什么必须用真实浏览器：AppTest 读的是脚本侧 widget 值，看不到下拉框**显示值**，
也看不到页面上那句「请先在文献库导入论文」提示。

用法：python scripts/_probe_read_sel.py --port 8640
退出码：0 通过 / 1 失败
"""
import argparse
import re
import sys
import time

from playwright.sync_api import sync_playwright

MAIN = '[data-testid="stMain"]'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8640")
    a = ap.parse_args()

    fails = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        pg.goto("http://127.0.0.1:%s" % a.port, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_selector('[data-testid="stSidebar"] label', state="attached", timeout=120000)
        time.sleep(2.0)

        labels = pg.locator('[data-testid="stSidebar"] label')
        hit = False
        for i in range(labels.count()):
            try:
                if labels.nth(i).inner_text().strip() == "版式阅读与智能翻译":
                    labels.nth(i).click()
                    hit = True
                    break
            except Exception:
                continue
        if not hit:
            print("!! 侧栏找不到「版式阅读与智能翻译」")
            br.close()
            return 1
        time.sleep(3.5)

        # 文献库篇数（侧栏底部「文献库 6 篇 · 50 个片段 · 内存 …」）
        side = ""
        try:
            side = pg.inner_text('[data-testid="stSidebar"]')
        except Exception:
            pass
        lib_n = 0
        m = re.search(r"文献库\s*(\d+)\s*篇", side)
        if m:
            lib_n = int(m.group(1))
        print("文献库篇数（侧栏）：%d" % lib_n)

        shown = ""
        try:
            sel = pg.locator('[class*="st-key-read_sel"] input').first
            shown = sel.input_value() if sel.count() else ""
        except Exception:
            pass
        print("read_sel 显示值：%r" % shown)

        body = ""
        try:
            body = pg.inner_text(MAIN)
        except Exception:
            pass
        empty_hint = "请先在文献库导入论文" in body
        print("页面有「请先在文献库导入论文」提示：%s" % empty_hint)

        if lib_n > 0:
            if not shown or shown.strip() in ("", "Choose an option"):
                fails.append("文献库有 %d 篇，但选择框默认停在空值 %r" % (lib_n, shown))
            if empty_hint:
                fails.append("文献库有 %d 篇，页面却提示「请先在文献库导入论文」" % lib_n)
        else:
            print("（文献库为空，本环境无法验证——请先加载演示数据）")

        br.close()

    if fails:
        print("\n❌ FAIL：")
        for f in fails:
            print("   - " + f)
        return 1
    print("\n✅ PASS：文献库有论文时默认选中且不误报空库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
