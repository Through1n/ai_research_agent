"""启动预热：把冷启动成本移出用户可见的首帧路径（部署辅助脚本）。

解决的问题（话术 19 白屏根因的部署侧闭环）
------------------------------------------
线上冷启动最贵的一项成本是 chroma 的 ONNX embedding 模型：
services/state.py 的 _build_embed_fn() 在冷缓存下会同步从
chroma-onnx-models.s3.amazonaws.com 拉 ~83MB 的 onnx.tar.gz，境内网络
"连接建立但 body 传输挂死"（实测 5s 0 字节）。Docker 镜像在第 5 层
构建期已把它烤进 /home/app/.cache，但**非 Docker 的发布平台**
（`sh start_app.sh` 直接跑的沙箱）没有那一层，只能靠：
  ① 随包 onnx_models/ 预置（start_app.sh 已做，拷 6 个文件）；或
  ② 本脚本在 exec streamlit **之前**预热（下载一次，落盘后跨进程共享）。
模型缓存落在 $HOME/.cache/chroma/onnx_models，是**磁盘持久**的——
这正是它值得单独预热的原因： subsequent 进程（streamlit 自己）直接命中。

为什么默认不预热「导入链」
--------------------------
chromadb/pandas/plotly/fitz 首次 import 约 2-4s，但它在**单独进程**里预热
对总耗时是净负的：streamlit 是单进程多会话，导入成本每进程只付一次；
预热进程先付一遍全量（编译+执行），streamlit 再付一遍执行（.pyc 只省掉
编译、不省解析与执行）。只有"平台会频繁重启服务进程"的平台才划得来，
故做成 opt-in 开关 --imports，默认关闭。

用法
----
    python scripts/_prewarm.py              # 预热 ONNX 模型（已就绪则秒退）
    python scripts/_prewarm.py --demo       # 追加把 3 篇演示数据写进文献库
    python scripts/_prewarm.py --imports    # 追加预热 app 导入链（见上，默认关）

环境变量
--------
    PREWARM_MODEL_TIMEOUT  模型预热子进程超时秒数（默认 180）。超时即杀子进程，
                          绝不把启动壳拖死（应用层有有界等待 + 降级兜底）。
    AI4S_ROOT              项目根目录（默认取本脚本的上级目录）。

退出码
------
    0  模型已就绪（原本就绪或本次预热成功）；--demo 失败不影响退出码（与应用层
       「预加载失败不阻断启动」的口径一致，详见 Dockerfile.quick 第 99-100 行）。
    1  模型未能就绪（chromadb 缺失 / 下载超时 / 校验文件不全）。
       调用方（start_app.sh）以 `|| echo 警告` 包住，不阻断应用启动。

安全边界：不读取、不回显任何 API Key；唯一外网访问是 chroma 官方的
AWS S3 模型下载（与 Dockerfile 第 5 层同一个 URL，构建期同款机制）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# chromadb 的 _download_model_if_not_exists 只认这 6 个文件（与 Dockerfile 第 5 层一致）
ONNX_REQUIRED = ("config.json", "model.onnx", "special_tokens_map.json",
                 "tokenizer_config.json", "tokenizer.json", "vocab.txt")

# app.py 依赖导入块的模块清单（import 失败只警告，不阻断——与 app.py 的
# try/except 兜底语义一致）。仅在 --imports 时使用。
APP_IMPORTS = (
    "services.state", "services.library", "services.graph", "services.writing",
    "services.track", "core.llm", "core.memory", "core.providers",
    "ui.common", "ui.sidebar", "ui.desktop_chrome",
    "ui.page_agent", "ui.page_library", "ui.page_qa", "ui.page_reading",
    "ui.page_search", "ui.page_graph", "ui.page_writing", "ui.page_misc",
)


def _root() -> Path:
    return Path(os.environ.get("AI4S_ROOT") or Path(__file__).resolve().parent.parent)


def _say(msg: str) -> None:
    print(msg, flush=True)


def _onnx_dir():
    """chroma 的 ONNX 模型缓存目录（import 失败返回 None）。"""
    try:
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
            ONNXMiniLM_L6_V2,
        )
        return Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / "onnx"
    except Exception as e:                                   # chromadb 未装/版本变
        _say(f"[prewarm] ⚠️ 无法定位 ONNX 模型缓存目录：{e}")
        return None


def _model_ready(d: Path) -> bool:
    return all((d / f).is_file() for f in ONNX_REQUIRED)


def _warm_model(timeout: float) -> bool:
    """在子进程里加载 ONNX 模型（带硬超时）。

    用子进程而不是进程内直调：chroma 的下载重试没有总时长上限，进程内调用
    一旦遇挂死网络无法被安全打断；子进程可以超时即杀，启动壳不被拖死。
    命令行与 Dockerfile 第 5 层构建期预热同款（同一条代码路径，避免两套逻辑）。
    """
    code = (
        "from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import "
        "ONNXMiniLM_L6_V2 as M; M()(['warmup'])"
    )
    _say(f"[prewarm] 正在预热 ONNX embedding 模型（超时 {int(timeout)}s）…")
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        _say(f"[prewarm] ⚠️ 模型预热超时（{int(timeout)}s）——已放弃，"
             f"应用将走后台预热 + 有界等待（可能较慢，但不会白屏）")
        return False
    except Exception as e:
        _say(f"[prewarm] ⚠️ 模型预热子进程启动失败：{e}")
        return False

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = tail[-1][:200] if tail else "(无输出)"
        _say(f"[prewarm] ⚠️ 模型预热失败：{detail}")
        return False
    _say(f"[prewarm] ✓ ONNX 模型加载完成，耗时 {time.time() - t0:.1f}s")
    return True


def _warm_imports() -> None:
    """预热 app 导入链（opt-in，见模块 docstring 的净负收益分析）。"""
    root = str(_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    ok, fail = 0, []
    for mod in APP_IMPORTS:
        t0 = time.time()
        try:
            __import__(mod)
            ok += 1
            _say(f"[prewarm]   import {mod:<18} {time.time() - t0:.2f}s")
        except Exception as e:
            fail.append(mod)
            _say(f"[prewarm]   ⚠️ import {mod} 失败：{str(e)[:120]}")
    _say(f"[prewarm] 导入链预热：{ok} 成功 / {len(fail)} 失败"
         + (f"（{', '.join(fail)}）" if fail else ""))


def _preload_demo() -> None:
    """把 DEMO_PAPERS 写进文献库（幂等）。

    逻辑与 ui/sidebar.py 的「🚀 一键加载演示数据」按钮及 Dockerfile.quick 的
    _quick_load_demo.py 对齐：`if fn not in ctx.files` 判重，已加载不重复写；
    失败只告警——应用层按钮仍是兜底。
    """
    root = str(_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from services.state import Container
        from services import library
        from config import DEMO_PAPERS
    except Exception as e:
        _say(f"[prewarm] ⚠️ 演示数据预加载跳过（模块导入失败）：{str(e)[:150]}")
        return

    ctx = Container()
    added, failed = 0, []
    for fn, info in DEMO_PAPERS.items():
        if fn in ctx.files:
            continue
        try:
            library.add_text_corpus(ctx, fn, info.get("text", ""), info)
            added += 1
        except Exception as e:
            failed.append(f"{fn}: {str(e)[:100]}")
    _say(f"[prewarm] 演示数据预加载：新增 {added} 篇，失败 {len(failed)} 篇，"
         f"库内现有 {len(ctx.files)} 篇"
         + (f"；失败明细：{'；'.join(failed[:3])}" if failed else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="AI4S 启动预热（ONNX 模型 / 演示数据 / 导入链）")
    ap.add_argument("--demo", action="store_true",
                    help="追加预加载 3 篇演示数据进文献库（幂等）")
    ap.add_argument("--imports", action="store_true",
                    help="追加预热 app 导入链（默认关闭，见模块 docstring）")
    ap.add_argument("--model-timeout", type=float,
                    default=float(os.environ.get("PREWARM_MODEL_TIMEOUT", "180")),
                    help="模型预热超时秒数（默认 180，可用环境变量覆盖）")
    args = ap.parse_args()

    d = _onnx_dir()
    if d is None:
        _say("[prewarm] ✗ chromadb 不可用，跳过模型预热")
        return 1

    if _model_ready(d):
        _say(f"[prewarm] ✓ ONNX 模型已就绪（跳过下载）：{d}")
    elif not _warm_model(args.model_timeout):
        pass  # 已打印警告；退出码由下面的就绪复核决定
    else:
        _say(f"[prewarm] ✓ ONNX 模型已写入缓存：{d}")

    model_ok = _model_ready(d)
    if not model_ok:
        miss = [f for f in ONNX_REQUIRED if not (d / f).is_file()]
        _say(f"[prewarm] ✗ 模型仍未就绪，缺失：{', '.join(miss)}")
        _say("[prewarm] 🩺 应用不会因此白屏（后台预热 + 有界等待 + 降级），"
             "但向量功能会变慢或降级。可检查网络后重跑本脚本。")

    if args.imports:
        _warm_imports()
    if args.demo:
        _preload_demo()

    _say(f"[prewarm] 完成：模型 {'就绪' if model_ok else '未就绪'}")
    return 0 if model_ok else 1


if __name__ == "__main__":
    sys.exit(main())
