# Task Harbor · 自动化任务管理

Task Harbor 是运行在本机的自动化任务管理平台。通过网页管理多个脚本的执行时间、运行状态和配置，适合每日签到、周期性脚本和其他重复任务。内置 WorkBuddy 每日签到和 Edge Microsoft Rewards 插件。

## 功能

- **任务管理**：新增、编辑、删除任务，单独或批量开启、关闭定时任务。
- **灵活调度**：支持手动、每日、每周、固定间隔和 Cron，可设置任务时区。
- **手动执行**：运行单个或批量任务，查看执行状态，停止任务或重新运行。
- **故障隔离**：每个脚本在独立进程中运行，单个任务出错后其他任务继续执行。
- **并发控制**：设置全局并发数量，同一任务避免重复运行，同资源组依次执行。
- **执行记录**：查看日志、耗时、结果和尝试次数，支持有限重试及错过任务补跑。
- **全局配置**：设置默认时区、调度暂停、补跑策略、重试间隔和日志保留时间。
- **运行环境**：使用 Python venv，支持本地环境变量配置，变量值保存后不回显。
- **脚本扩展**：扫描插件目录发现新任务，在界面创建、修改脚本；修改前备份，Python 脚本保存时检查语法。
- **WorkBuddy 签到**：读取本机客户端登录状态，执行每日签到，显示成功、今日已完成或需要重新登录等结果。
- **Edge Rewards**：连接日常使用的桌面 Edge，复用已有登录信息，按当天页面处理支持的积分活动与桌面搜索；默认允许 VPN 和代理。

## 部署

### 环境要求

- Windows，Python 3.11 或更新版本。
- 安装依赖时需要网络连接。
- 前端由后端直接提供，无需 Node.js 或前端构建。
- WorkBuddy 签到需要安装并登录本机 WorkBuddy 桌面客户端。

### 下载与启动

```powershell
git clone https://github.com/jackfeicoder/automation-task-manager.git
cd automation-task-manager
./scripts/setup.ps1
./scripts/start.ps1
```

安装脚本会创建 `.venv`、安装锁定依赖，并从示例配置生成本地 `.env` 文件。

启动后访问 **http://127.0.0.1:8765**。也可双击项目目录中的 `start.cmd`，首次运行时自动完成环境安装。前台启动时按 `Ctrl+C` 停止服务；服务运行期间，关闭网页不影响任务执行。

创建桌面快捷方式：

```powershell
./scripts/create_desktop_shortcut.ps1
```

双击桌面的 **Task Harbor**，自动在后台启动服务并打开管理页面；服务已经运行时直接打开页面。关闭网页后，后台任务继续运行。

### 本地配置

编辑本地 `.env` 可调整监听端口等启动配置，修改后重启服务。在网页的「全局配置」中调整调度与并发，在「运行环境」中设置任务变量。

登录信息、访问密钥及其他敏感值只保存于本机的 `.env` 或 `data/`，请勿填写到脚本源码、插件清单或示例配置中。这些本地文件已加入 Git 忽略规则，上传脚本还会检查当前文件和 Git 历史。

### 登录后自动启动

```powershell
./scripts/install_startup.ps1
```

移除自动启动：

```powershell
./scripts/install_startup.ps1 -Remove
```

电脑关机期间任务暂停，服务启动后按配置处理错过的任务。

## 使用

### WorkBuddy 每日签到

1. 打开本机 WorkBuddy 桌面客户端并登录。
2. 在「运行环境」中检查 WorkBuddy 登录状态。
3. 在任务列表中运行 WorkBuddy 签到，或开启定时执行。

默认执行时间为每天 **09:00，Asia/Shanghai**。登录过期时，在客户端重新登录后运行任务。实际积分以服务端返回结果为准，客户端接口发生变化时需要更新插件。

### Edge Microsoft Rewards

首次安装浏览器依赖（本机需已安装 Microsoft Edge）：

```powershell
./scripts/setup.ps1 -Browser
```

任务默认使用当前网络，VPN、Windows 系统代理、WinHTTP 代理和出口地区不会阻止启动。需要启用直接网络检查时，在「运行环境」设置 `REWARDS_REQUIRE_DIRECT_NETWORK=true`，并按实际所在地区设置 `REWARDS_COUNTRY_CODE`。

连接桌面 Edge：先完全退出 Edge（包括后台进程），然后在项目目录运行：

```powershell
./scripts/start_edge.ps1
```

启动脚本打开已安装的桌面 Edge，使用原来的 `User Data` 目录及最近使用的配置，复用该配置中的微软登录信息。自动化连接仅使用本机 `127.0.0.1:9222`。任务在这个 Edge 中新建工作页面，结束后关闭自己的页面并断开连接，保留浏览器及原有页面。

