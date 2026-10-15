#!/bin/sh
# AI4S 科研助手 —— 沙箱启动壳（线上部署入口）
#
# 本文件是**线上可用性的全部关键**，三处修复都来自实测复盘，改前先读注释：
#
# ① PORT 兜底：沙箱未把 PORT 注入 startCmd 执行环境，`--server.port=$PORT` 会展开成
#    空串 → `Error: Invalid value for '--server.port': '' is not a valid integer`。
#
# ② enableCORS=false：**反向代理场景的必需项**。浏览器连 Streamlit WebSocket
#    （/_stcore/stream）一定带 Origin，而 Streamlit 默认 corsAllowedOrigins 只含
#    streamlit.io/app/run 与 localhost；本域名不在其中 → 403 → 页面全白、零交互、
#    但 /_stcore/health 返回 200、静态资源正常——**只看 HTTP 健康检查会误判成"部署成功"**。
#    enableCORS=False 时 is_url_from_allowed_origins() 直接返回 True，放行所有 Origin。
#    （与根目录 Dockerfile 的 CMD 保持一致；本项目无登录鉴权、无会话 Cookie，风险可接受。）
#
# ③ 预置 embedding 模型：**第二处"HTTP 健康但页面全白"的根因**。chromadb 的
#    ONNXMiniLM_L6_V2 冷缓存下会从 S3（chroma-onnx-models.s3.amazonaws.com）拉
#    83MB onnx.tar.gz；境内网络该域名"连接建立但 body 传输挂死"（实测 5s 0 字节），
#    下载永不完成。而 Streamlit 是「整段脚本跑完才发首帧」——Container.__init__
#    同步等这个下载，脚本永远跑不完 → 一帧发不出来 → 全白 + 零报错（唯一线索是
#    右上角 Stop 按钮表示"脚本仍在运行"）。
#    chroma 的跳过判据是 DOWNLOAD_PATH/onnx/ 下 6 个文件齐全，故随包发布 onnx/
#    并在这里拷进缓存目录，**一次到位、不依赖任何外网**。
#    （代码层另有后台预热兜底：services/state.py 的 _EMBED_* 一节。）
#
# 与根目录 start.sh 的区别：start.sh 是 macOS/Linux 本地交互式一键启动（含依赖安装、
# Key 检查、自动开浏览器）；本文件是**无交互**的线上部署入口，供发布平台以
# `sh start_app.sh` 调用，不读键盘、不开浏览器。
#
# Round 16 加固（两处，都不改变既有行为，只堵新变种故障）：
#   ④ PORT 数字校验：① 修的是「PORT 未注入 → 空串」。但沙箱若注入一个**非数字**
#      的 PORT（占位符、误配置），`--server.port=$PORT` 照样让 streamlit 以
#      Invalid value 退出——同一类故障的新变种，在这里一并堵掉。
#   ⑤ 预热钩子 + 启动诊断落盘：exec streamlit 之前先跑 scripts/_prewarm.py，
#      把 ONNX 模型下载（若随包预置没盖上）挪出首帧路径；启动参数写进
#      logs/start.log，云平台控制台日志被截断时仍有据可查。
#      预热失败**只告警不阻断**（`|| echo` 包裹）——应用层有后台预热 +
#      有界等待 + 降级兜底，本脚本绝不让「预热失败」升级成「应用起不来」。
set -e

# 无论以何种相对/绝对路径被调用，都先切到脚本所在目录（项目根），
# 保证后续 `./onnx_models/...` 与 `app.py` 路径稳定。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || { echo "✗ 无法进入目录：$SCRIPT_DIR"; exit 1; }

PORT="${PORT:-8501}"

# ---------- ④ PORT 数字校验（Round 16 加固，见头部注释）----------
# POSIX 兼容的数字校验：空串或含任何非数字字符 → 回落 8501 并告警。
# 不静默改：日志里能看到「PORT 值不合法」，排障时才知道平台注入了什么。
case "$PORT" in
    ''|*[!0-9]*)
        echo "[start] ⚠️ PORT 值不合法（'${PORT}'），回落默认端口 8501"
        PORT=8501
        ;;
esac

