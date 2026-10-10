"""关于页常量：应用名、版本、团队、主页与版权文案（纯字符串，无副作用）。"""

APP_NAME = "AI4S 科研助手"
APP_ENGLISH_NAME = "AI4SResearchAgent"
APP_VERSION = "v1.0"
TEAM = "AI4S 科研助手团队"
HOMEPAGE_URL = "https://github.com/AI4SResearchAgent/AI4SResearchAgent"
LICENSE = "MIT"

ABOUT_TEXT = (
    f"{APP_NAME}（{APP_ENGLISH_NAME}）{APP_VERSION}\n"
    f"{TEAM}\n"
    f"{HOMEPAGE_URL}\n"
    "\n"
    "技术栈：Streamlit + pywebview（WebView2 / Edge Chromium 后端）\n"
    "本机启动后监听 127.0.0.1 动态端口，以原生窗口承载，不弹出浏览器。\n"
    f"许可证：{LICENSE} License。\n"
    "\n"
    "本软件按“现状”提供，不附带任何明示或默示担保。"
)