多个 Edge 配置时，可以显式选择已经登录的配置，例如 `./scripts/start_edge.ps1 -ProfileDirectory "Profile 1"`。关闭 Edge 后，下次运行任务前仍需用该启动脚本打开 Edge；已经按此方式打开时直接运行任务即可。

可以创建本机搜索词文件，每行填写一个需要查询的内容；不创建时任务会自动使用仓库内的三个非敏感示例词：

```powershell
New-Item -ItemType Directory -Force data/rewards
Copy-Item tasks/edge_rewards/queries.example.txt data/rewards/queries.txt
notepad data/rewards/queries.txt
```

在网页点击「扫描插件」，找到 **Edge Microsoft Rewards** 后手动运行。任务默认关闭定时；按需开启，默认时间为每天 10:00。默认每次最多搜索 10 个词，间隔 15 秒，最多尝试 30 项活动。

每次运行重新读取首页和赚取页的任务，根据当天页面处理搜索、浏览栏目、打开官方活动页面和简单任务的子步骤。已完成、尚未解锁、跨天等待以及需要安装应用、订阅、购买或兑换的任务会跳过。单项失败继续下一项；活动页面操作后立即关闭，搜索共用一个页面，运行结束关闭本任务创建的页面。

问答只按已知问题与答案匹配，未知问题跳过。可以将 `tasks/edge_rewards/quiz-answers.example.json` 复制到本机 `data/rewards/quiz-answers.json`，按「问题：答案」补充内容。投票可以在运行环境变量 `REWARDS_POLL_OPTION` 中填写要选择的完整选项文字；留空时只打开活动并核对完成状态。

搜索上限从当前积分明细读取，支持页面显示的加倍额度；达到上限、搜索词用完或等待一次后仍未确认增长，结束本轮搜索。结果只记录服务端确认的完成状态和实际余额变化，不保证每天领取所有积分。执行明细保存在本机 `data/rewards/last-run.json`，也可在网页查看任务结果和日志。

本机搜索词文件为空、格式异常或读取失败时，仅跳过补充桌面搜索，继续当天活动。Edge 缺失、连接未开放、配置未登录以及连接失败会显示对应的处理提示；网络错误只记录错误代码，不输出含登录参数的完整地址。

| 运行环境变量 | 默认值 | 用途 |
|---|---|---|
| `REWARDS_EDGE_ENDPOINT` | `http://127.0.0.1:9222` | 桌面 Edge 的本机连接地址；用启动脚本的 `-Port` 参数改变端口 |
| `REWARDS_REQUIRE_DIRECT_NETWORK` | `false` | 是否启用 VPN、代理、路由及出口地区拦截 |
| `REWARDS_COUNTRY_CODE` | `CN` | 仅启用直接网络检查时使用的地区代码 |
| `REWARDS_SEARCH_COUNT` | `10` | 每次桌面搜索数量，0 表示只处理活动 |
| `REWARDS_SEARCH_INTERVAL` | `15` | 搜索后等待秒数，范围 10–120 |
| `REWARDS_ACTION_WAIT` | `10` | 活动后等待秒数，范围 5–60 |
| `REWARDS_MAX_ACTIVITIES` | `30` | 每次活动上限，范围 0–100；0 表示只搜索 |
| `REWARDS_POLL_OPTION` | 空 | 投票时选择的完整选项文字，未匹配则跳过 |

可单独运行 `--doctor` 检查浏览器依赖、桌面 Edge 连接和网络检查开关，不访问微软网站。默认不查询出口地区；开启直接网络检查后，才使用两个外部 IP 地区服务核对 IPv4 / IPv6 出口。

网站显示的登录、地区限制或验证提示仍会记录为需要处理。实际活动资格和积分以微软页面为准，详见 [Microsoft Rewards 条款](https://www.microsoft.com/en-US/servicesagreement)。

任务实现参考了 [bing-rewards-auto](https://github.com/dwgx/bing-rewards-auto) 的独立会话及操作后核对积分思路，具体功能以本项目说明为准。

### 添加脚本

在网页中新建任务，设置唯一任务 ID、工作目录、启动命令和执行频率，再通过「脚本」编辑入口创建脚本。

Python 任务的启动命令可设置为：

```json
["{python}", "main.py"]
```

`{python}` 自动使用项目虚拟环境中的解释器。也支持 JavaScript、PowerShell 和 Shell 脚本，需要本机安装对应运行环境。

也可将脚本和 `task.json` 放入 `tasks/<任务名称>/`，点击「扫描插件」添加任务。插件配置示例见 `tasks/example/`。

### 数据备份

任务配置、执行记录、日志和脚本备份保存在本机 `data/` 目录。备份前停止服务，再复制整个目录；恢复时将备份放回相同位置。备份中可能包含本地配置和登录状态，请妥善保存。

更多任务执行行为见 [功能说明](docs/architecture.md)。
