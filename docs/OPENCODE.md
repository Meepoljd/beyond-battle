# OpenCode 单位行动

本接入对照 OpenCode 1.19.5 的 HTTP 接口。默认规则模式无需 OpenCode；选择模型模式需要明确配置服务、模型和每局调用次数。只向服务发送单个友方单位的观察、收到的命令、有限记忆及公开角色设定；敌方仍使用规则。模拟内核验证提案并结算，记录可用于不调用模型的重放。

## 准备

单独安装 OpenCode 1.19.5，并为游戏专用的 OpenCode 运行目录配置可用模型和供应商凭据。不要把凭据写入仓库。游戏不自动沿用其他项目的凭据。`scripts/run-opencode.sh` 默认使用 `$HOME/.local/share/beyond-battle/opencode` 下的独立工作、配置、数据和缓存目录；可用 `BATTLE_OPENCODE_HOME` 指定目录。该脚本固定加载 `config/opencode.json` 并关闭项目配置读取。应在该专用环境中通过 OpenCode 官方方式登录或设置供应商凭据。

```bash
# 示例：在游戏专用目录中运行 OpenCode 登录流程
bash scripts/run-opencode.sh auth login
# 启动仅监听本机的 OpenCode 服务
bash scripts/run-opencode.sh
```

`BATTLE_OPENCODE_BIN` 可指定现有 OpenCode 可执行文件，`BATTLE_OPENCODE_PORT` 可选择空闲端口。服务配置了单步 `battle-unit` Agent、禁止工具、禁止分享及自动压缩；每个单位、每回合再创建带全拒绝权限的新会话。应使用单独运行用户和工作目录，确保服务进程没有不需要的文件与凭据权限。服务仍保有模型供应商访问权限，需按实际部署隔离。

在另一个终端为游戏进程设置，模型名替换为实际配置：

```bash
export BATTLE_OPENCODE_URL=http://127.0.0.1:4096
export BATTLE_OPENCODE_MODEL=provider/model
export BATTLE_OPENCODE_MAX_CALLS=48
export BATTLE_OPENCODE_TIMEOUT=20
bash scripts/run.sh
```

全部前三项均已配置时，准备页面才提供 OpenCode 选项。超时允许 1–60 秒，次数允许 1–512；选择次数应结合供应商价格和允许费用。远程服务要求 HTTPS；可通过 `BATTLE_OPENCODE_USERNAME` 与 `BATTLE_OPENCODE_PASSWORD` 传递 OpenCode 的 HTTP Basic 身份，密码通过安全的运行环境注入。不要公开未认证的 OpenCode 服务。

## 约束和恢复

- 每个友方单位一次提案独立创建会话，限制一步推理并请求 JSON Schema 结构化结果；服务返回后尽力终止并删除该会话。服务宕机或网络故障时清理可能失败，需要按游戏专用工作目录检查残留。
- 每局开始时固定模型与额度。每回合先在数据库预留存活友方单位的请求数，超出额度的单位使用规则回退。中断、失败和未实际发出的预留不会自动返还，避免恢复后重复计费。次数上限不是货币上限；供应商内部重试、服务端额外请求和价格不由本程序控制，需单独设置供应商预算。
- 模型、凭据、服务或隔离 Agent 不可用、权限不符、超时、响应异常、提案非法或自主权不允许时，单位回退到规则。模型请求发生在 SQLite 写事务外，同一幂等请求复用任务；重启后未提交任务标记 interrupted，不自动重试或推进。
- 模型输入中的命令文字和经历可能含提示注入，系统提示把它们作为数据；服务端权限与输出校验是执行边界。模型仍可能产生不准确叙述，提案被校验不代表其战术合理。
- 模型对话由 OpenCode 和供应商按各自策略处理，可能留存数据或产生费用。当前不实现供应商用量结算、金额硬限额或精确取消保障。游戏记录实际用于重放的观察、提案及非敏感的用量元数据，不能当作账单。

## 验证范围

```bash
BATTLE_DB=/tmp/battle-test-bootstrap.sqlite3 .venv/bin/python -m unittest discover -s tests -v
BATTLE_DB=/tmp/battle-test-bootstrap.sqlite3 .venv/bin/python tests/e2e_opencode.py
```

自动测试使用本地模拟 OpenCode 协议服务，不请求外部模型。真实 OpenCode 1.19.5 的 Agent 配置和空会话生命周期另行验证；真实模型供应商的结构化输出、费用、超时以及 NUC 环境仍需验收。
