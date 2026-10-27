"""真实浏览器门禁：问答页「点胶囊 → 填入 → 提问 → 出结果」+「生成期间可切页」。

AppTest 读的是脚本侧 widget 值，看不见前端"表单态"与主线程阻塞，本文件是这类
缺陷唯一的把关者。逐项断言（任一失败 exit 1）：

  A. 点胶囊 → 输入框 DOM value 真的变成示例问题；
  B. 提问 → 若干秒内出现🙋历史区 + 答案来源 + 引用校验通过；
  C. **提交成功后**再点同一胶囊 → 仍能填入（st.form 表单态缺陷回归）；
  D. 空问题提交 → 有可见反馈（不是纯静默）；
  E. 生成期间切换页面 → 侧栏导航立即响应（主线程阻塞回归，用户反馈
     「切到别的页面会卡顿，切不出去」）；
  F. 逐页切换耗时清单。

用法：python scripts/_probe_qa_dom.py --port 8616 [--shot 名字.png]
"""
import argparse
import os
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(ROOT, "资料", "截图")
EX0 = "这篇论文提出了什么模型？"
QA_LABEL = "跨文献问答与溯源"

PILLS = '[data-testid="stButtonGroup"] button'
INPUT = 'input[aria-label="请输入问题"]'
SUBMIT = 'button:has-text("提问")'
MAIN = '[data-testid="stMain"]'

problems = []


def _fail(tag, msg):
    print("    ❌ %s：%s" % (tag, msg))
    problems.append((tag, msg))


def _ok(tag, msg=""):
    print("    ✅ %s%s" % (tag, ("：" + msg) if msg else ""))


def _wait_for(pg, sel, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pg.locator(sel).count() > 0:
                return time.time() - t0
        except Exception:
            pass
        time.sleep(0.3)
    return -1.0


def _settle(pg, quiet=1.2, timeout=180):
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


def _goto(pg, label, timeout=180):
    """点侧栏导航某一项，返回耗时（秒）；找不到返回 -1。"""
    t0 = time.time()
    labels = pg.locator('[data-testid="stSidebar"] label')
    for i in range(labels.count()):
        try:
            if labels.nth(i).inner_text().strip() == label:
                labels.nth(i).click()
                _settle(pg, quiet=0.8, timeout=timeout)
                return time.time() - t0
        except Exception:
            continue
    return -1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8616")
    ap.add_argument("--shot", default="")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}"

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        dt = _wait_for(pg, '[data-testid="stSidebar"] label', 240)
        print("[首屏] 侧边栏可见 %.1fs" % dt)
        _settle(pg)

        dt = _goto(pg, QA_LABEL)
        print("[切页] 进入问答页 %.1fs" % dt)
        if _wait_for(pg, INPUT, 120) < 0:
            _fail("前置", "找不到问题输入框")
            b.close()
            return 1
        ti = pg.locator(INPUT).first

        # ---------------- A 首次点胶囊 ----------------
        print("\n[A] 点示例问题胶囊 → 输入框填入")
        if _wait_for(pg, PILLS, 60) < 0:
            _fail("A", "找不到示例问题胶囊")
            b.close()
            return 1
        pg.locator(PILLS).first.click()
        _settle(pg, quiet=1.2)
        v1 = ti.input_value()
        if v1 == EX0:
            _ok("A 填入", "input.value = %r" % v1)
        else:
            _fail("A 填入", "input.value = %r（期望 %r）" % (v1, EX0))
        if a.shot:
            pg.screenshot(path=os.path.join(SHOT_DIR, a.shot), full_page=False)

        # ---------------- B 提问出结果 + E 生成期间切页 ----------------
        print("\n[B] 点「提问」→ 后台生成 → 答案交付")
        t3 = time.time()
        pg.locator(SUBMIT).first.click()
        # E：生成期间切页（用户反馈「切不出去」的回归点）
        time.sleep(1.0)
        dt_nav = _goto(pg, "文献库管理", timeout=60)
        if dt_nav < 0:
            _fail("E 切页", "生成期间找不到导航项")
        elif dt_nav > 20:
            _fail("E 切页", "生成期间切页耗时 %.1fs（>20s，主线程仍被阻塞）" % dt_nav)
        else:
            _ok("E 切页", "生成期间切到「文献库管理」仅 %.1fs" % dt_nav)
        _goto(pg, QA_LABEL)
        # 等答案落地（后台交付后会整页重绘）
        first_seen = -1.0
        while time.time() - t3 < 300:
            try:
                txt = pg.inner_text(MAIN)
            except Exception:
                txt = ""
            if "🙋" in txt:
                first_seen = time.time() - t3
                break
            time.sleep(0.3)
        _settle(pg, quiet=1.5, timeout=120)
        body = pg.inner_text(MAIN)
        if first_seen < 0:
            _fail("B 出结果", "300s 内未出现🙋历史区")
        else:
            _ok("B 出结果", "%.1fs 后答案可见" % first_seen)
        for tag, key in (("答案来源", "答案来源"), ("引用校验", "引用校验通过")):
            if key in body:
                _ok("B " + tag, "已渲染")
            else:
                _fail("B " + tag, "未渲染")
        if "填入 API Key" in body:
            _fail("B 环境", "页面提示未配置 API Key（本次断言不成立）")

        # ---------------- C 提交后再点胶囊 ----------------
        print("\n[C] 提交成功后，再点同一胶囊")
        v_before = ti.input_value()
        for _k in (1, 2):
            pg.locator(PILLS).first.click()
            _settle(pg, quiet=1.0)
        v3 = ti.input_value()
        if v3 == EX0:
            _ok("C 再次填入", "提交前=%r → 两次点击后=%r" % (v_before, v3))
        else:
            _fail("C 再次填入", "提交前=%r → 两次点击后=%r（前端表单态缺陷回归）"
                                % (v_before, v3))

        # ---------------- D 空问题提交 ----------------
        print("\n[D] 清空后点「提问」")
        ti.click()
        pg.keyboard.press("Control+A")
        pg.keyboard.press("Delete")
        _settle(pg, quiet=0.8)
        pg.locator(SUBMIT).first.click()
        _settle(pg, quiet=1.2, timeout=120)
        body2 = pg.inner_text(MAIN)
        if "还没有输入问题" in body2:
            _ok("D 空问题反馈", "可见「还没有输入问题」提示")
        else:
            _fail("D 空问题反馈", "提交空问题后页面无任何反馈（纯静默）")

        # ---------------- F 逐页切换耗时 ----------------
        print("\n[F] 逐页切换耗时")
        for name in ("智能总助手", "文献库管理", "跨文献问答与溯源",
                     "版式阅读与智能翻译", "论文检索与追踪",
                     "引用图谱与综述", "写作辅助与可视化"):
            dt = _goto(pg, name)
            if dt < 0:
                print("    %-12s 跳过（找不到导航项）" % name)
            else:
                print("    %-12s %.1fs" % (name, dt))

        b.close()

    print("\n" + "=" * 66)
    if problems:
        print("❌ 问答页真实浏览器门禁未通过：%d 处" % len(problems))
        for tag, msg in problems:
            print("   - %s：%s" % (tag, msg))
        return 1
    print("🎉 问答页真实浏览器门禁全绿（胶囊填入 / 出结果 / 再填入 / 空问题反馈 / 生成期间可切页）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
