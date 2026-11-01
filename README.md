# 战报之外 · 守桥演习 Demo

一个可以完整游玩的单人规则版：选择指挥官、下令、手动推进、接收延迟战报、战后对照事实，再审阅并保留沟通经历。当前 **没有调用大模型**，也没有接入 NUC、公网或其他项目服务。

## 启动（Python 3.12）

```bash
cd /workspace/beyond-battle-demo
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
bash scripts/run.sh
```

浏览器打开 `http://127.0.0.1:8765`。源码包解压到其他位置时，把第一行换成实际项目目录。前端不需要 Node、npm、CDN 或构建步骤。依赖安装后，规则演习不需要网络。

`scripts/run.sh` 只监听回环地址、只启一个 worker。默认数据库为项目内 `data/battle.sqlite3`，首次启动建表。指定独立数据位置与未占用端口：

```bash
BATTLE_DB=/absolute/game-data/battle.sqlite3 BATTLE_PORT=8766 bash scripts/run.sh
```

Ctrl+C 停止。再次启动会保留已提交回合与经历；未完成 job 标记 `interrupted`，不会按停机时间追赶。浏览器保留当前演习 ID 和在途请求的幂等键；刷新会重试同一个请求。不要同时运行两个进程指向同一个数据库（启动恢复按单进程设计）。

## 第一局怎么试

默认种子 42、三名指挥官、最多 16 回合，每回合 5 分钟战场时间。

1. 进入演习，为某位下属选择行动、目标、优先级、保全阈值和有效期，发送任务。
2. 只有送达的命令才能生效。提交、执行、回执是不同时间，页面不会把“提交成功”当成“已执行”。
3. 点击推进，下属依原任务自主行动。第 4–6 回合通信中断，积压报告在恢复后送达；第 9–10 回合发送的消息增加延迟。
4. 默认不干预会在 T7 失守。作为对照，在开局让陈砚 `SUPPORT → bridge`、保全阈值设为 3，种子 42 下可在 T12 守桥成功。不是所有随机种子都保证同一结果。
5. 结束后用复盘滑块对照真实事件与当时已到总部的报告。事件重放和已记录决策再模拟都做 SHA-256 校验。
6. 下过“撤退时先报告兵力与弹药”且实际送达的命令可以产生一条沟通记忆。玩家确认后，下场撤退报告会引用该约定；不增加兵力、命中或装备。角色详情可停用；停用从新演习生效，进行中演习使用创建时的记忆快照。

自由文字是备注。规则版执行结构化选项，**不会理解任意自然语言命令**。`HOLD/OBSERVE/REST` 原地进行，目标区域对这三类行动不产生移动。

## 检查与测试

```bash
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m unittest discover -s tests -v
CHROMIUM_PATH=/usr/bin/chromium .venv/bin/python tests/e2e_browser.py
```

E2E 启动临时回环服务、临时数据库并自行关闭；默认优先使用系统 Chromium。没有系统浏览器时，可自行安装 Playwright Chromium 后不设 `CHROMIUM_PATH`。本环境 Playwright 浏览器下载站返回 403，已用预装 `/usr/bin/chromium` 完成验证。截图只用于本地检查，默认写到 `/tmp/beyond-battle-screenshots`。

当前测试结果见 [docs/TEST_REPORT.md](docs/TEST_REPORT.md)。API 契约是 [contracts/openapi.json](contracts/openapi.json)，也可从运行中的 `/openapi.json` 获取。为保持严格 CSP，框架默认 `/docs` 的外部 CDN UI 不作为交付界面使用。

## 实现结构

| 路径 | 职责 |
|---|---|
| `server/domain.py` | 初始状态、规范哈希、Agent 观察、白名单玩家投影 |
| `server/agents.py` | 无 I/O 的 Provider 协议、规则决策、输出验证 |
| `server/simulation.py` | 消息队列、到达/过期、同时移动与伤害、报告、结束条件 |
| `server/storage.py` | SQLite schema v1、短事务、幂等请求、job、记忆审阅 |
| `server/replay.py` | 使用记录决策重算每个已提交操作，比较事件和状态 |
| `server/api.py` | FastAPI DTO、错误码、接口和静态页面 |
| `web/` | 手机优先原生 HTML/CSS/JS；不执行用户 HTML |
| `scenarios/bridge_v1.json` | 地图、回合、兵力、角色、通信配置 |
| `tests/golden_seed42.json` | 固定规则/种子的回合状态哈希与结局 |
| `scripts/backup.py` | SQLite online backup，不覆盖现有目标 |

配置修改后应提升规则/场景版本并更新明确审阅过的 fixture。旧局携带场景快照，但当前没有多版本内核路由；升级可能改变旧局的再模拟结果。保留原应用版本与数据库备份，不能直接把新规则跑在旧存档上。

## 备份、恢复、迁移与部署边界

在线备份使用 SQLite backup API，避免只复制主文件而漏掉 WAL：

```bash
.venv/bin/python scripts/backup.py data/battle.sqlite3 /absolute/backups/battle-20261101.sqlite3
```

备份文件已存在时失败，不覆盖。恢复时先停止本游戏进程，将应用切回匹配版本，然后用 `BATTLE_DB=/absolute/backups/battle-20261101.sqlite3 bash scripts/run.sh` 指向确认要恢复的副本。建议再复制备份到新的工作数据路径，保留原始备份。不要删除旧数据修复启动。

目前只有 schema v1 的首次建表，没有后续升级迁移。将来的 schema 变更必须加显式版本迁移与旧数据测试。部署操作说明见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。本次没有部署到 NUC，没有建立公网链接；云端回环 URL 不能直接从用户手机访问。

## 已知限制和后续工作

- P3 真实模型未接入：没有供应商适配器、API key、计费、预算预留、超时/重试或真实模型提示词。`AgentProvider` 和有界提案校验是预留边界。供应商与每局预算需另行确认。
- 单用户实例，无账户和用户隔离。严格同源检查和 CSP 只是本地原型的基础保护，不是公网多用户鉴权方案。
- 用版本化 JSON aggregate 保存 orders/messages/观察，另有事件、快照、决策、jobs、memories/reviews 表；未照计划把每个聚合子对象拆成关系表。事件 journal 是复盘来源，matches/snapshots 是读取缓存。
- 规则版每回合计算很短：先落 running job，在写事务外计算，再原子提交；POST 返回 202 和已完成 job。没有后台任务队列。进程中断会显式标记，用户选择重新推进，不自动恢复未提交计算。
- 规则版提案无任意工具权限；没有完整自然语言意图、动态完成条件和任意约束解释。越级成本先表现为上级同步延迟，不模拟独立上级再决策。
- 命令静默目前限制常规战报发送，回执仍可发送。角色不捏造具体假情报；周野的敌军人数估计偏乐观，所有人都可能只有过时报告。
- `REST` 恢复士气、降低疲劳，不补弹。疲劳和压制目前主要记录并展示；没有复杂命中修正、补给、俘虏或精细寻路。所有角色损失都仅在本局。
- 每局最多每角色一条合作格式记忆，注入按类别去重且每角色最多三条；暂未实现开放式总结。变化是可解释的沟通格式，不声称已验证大模型长期人格成长。
- 没有公开托管、系统服务安装、容器构建、移动真机/NUC 验收或朋友试玩。源码运行包已经可用。
- Access log 为 uvicorn 标准日志；没有长期日志轮转服务，也不保存供应商请求。开发测试数据库与截图不包含在源码包中。

原始授权计划及差异映射见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)。应用代码暂为私有原型，未自行替用户授予开源许可。第三方依赖清单见 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)。
