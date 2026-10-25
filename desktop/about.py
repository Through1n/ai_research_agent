"""关于页常量：应用名、版本、团队、主页与版权文案（纯字符串，无副作用）。

注意：桌面壳不导入业务包（config.py），所以品牌名在这里**必须自带一份**。
改品牌名要同时改：config.py 的 BRAND / BRAND_TAGLINE、本文件 APP_NAME。
内部标识（AI4SResearchAgent，用于 exe 名 / %APPDATA% 数据目录 / 环境变量前缀）
保持不变，避免搬走用户已有的文献库与配置。
"""

APP_NAME = "MoreHand"
APP_ENGLISH_NAME = "MoreHand"
APP_VERSION = "v1.0"
TEAM = "MoreHand 团队"
HOMEPAGE_URL = "https://github.com/Through1n/ai_research_agent"
LICENSE = "MIT"

ABOUT_TEXT = (
    f"{APP_NAME} {APP_VERSION}\n"
    f"{TEAM}\n"
    f"{HOMEPAGE_URL}\n"
    "\n"
    "技术栈：Streamlit + pywebview（WebView2 / Edge Chromium 后端）\n"
    "本机启动后监听 127.0.0.1 动态端口，以原生窗口承载，不弹出浏览器。\n"
    f"许可证：{LICENSE} License。\n"
    "\n"
    "本软件按“现状”提供，不附带任何明示或默示担保。"
)
