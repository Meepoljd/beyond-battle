# 云端验证报告 / 2026-11-01

环境：独立云端工作区，Python 3.12.14，FastAPI 0.141.1，SQLite，Chromium 151.0.7922.173，Playwright 1.62.0。浏览器仅访问临时 `127.0.0.1` 服务。没有 NUC 或公网部署。

## 结果

| 检查 | 状态 | 证据与范围 |
|---|---|---|
| 干净 Python 虚拟环境安装锁定依赖 | PASS | `/tmp/beyond-battle-clean-venv`，从 requirements-dev.lock 安装，无 system-site-packages |
| Python 单元/API/持久化测试 | PASS | 32 项，`python -m unittest discover -s tests -v`，原始输出见 `test-results.txt` |
| 浏览器完整一局 + 第二局 | PASS | 390×844 手机模拟与 1440×1000 桌面，真实 Chromium |
| 手机布局 | PASS | 准备、指挥、复盘无横向溢出；已目视检查截图；按钮无需悬停 |
| 断网与响应丢失 | PASS | 服务端提交后丢弃响应，刷新重试同一幂等键，只推进一次；离线操作恢复后只推进一次 |
| JS/CSP/XSS | PASS | 无页面异常与 CSP 错误；HTML 注入备注只显示文本、不创建 img 节点；浏览器同源守卫测试通过 |
| 信息隔离 | PASS | 普通 API 不返回隐藏敌军、未送达消息、决策或全知状态；进行中 replay/memory-candidates 返回 MATCH_NOT_FINISHED |
| 通信与命令 | PASS | 正常、延迟、中断恢复、静默、到期、重复送达、旧情报、优先级、替换、越级同步 |
| 规则确定性 | PASS | 固定种子 golden fixture；已记录决策再运行，事件与最终状态一致；字典顺序不产生战斗先手 |
| 完整复盘验证 | PASS | 事件增量折叠哈希 + 已记录决策重新结算；拒绝篡改哈希 |
| 并发与幂等 | PASS | 6 个相同 key 返回同一 job；不同 key 同版本最多一个提交；过时 version 拒绝 |
| 进程中断与原子性 | PASS | 提交前/提交后恢复、事务内硬退出 os._exit、插入失败回滚；不补算停机时间 |
| 记忆 | PASS | 实际收到的命令作为来源、拒绝假来源、审阅去重、停用后新局不注入；只改变报告格式 |
| 演习资产隔离 | PASS | 新局恢复初始人员与弹药；长期角色没有写入伤亡/奖励字段 |
| SQLite 备份恢复 | PASS | online backup、完整性检查、新路径读回同状态、拒绝覆盖目标；未知 schema 拒绝且不改写 |
| 模型恶意/不完整提案 | PASS（规则接口边界） | 非法动作、修改观察对象、过大文本、伪造记忆引用均回退；不代表真实供应商已测试 |
| Python 依赖一致性/语法 | PASS | pip check、compileall、node --check |
| 真实模型、供应商超时/费用/预算 | NOT RUN | 未接入，未用任何 API key |
| NUC、真机 Safari/Android、LAN 访问、常驻服务 | NOT RUN | 本轮只授权云端开发测试 |
| 3–5 位朋友试玩/趣味性验证 | NOT RUN | 附 PLAYTEST.md 供下一轮使用 |
| 容器镜像/系统资源限制/端口冲突脚本 | NOT RUN | 交付源码运行包，不安装服务 |

## 可复现玩法对照

种子 42、全部三名指挥官：

- 不干预：T7 桥梁失守。T4–T6 中断期间林岚达到兵力保全阈值，T6 离开桥头，总部在恢复后才获得更晚报告。
- 开局令陈砚 SUPPORT → bridge、保全阈值 3：T12 对手失去作战能力，守桥成功。测试覆盖该结果。
- 接受一次“撤退时先报告兵力与弹药”经历：下场相同输入下的战斗数值不变，撤退报告增加有来源的约定说明；这只是规则版沟通延续示例。

## 成本与性能样例

`rules-usage-sample.json`：本机 5 场、35 回合，提交中位耗时约 4.74 ms、最大 6.23 ms；含 SQLite journal/snapshots/decisions 的数据库约 2.54 MB。没有模型调用，模型费用为 0。这不是 NUC 容量承诺，也不代表真实模型延迟。

## 验证中发现并修复

- 替换命令比旧命令先到时，旧命令之后不能复活：队列识别 superseded 并取消。
- Provider 的表面有效但字段缺失/过长提案应回退：补齐完整输出字段与长度/记忆来源校验。
- 对手撤退原本借用了友军集结区：改为退向北岸，避免错误地向玩家后方撤退。
- 战后信息列过长：事实与总部信息分别使用有界滚动区。
- 未知数据库版本不得被首次初始化掩盖：启动前检查现有 schema，不兼容时停止。

非阻塞环境事项：Playwright 自带浏览器下载返回 403 Domain forbidden，改用已安装系统 Chromium；无需变更网络规则。TestClient 发出 httpx 弃用提示，当前锁定版本功能通过；未来升级框架时再处理。原计划文件 transfer helper 返回 download failed，但已通过 Library 完整读取全部正文，无缺失章节。