# ---------- ③ 预置 ONNX embedding 模型 ----------
# 注意：chroma 用 Path.home()/.cache/chroma/onnx_models，所以必须先确保 HOME 可写。
# 沙箱若没给 HOME 或 HOME 不可写，显式指到一个可写目录，否则模型拷不进去、
# 代码层的后台预热会退回"从 S3 下载"这条死路。
if [ -z "$HOME" ] || [ ! -w "$HOME" ]; then
    HOME="/tmp/ai4s-home"
    mkdir -p "$HOME"
    export HOME
    echo "[start] HOME 不可用，改用 $HOME"
fi

ONNX_SRC="./onnx_models/all-MiniLM-L6-v2/onnx"
ONNX_DST="${HOME}/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx"
if [ -d "$ONNX_SRC" ]; then
    if [ -f "$ONNX_DST/model.onnx" ]; then
        echo "[start] ONNX 模型已就绪（跳过预置）: $ONNX_DST"
    elif mkdir -p "$ONNX_DST" 2>/dev/null; then
        # 只拷 chroma 真正读取的 6 个文件；onnx.tar.gz 仅用于下载校验，不拷（省 83MB 传输）。
        # 注意不能用 `set -e` 下的裸 cp：单个文件失败会让整个启动壳退出，
        # 把"模型没预置上"升级成"应用起不来"。这里逐文件尝试、失败只警告。
        _ok=1
        for f in config.json model.onnx special_tokens_map.json \
                 tokenizer_config.json tokenizer.json vocab.txt; do
            if [ -f "$ONNX_SRC/$f" ]; then
                cp "$ONNX_SRC/$f" "$ONNX_DST/$f" 2>/dev/null || _ok=0
            else
                _ok=0
            fi
        done
        if [ "$_ok" = "1" ] && [ -f "$ONNX_DST/model.onnx" ]; then
            echo "[start] ONNX 模型预置完成 → $ONNX_DST"
        else
            # 预置失败不阻断启动：代码层会走后台预热 + 有界等待 + 人话降级。
            echo "[start] ⚠️ ONNX 模型预置不完整，将由应用后台加载（可能较慢）"
        fi
    else
        echo "[start] ⚠️ 无法创建模型缓存目录，将由应用后台加载（可能较慢）"
    fi
else
    echo "[start] ⚠️ 发布包内无 onnx_models/，模型将按需从网络加载"
fi

# ---------- ⑤ 预热钩子（Round 16 加固，见头部注释）----------
# 解释器探测：沙箱里 python3/python 名字不保证，谁在算谁。
# 预热脚本本身幂等：模型已就绪时秒退（Docker 镜像构建期已烤过的场景零成本）；
# 未就绪时先做 ~8s 的 S3 连通性预检，预检不过直接跳过下载（最坏只损失 ~8s），
# 绝不会把启动壳拖死在下载上。
PYW="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
if [ -n "$PYW" ] && [ -f "scripts/_prewarm.py" ]; then
    "$PYW" scripts/_prewarm.py \
        || echo "[start] ⚠️ 预热未完全成功（不阻断启动：应用后台会继续加载，向量功能可能变慢）"
else
    echo "[start] 未找到预热脚本或 Python 解释器，跳过预热（不影响启动）"
fi

# ---------- ⑤ 启动诊断落盘（Round 16 加固）----------
# 云平台控制台日志常有滚动截断，把关键启动参数追加进 logs/start.log，
# 出事回看这一行就知道「用的什么 PORT / HOME / ONNX 状态」。
# 任何一步失败都不阻断启动（|| true 全链路包裹）。
mkdir -p logs 2>/dev/null || true
{
    _ts="$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '?')"
    _pyv="$("$PYW" -V 2>&1 || echo 'python 不可用')"
    _onnx="missing"
    if [ -f "${HOME}/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx/model.onnx" ]; then
        _onnx="ready"
    fi
    echo "[${_ts}] start: port=${PORT} home=${HOME} onnx=${_onnx} cwd=$(pwd) ${_pyv}"
} >> logs/start.log 2>/dev/null || true

echo "[start] binding 0.0.0.0:${PORT}"
exec streamlit run app.py \
    --server.port="${PORT}" \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableStaticServing=true \
    --server.maxUploadSize=100 \
    --browser.gatherUsageStats=false
