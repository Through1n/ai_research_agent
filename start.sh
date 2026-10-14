#!/usr/bin/env bash
# ==============================================================================
#  AI4S 科研助手 Agent —— macOS / Linux 一键启动脚本
#
#  用法：chmod +x start.sh 然后 ./start.sh
#
#  按顺序自动完成：
#    1/6 定位项目根目录
#    2/6 检测 conda，没有 ai_research 环境就自动创建（Python 3.11）
#    3/6 安装 / 刷新 requirements.txt 依赖
#    4/6 检查大模型 API Key（只看有没有，绝不打印内容）
#    5/6 端口探测 + 自动打开浏览器
#    6/6 启动 streamlit run app.py
#
#  安全说明：本脚本不含任何真实 API Key，运行时只判断「有没有」，
#          不读取、不回显、不外传 Key 的任何内容。
# ==============================================================================

ENV_NAME="ai_research"
APP_ENTRY="app.py"
PORT="8501"
KEY_OK=0

say() { printf '%s\n' "$*"; }

say ""
say "==============================================================="
say "  AI4S 科研助手 Agent · macOS / Linux 一键启动"
say "==============================================================="
say ""

# ---------- 1/6 切到脚本所在目录（支持从任意位置调用 ./xxx/start.sh）----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || { say "✗ 无法进入目录：$SCRIPT_DIR"; exit 1; }

say "[1/6] 项目目录：$(pwd)"

if [ ! -f "$APP_ENTRY" ]; then
    say "✗ 没找到 $APP_ENTRY —— 请把本脚本放在项目根目录再运行。"
    exit 1
fi
if [ ! -f "requirements.txt" ]; then
    say "✗ 没找到 requirements.txt —— 当前目录不是完整的项目副本。"
    exit 1
fi

# ---------- 2/6 检测 conda ----------
say "[2/6] 正在检测 conda ..."

CONDA_ROOT=""
if command -v conda >/dev/null 2>&1; then
    _c="$(command -v conda)"
    if [ -n "$_c" ]; then
        CONDA_ROOT="$(cd "$(dirname "$_c")/.." && pwd)"
    fi
fi

# PATH 里找不到时，按常见安装路径兜底
if [ -z "$CONDA_ROOT" ]; then
    for cand in \
        "$HOME/anaconda3" "$HOME/miniconda3" "$HOME/opt/anaconda3" \
        "/opt/anaconda3" "/opt/miniconda3" "/opt/conda" \
        "/usr/local/anaconda3" "/usr/local/miniconda3"
    do
        if [ -f "$cand/etc/profile.d/conda.sh" ]; then
            CONDA_ROOT="$cand"
            break
        fi
    done
fi

if [ -z "$CONDA_ROOT" ]; then
    say ""
    say "✗ 没有检测到 conda，常见安装路径下也没找到。"
    say ""
    say "  🛠️ 三条路任选一条："
    say "    ① 安装 Miniconda 后重新运行本脚本："
    say "       https://docs.conda.io/projects/miniconda/"
    say "    ② 完全不想装东西：直接用在线演示地址，浏览器打开即可"
    say "       （见 docs/5分钟快速体验指南.md）"
    say "    ③ 装了 Docker："
    say "       docker build -f Dockerfile.quick -t ai4s-quick ."
    say "       docker run -p 8501:8501 ai4s-quick"
    say ""
    exit 1
fi

say "✓ 找到 conda：$CONDA_ROOT"

# shellcheck disable=SC1091
. "$CONDA_ROOT/etc/profile.d/conda.sh" || {
    say "✗ conda 初始化脚本加载失败：$CONDA_ROOT/etc/profile.d/conda.sh"
    exit 1
}

PYEXE="$CONDA_ROOT/envs/$ENV_NAME/bin/python"

if [ -x "$PYEXE" ]; then
    conda activate "$ENV_NAME" || { say "✗ 激活环境 $ENV_NAME 失败"; exit 1; }
else
    say "  未找到环境 $ENV_NAME，正在自动创建（Python 3.11，约 1-3 分钟）..."
    conda activate base >/dev/null 2>&1 || true
    conda create -y -n "$ENV_NAME" python=3.11 || {
        say "✗ 环境创建失败 —— 多半是网络问题，可换国内 conda 源后重试。"
        exit 1
    }
    if [ ! -x "$PYEXE" ]; then
        say "✗ 创建命令执行完了，但没有找到 $PYEXE"
        say "  请手动执行 conda create -n $ENV_NAME python=3.11 看具体报错。"
        exit 1
    fi
    conda activate "$ENV_NAME" || { say "✗ 激活环境 $ENV_NAME 失败"; exit 1; }
