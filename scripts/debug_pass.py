"""评审视角全功能调试：模拟评委真实操作路径，逐页走查并暴露显示/逻辑问题。

与 apptest_check 的区别：apptest 只验证"页面能渲染"，本脚本验证"评委乱点也安全"：
- 空库直接进每个页面（不点一键加载）
- 每个页面里的按钮/表单在不满足前置条件时点击，是否给出友好提示而非堆栈
- Provider 切换到无多模态(DeepSeek)后，涉及 OCR/图表理解的流程是否降级正常
- 极端输入：空问题、超长问题、非法 CSV、不存在引用编号
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest

PROBLEMS = []


def record(at, page, step):
    """收集页面上的异常/报错（AppTest: at.exception=未捕获堆栈, at.error=st.error 红框）。"""
    exc = getattr(at, "exception", None)
    if exc is not None:
        try:
            for el in exc:
                PROBLEMS.append((page, step, "UNCAUGHT", str(getattr(el, "value", el))[:200]))
        except TypeError:
            pass
    for el in at.error:
        val = str(getattr(el, "value", el))
        # 错误边界/降级提示属预期设计，仅未捕获堆栈记为问题
        if "Traceback (most recent call" in val:
            PROBLEMS.append((page, step, "TRACEBACK", val[:200]))


APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def run(page_idx, steps_fn, label):
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["p"] = str(page_idx)
    try:
        at.run()
        record(at, label, "init")
        steps_fn(at)
    except Exception:
        PROBLEMS.append((label, "crash", "HARNESS", traceback.format_exc()[-300:]))
    return at


def empty_db_walkthrough():
    """评委第一件事可能就是乱点：空库 + 未切 Provider。
    空库守卫生效时页面不会有输入控件，此时记录为「已正确拦截」。"""
    def s0(at):
        if len(at.text_area) == 0 and len(at.chat_input) == 0:
            return  # 被守卫拦截，符合预期
        try:
            if len(at.text_area):
                at.text_area[0].set_value("transformer 有几层").run()
        except Exception:
            record(at, "空库问答", "输入框异常")
        record(at, "空库问答", "直接提问")

    def s1(at):
        record(at, "空库阅读", "init")

    def s2(at):
        for b in at.button:
            if "图谱" in (b.label or "") or "生成" in (b.label or ""):
                b.click().run()
                break
        record(at, "空库图谱", "点生成")

    def s3(at):
        for b in at.button:
            lbl = b.label or ""
            if "综述" in lbl or "Future" in lbl:
                b.click().run()
                break
        record(at, "空库综述", "点按钮")

    for idx, fn, label in [
        (2, s0, "空库问答"), (3, s1, "空库阅读"),
        (5, s2, "空库图谱"), (4, s3, "空库追踪"),
    ]:
        run(idx, fn, label)


def provider_switch():
    """切到 DeepSeek（无多模态）：Key 为空应提示不可用，不应崩。"""
    def sw(at):
        at.selectbox[0].set_value("DeepSeek").run()
        record(at, "切DeepSeek", "selectbox")
        if len(at.selectbox) > 0:
            at.selectbox[0].set_value("智谱 GLM（默认）").run()
    run(0, sw, "Provider切换")


def extreme_inputs():
    """正常库 + 极端输入。"""
    def s(at):
        for b in at.button:
            if "一键加载" in (b.label or ""):
                b.click().run()
                break
        if len(at.text_input) == 0:
            return
        try:
            at.text_input[0].set_value("").run()          # 空问题
            at.text_input[0].set_value("x" * 6000).run()  # 超长问题
        except Exception:
            record(at, "极端输入", "输入框异常")
        record(at, "问答极端输入", "空/超长问题")
    run(2, s, "问答极端输入")

    def r(at):
        for b in at.button:
            if "一键加载" in (b.label or ""):
                b.click().run()
                break
        # 引用跳转：不存在的编号
        for inp in at.number_input:
            inp.set_value(9999).run()
            break
        for b in at.button:
            if "跳转" in (b.label or "") or "定位" in (b.label or ""):
                b.click().run()
                break
        record(at, "引用跳转", "不存在编号")
    run(3, r, "阅读页极端输入")


if __name__ == "__main__":
    print("== 空库乱点走查 ==")
    empty_db_walkthrough()
    print("== Provider 切换走查 ==")
    provider_switch()
    print("== 极端输入走查 ==")
    extreme_inputs()
    print()
    if PROBLEMS:
        print(f"❌ 发现 {len(PROBLEMS)} 个问题：")
        for page, step, kind, msg in PROBLEMS:
            print(f"- [{page}] {step} ({kind}): {msg}")
        sys.exit(1)
    else:
        print("✅ 评审视角乱点走查全部通过（无异常、无堆栈）")
