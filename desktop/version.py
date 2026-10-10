"""运行时（壳层）版本号。

壳层（desktop/、exe 本身）的版本。业务代码版本由 dist-release/version.json
下发的字段 version 决定，二者通过 min_runtime_version 约束兼容性：
- 业务包 version.json 里的 min_runtime_version 必须 <= RUNTIME_VERSION，
  否则拒绝应用该业务包，提示用户下载完整安装包。
"""

RUNTIME_VERSION = "1.0.0"
