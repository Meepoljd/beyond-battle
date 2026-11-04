# 《战报之外》专用 Codex Cloud 开发环境

仓库侧准备已完成；可复用环境的创建/发布需要平台确认，不能从本文件的存在推断已发布。

## 要保存的配置

| 项目 | 设置 |
|---|---|
| 名称 | `beyond-battle-dev` |
| 仓库 | `Meepoljd/beyond-battle`，只选此仓库 |
| 初始分支 | `main` |
| 隐私 | Only me / 仅自己 |
| Python | 3.12 |
| 安装 | 在仓库根目录运行 `bash scripts/setup-cloud.sh` |
| 启动 | 参见下面的启动说明 |
| Secrets / 密钥 | 无；规则版不调用真实模型 |
| 网络 | 安装依赖所需包管理器访问；无需额外公网服务、隧道或全网放开 |

`setup-cloud.sh` 使用仓库内 `.venv` 安装锁定的开发依赖，执行单元/API测试、临时服务检查，以及可用系统 Chromium 的浏览器 E2E。测试数据与截图使用独立临时目录，不放入开发存档。它不会安装系统服务或修改网络设置。无浏览器时明确打印 NOT RUN，不把缺失浏览器的环境当作 UI 已验收。

2026-11-04 已在当前云任务工作区实际运行该脚本：依赖安装与 pip check、32 项单元/API测试、health/ready/首页启动检查，以及 390×844 和 1440×1000 浏览器全流程均 PASS。临时服务已由检查脚本关闭。这验证了仓库准备流程，不等同于平台环境已经发布。

## 创建/发布步骤

在网页或桌面端进入 **Settings → Codex Cloud → Environments → Create environment**，选择本仓库并点击 Get started。将下面内容交给设置会话执行：

> 为 Meepoljd/beyond-battle 准备私有开发环境，命名 beyond-battle-dev。只使用这个仓库和 Python 3.12，读取 AGENTS.md。将 `bash scripts/setup-cloud.sh` 作为安装命令执行；不要加入任何模型密钥、操作 MyAgent 或部署到 NUC。验证 32 项单元/API测试、启动检查，以及 Chromium 可用时的两种屏幕尺寸 E2E。保存经过验证的安装与启动设置，报告真实通过结果和未完成项。

检查设置报告后保存并选择 **Publish**；只有看到 **Environment published** 才表示可供后续任务复用。后续新任务选择 **Work in → Cloud → beyond-battle-dev**。这些入口与安装/启动字段依据 [OpenAI 的云环境说明](https://learn.chatgpt.com/docs/environments/cloud-environments)（2026-11-04 核对）。

## 启动说明（可填入 Start skill）

1. 进入 `Meepoljd/beyond-battle` 的实际检出目录，不假设绝对路径固定。
2. 确认 `.venv/bin/python` 可用；若依赖未准备，执行 `bash scripts/setup-cloud.sh`。
3. 运行 `.venv/bin/python scripts/check-startup.py` 检查隔离数据库下的 health、ready 和首页；该检查会自行停止测试服务。
4. 需要查看 UI 时，在前台终端运行 `bash scripts/run.sh`，浏览器使用 `http://127.0.0.1:8765`。端口冲突时选择另一个 `BATTLE_PORT`，不要终止不认识的服务。
5. 默认开发存档位于 `data/battle.sqlite3`。关闭游戏进程不会自动推进或清空存档。没有 UI 任务时无需后台常驻服务。

## 持久性与开发方式

源码及环境准备说明保存在私有 GitHub 仓库。平台发布的环境配置供新任务使用，每个任务拥有自己的工作区；游戏存档与未提交文件不要当作跨任务自动共享的永久数据。后续功能开发优先使用功能分支和 PR，测试结果随提交说明记录。

此次已有任务环境与“新建并发布 beyond-battle-dev”是不同状态。当前会话没有环境创建/发布工具，因此仓库准备完成后仍需上述平台操作；未改变已有环境关联的 MyAgent 配置。
