# AI4S 科研助手 · 项目长期约定

## 运行环境（最容易搞错的一条）
- **真实解释器**：`C:/Users/Administrator/anaconda3/envs/ai_research/python.exe`
  （conda env `ai_research`，streamlit 1.63.0，python 3.11.16，依赖齐全含 fitz/networkx/plotly）
- 启动方式见 `启动科研助手.bat`：`conda activate ai_research` → `streamlit run app.py`
- **不要用** `C:/Users/Administrator/anaconda3/python.exe`（base 环境，缺模块）
  也不要用 `.workbuddy/binaries/python`（只能做纯语法 compile 检查）

## 验证命令基线
```bash
PY=C:/Users/Administrator/anaconda3/envs/ai_research/python.exe
$PY -m compileall -q core services tools ui app.py config.py   # 全量编译
$PY -m unittest discover -s tests                              # 应 18/18 OK
$PY scripts/_smoke_ui.py                                       # 9 页 + 9 条故障注入
$PY scripts/_smoke_read.py                                     # 阅读页 59 项
```
改完代码必须跑这四条，四条全绿才算通过。

## 架构分层
`ui/`（交互）→ `core/`（Agent 内核：router/agent/planner/llm/guardrails）
→ `services/` + `tools/`（能力与工具）→ `storage/`（运行时数据，不入库）

## 多 agent 协作约定
- 并行子 agent 必须**按文件所有权严格隔离**，prompt 里写明独占清单 + 禁止清单
- 一批最多派 4 个，超过会触发 429 频率限制
- 角色分为「考生」（改代码）与「评委」（只读取证），有意设置评审范围重叠以交叉验证
- 每轮产出写 `docs/review/round{N}_{角色}{编号}_{主题}.md`
- 要求 agent 诚实报告风险项，并声明"虚报会被抓"，实测最有效

## 当前 git 状态
- 主分支：`main`；改动前备份：`backup-20261011`（另有更早的 `backup-20261010`）
- `.gitignore` 已放行 `docs/shots/`（README 与技术文档引用这些截图，忽略会裂图）
- `.dockerignore` 阻止 `.env` 被打进镜像

## 演示前检查清单
1. `.env` 换成真实 **ASCII** API Key（含中文会触发 ascii 编码错误）
2. OpenAlex 限流：避免现场反复刷被引数查询，或填 `citation_quality.py:29` 的 `OPENALEX_MAILTO`
3. 演示路径务必真跑一遍 AppTest，不要只做失败路径的故障注入