fi

PYVER="$("$PYEXE" -c 'import sys;print("%d.%d.%d" % sys.version_info[:3])')"
say "✓ 使用 Python $PYVER"

# ---------- 3/6 依赖检查与安装 ----------
say "[3/6] 正在检查依赖 ..."
if "$PYEXE" -c "import streamlit, fitz, chromadb, pandas, numpy, plotly, networkx, dotenv, rank_bm25, psutil" >/dev/null 2>&1; then
    say "✓ 依赖已就绪"
else
    say "  依赖不完整，正在按 requirements.txt 安装（首次约 2-5 分钟）..."
    "$PYEXE" -m pip install -r requirements.txt --disable-pip-version-check || {
        say ""
        say "✗ 依赖安装失败。可先换国内 pip 源再重试："
        say "  pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
        say ""
        exit 1
    }
fi

# 想强制刷新依赖：UPDATE_DEPS=1 ./start.sh
if [ "${UPDATE_DEPS:-0}" = "1" ]; then
    say "  检测到 UPDATE_DEPS=1，强制按 requirements.txt 刷新依赖 ..."
    "$PYEXE" -m pip install -r requirements.txt --disable-pip-version-check
fi

# ---------- 4/6 API Key 检查（只判断是否存在）----------
say "[4/6] 检查大模型 API Key ..."
if [ -n "${ZHIPU_API_KEY:-}" ] || [ -n "${LLM_API_KEY:-}" ]; then
    KEY_OK=1
fi
if [ -f .env ]; then
    if grep -Eq '^[[:space:]]*ZHIPU_API_KEY=.+' .env; then KEY_OK=1; fi
    if grep -Eq '^[[:space:]]*LLM_API_KEY=.+' .env; then KEY_OK=1; fi
fi

if [ "$KEY_OK" = "1" ]; then
    say "✓ 已检测到 API Key（为安全起见，此处不显示任何内容）"
    say "  提示：也可以在网页侧边栏临时填入自己的 Key，仅存于会话内存，不落盘。"
else
    say ""
    say "⚠ 没有检测到 API Key（ZHIPU_API_KEY）"
    say "  🛠️ 想用问答 / 翻译 / 写作这些大模型功能："
    say "     复制 .env.example 为 .env，在 ZHIPU_API_KEY= 后面填你的 Key"
    say "     （智谱开放平台免费注册：https://open.bigmodel.cn/ ）"
    say "  💡 没有 Key 也完全能演示：网页打开后，点侧边栏底部的"
    say "     「🚀 一键加载演示数据（v1/v7/BERT）」"
    say "     本地解析 / 混合检索 / 引用图谱 / 版本管理全部离线可用。"
    say ""
    printf '    直接回车 = 继续以离线演示模式启动，输入 q 回车 = 退出：'
    read -r GO
    if [ "$GO" = "q" ] || [ "$GO" = "Q" ]; then
        exit 0
    fi
fi

# ---------- 5/6 端口 + 自动打开浏览器 ----------
say "[5/6] 启动服务 ..."

port_free() {
    "$PYEXE" - "$1" <<'PYEOF'
import socket, sys
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except Exception:
    sys.exit(0)
finally:
    s.close()
sys.exit(1)
PYEOF
}

P="$PORT"
while [ "$P" -le 8510 ]; do
    if port_free "$P"; then
        break
    fi
    P=$((P + 1))
done
PORT="$P"
URL="http://localhost:$PORT"

if [ "$PORT" != "8501" ]; then
    say "⚠ 8501 已被占用，自动改用端口 $PORT"
fi

say ""
say "🚀 正在启动 AI4S 科研助手：$URL"
say "  浏览器会自动打开。用完按 Ctrl+C 停止服务。"
say "  首次打开需要几秒预热，请稍等。"
say ""

open_browser_later() {
    sleep 6
    case "$(uname -s)" in
        Darwin*) open "$URL" 2>/dev/null || true ;;
        *)       xdg-open "$URL" 2>/dev/null || true ;;
    esac
}
open_browser_later &

# ---------- 6/6 启动 streamlit ----------
exec "$PYEXE" -m streamlit run "$APP_ENTRY" \
    --server.port="$PORT" \
    --server.headless=true \
    --browser.gatherUsageStats=false
