"""验证并替换 .env 里的智谱 API Key —— 仅用于本地联调，绝不落盘 Key 文本。

用法（Key 只走环境变量，不出现在脚本/日志/提交里）：
    NEW_ZHIPU_KEY='<key>' python scripts/_verify_and_set_key.py

行为：
1. 从环境变量 NEW_ZHIPU_KEY 读新 Key，做形态校验（必须纯 ASCII）；
2. 用应用自带 LLMClient（智谱 base_url / glm-4-flash）发一次极小真实调用；
3. 成功则把 .env 的 ZHIPU_API_KEY 行替换为新 Key，其余行不动；失败则不改 .env。
"""
import os
import sys

# 把项目根加入导入路径（脚本位于 scripts/，核心模块在仓库根的 core/）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

NEW = os.environ.get("NEW_ZHIPU_KEY", "").strip()


def scrub(text, key):
    """把任何可能出现的 Key 片段替换成 ***，防止误输出。"""
    if not key:
        return text
    return text.replace(key, "***")


def fail(msg):
    print("RESULT: FAIL")
    print("REASON: " + msg)
    sys.exit(1)


if not NEW:
    fail("未收到新 Key（请通过 NEW_ZHIPU_KEY 环境变量传入）")

if not NEW.isascii():
    fail("Key 含非 ASCII 字符，形态非法")

try:
    from core.llm import LLMClient, is_degraded
except Exception as e:
    fail("无法导入 LLMClient：%s" % e)

BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
MODEL = "glm-4-flash"
VL_MODEL = "glm-4v"

print("STEP 1: 构造客户端（base=%s, model=%s）" % (BASE_URL, MODEL))
client = LLMClient(api_key=NEW, base_url=BASE_URL, model=MODEL, vl_model=VL_MODEL)
if not client.available:
    fail("客户端不可用：%s" % scrub(str(client.key_error or "未知"), NEW))

print("STEP 2: 发起一次极小真实调用（流式不可用，这里用 chat）")
try:
    resp = client.chat(
        [{"role": "user", "content": "用一句话回答：你是谁？只说一句话。"}],
        model=MODEL, temperature=0.1,
    )
except Exception as e:
    fail("调用抛出异常：%s" % scrub(str(e), NEW))

content = getattr(resp, "content", "") or ""
if is_degraded(resp):
    print("STEP 2 响应判定为降级（伪响应），原始错误：%s"
          % scrub(str(client.last_error or ""), NEW))
    fail("Key 调用被降级，可能无效或网络不通：%s"
         % scrub(str(client.last_error or ""), NEW))

reply = content.strip()
print("STEP 2 OK: 模型返回 %d 字符" % len(reply))
print("REPLY_PREVIEW: " + scrub(reply[:80], NEW))

# ---- 把 .env 的 ZHIPU_API_KEY 行替换为新 Key ----
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if not os.path.exists(env_path):
    fail(".env 不存在，无法写入")

with open(env_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

patched = False
out = []
for line in lines:
    if line.startswith("ZHIPU_API_KEY="):
        # 保留行内注释结构但替换为新 Key（脱敏处理，仅此处写入一次）
        out.append("ZHIPU_API_KEY=%s\n" % NEW)
        patched = True
    else:
        out.append(line)

if not patched:
    out.append("ZHIPU_API_KEY=%s\n" % NEW)

with open(env_path, "w", encoding="utf-8") as f:
    f.writelines(out)

print("STEP 3: 已将 .env 的 ZHIPU_API_KEY 替换为新 Key（其余配置不动）")
print("RESULT: PASS")
