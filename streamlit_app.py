"""Streamlit Community Cloud 入口引导文件。

为什么需要它：Streamlit Community Cloud 默认把仓库根的 `streamlit_app.py`
当作主脚本；而本项目主脚本是 `app.py`。Cloud 的部署对话框里也能手动指定入口，
但填了就多一步、且换分支容易漏；放一个引导文件更省心。

为什么用 runpy 而不是 `from app import *`：Streamlit 应用的逻辑写在**模块顶层**
（脚本自上而下执行），不是 `main()` 函数。用 `runpy.run_path(..., run_name="__main__")`
可以让 `app.py` 以 `__main__` 身份执行，模块级副作用、`__file__` 相对路径解析
（如 `demo_data/`、`static/`、`.streamlit/config.toml`）都与
`streamlit run app.py` 完全一致。

等价替代方案（二选一即可，不需要同时做）：
在 Cloud 部署对话框把「Main file path」显式填成 `app.py`，此时可删除本文件。
"""

import os
import runpy

runpy.run_path(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
    run_name="__main__",
)
