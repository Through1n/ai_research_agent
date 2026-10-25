# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MoreHand（桌面壳 + 初始业务 bundle）。

注意：exe 名 / 目录名仍是 AI4SResearchAgent —— 它是**内部标识**（%APPDATA% 目录、
环境变量前缀、更新包定位都靠它），改名会搬走用户已有文献库，不随品牌改名一起动。

布局（PyInstaller 解包后 _MEIPASS 内）：
    AI4SResearchAgent.exe
    desktop/                壳层（路径/启动器/窗口/updater/about/version）
    assets/icon.ico         窗口图标
    app_bundle/             初始业务代码（首次启动复制到 %APPDATA%/.../app/ 后热更新）
        app.py
        config.py
        .env.example
        requirements.txt
        ui/ services/ core/ tools/ lib/ demo_data/ .streamlit/
"""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)

block_cipher = None

PROJECT = Path(SPECPATH)  # noqa: F821  (PyInstaller 注入 SPECPATH)

# ---------------------------------------------------------------- 业务代码 datas（热更新 bundle）

BUNDLE_ITEMS = [
    ("app.py", "app_bundle"),
    ("config.py", "app_bundle"),
    (".env.example", "app_bundle"),
    ("requirements.txt", "app_bundle"),
    ("ui", "app_bundle/ui"),
    ("services", "app_bundle/services"),
    ("core", "app_bundle/core"),
    ("tools", "app_bundle/tools"),
    ("lib", "app_bundle/lib"),
    ("demo_data", "app_bundle/demo_data"),
    (".streamlit", "app_bundle/.streamlit"),
    # 前端静态库（mermaid.min.js 等）：随软件本地分发，运行时零外网依赖（/app/static/...）
    ("static", "app_bundle/static"),
]

datas = []
for src, dest in BUNDLE_ITEMS:
    src_path = PROJECT / src
    if not src_path.exists():
        print(f"[spec] WARN: bundle item missing, skip: {src}")
        continue
    datas.append((str(src_path), dest))

# 窗口图标打到根目录
datas.append((str(PROJECT / "assets" / "icon.ico"), "."))

# ---------------------------------------------------------------- streamlit / chromadb 数据文件

# streamlit 静态资源、frontend 等必须全部收集
_streamlit_datas, _streamlit_bin, _streamlit_hidden = collect_all("streamlit")
datas += _streamlit_datas

# 强制锁定 conda 环境自带的匹配版本 OpenSSL DLL。
# PyInstaller 会从 PATH 上误抓其它程序（Git/ucrt64 等）自带的旧版 libssl/libcrypto，
# 与为本环境编译的 _ssl.pyd 版本不符，运行时报
# "DLL load failed while importing _ssl: 找不到指定的程序"。
# 放在 binaries 列表末尾以覆盖自动收集到的错误版本（目标目录 '.' = _internal 根）。
_conda_bin = Path(sys.base_prefix) / "Library" / "bin"
force_binaries = []
for _dll in ("libssl-3-x64.dll", "libcrypto-3-x64.dll"):
    _p = _conda_bin / _dll
    if _p.exists():
        force_binaries.append((str(_p), "."))
        print(f"[spec] pin OpenSSL DLL: {_p}")
    else:
        print(f"[spec] WARN: conda OpenSSL DLL missing: {_p}")

# chromadb / 本地向量库相关：这些包大量使用 importlib 动态加载（telemetry、
# distance_functions、embedding function、onnx/tokenizers 后端），PyInstaller 静态
# 分析扫不到，必须 collect_all 把全部子模块 + 数据 + 原生 DLL 收进来，否则冻结态报
# "No module named 'chromadb.telemetry.product.posthog'" 等错误。
_extra_hidden = []
_extra_binaries = []
for _pkg in ("chromadb", "onnxruntime", "tokenizers", "opentelemetry", "posthog"):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        _extra_binaries += _b
        _extra_hidden += _h
        print(f"[spec] collect_all({_pkg}): datas={len(_d)} bin={len(_b)} hidden={len(_h)}")
    except Exception as _e:
        print(f"[spec] skip collect_all({_pkg}): {_e}")

# webview 后端子模块（edgechromium / winforms 等）
webview_hidden = collect_submodules("webview")

# ---------------------------------------------------------------- hiddenimports

hiddenimports = (
    _streamlit_hidden
    + _streamlit_hidden  # 防重复，去重在 Analysis 内部处理
    + webview_hidden
    + _extra_hidden
    + [
        # 业务代码直接 import 的第三方
        "fitz",  # pymupdf
        "pymupdf",
        "PIL",
        "PIL.Image",
        "numpy",
        "pandas",
        "plotly",
        "plotly.express",
        "plotly.graph_objects",
        "plotly.io",
        "networkx",
        "openai",
        "dotenv",
        "psutil",
        "chromadb",
        "chromadb.utils",
        "chromadb.utils.embedding_functions",
        # updater / launcher 用到的
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr",            # pythonnet
        "pythonnet",
        # 业务代码可能 lazy import 的
        "arxiv",
        "rank_bm25",
        # streamlit 运行时需要
        "streamlit.web.cli",
        "streamlit.runtime.scriptrunner.magic_funcs",
    ]
)

# ---------------------------------------------------------------- excludes

excludes = [
    "tkinter",
    "matplotlib",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "IPython",
    "notebook",
    "jupyter",
    "IPython.core",
    "pytest",
    "sphinx",
]

# ---------------------------------------------------------------- Analysis

a = Analysis(
    ["desktop/__main__.py"],
    pathex=[str(PROJECT)],
    binaries=_streamlit_bin + _extra_binaries + force_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI4SResearchAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # windowed，无控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT / "assets" / "icon.ico"),
    version="version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AI4SResearchAgent",
)
