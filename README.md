# Task Harbor · 自动化任务管理

本机任务管理平台，支持 Web UI、定时调度、独立进程、单个与批量运行、执行日志、全局配置、环境变量以及脚本编辑。内置 WorkBuddy 每日签到。

## Windows 启动

要求 Python 3.11+。前端由后端直接提供，无需 Node.js 或前端构建。

```powershell
cd E:\Desktop\github\automation
./scripts/setup.ps1
./scripts/start.ps1
```

打开 **http://127.0.0.1:8765**。也可双击 `start.cmd`，首次自动创建 `.venv` 并安装锁定依赖。服务停止按 Ctrl+C；关闭网页后后台任务继续执行。

## 界面

- **任务管理**：新增、编辑、删除、定时开关、单个/批量运行、批量启停。
- **频率**：手动、每日、每周、固定间隔、五段 Cron，支持任务时区。
- **执行记录**：状态、耗时、尝试次数、每 3 秒更新日志、停止与重跑。
- **全局配置**：并发、默认时区、调度暂停、补跑、重试间隔、日志保留。
- **运行环境**：venv、解释器、变量配置状态、WorkBuddy 登录检查；变量值只写不回显。
- **脚本编辑**：创建/修改 Python、JS、PowerShell、Shell 脚本，保存前备份旧版本；Python 检查语法。共用目录有任务排队/执行时需先停止任务。

## WorkBuddy

默认每天 **09:00 / Asia/Shanghai** 执行。先登录本机 WorkBuddy 桌面客户端，再点「运行环境 → 检查 WorkBuddy」。本机已验证 2026-09-28 签到领取 100 积分，实际奖励按活动规则确定。

自动查找：

```text
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
%APPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
```

可用 `WORKBUDDY_AUTH_FILE` 指定文件，或设置 `WORKBUDDY_ACCESS_TOKEN` 和 `WORKBUDDY_USER_ID`，可选 `WORKBUDDY_DOMAIN`。UI 本地变量优先于进程变量。

登录过期显示「需要登录」：打开客户端刷新登录后重跑。当前版本不改写客户端文件，也不自行刷新 refresh token。内部签到接口变更后可能需更新插件。

插件先查询状态，再通过积分结果或明确的「今日已签到」业务码确认完成。未确认的响应不进入每日成功缓存；网络/服务端错误可有限重试。

```powershell
./.venv/Scripts/python.exe tasks/workbuddy/main.py --doctor
./.venv/Scripts/python.exe tasks/workbuddy/main.py --status
./.venv/Scripts/python.exe tasks/workbuddy/main.py --claim
```

## 新增插件

前端新建任务，填写唯一 ID 及 `tasks/任务ID` 工作目录，保存后点「脚本」创建 `main.py`。命令使用 `["{python}", "main.py"]`，自动选择平台 venv。

也可在 `tasks/<plugin>/` 放入脚本及 `task.json`，点「扫描插件」发现。参考 `tasks/example/task.json`。已有配置以数据库为准，扫描不覆盖 UI 设置。删除任务后不会因扫描/重启自动恢复，可通过新建重新添加。

脚本打印日志，退出码 0 表示正常结束。业务结果可输出独立一行：

```python
import json
print('AUTOMATION_RESULT=' + json.dumps({
    'status': 'success', 'message': '已确认任务完成',
    'data': {'credits': 100}
}, ensure_ascii=False), flush=True)
```

支持 `success`、`already_completed`、`failed`、`needs_login`、`needs_attention`。仅 `failed` 并携带 `retryable: true` 自动重试，插件应确保写操作幂等。

同一任务最多一项活动执行，同资源组串行。关闭定时不会终止当前任务；停止按钮单独控制取消。入队配置固定，磁盘脚本请避开运行时手动修改。

## 配置与数据

`.env.example` 是启动配置示例：监听端口、可选管理访问密钥、WorkBuddy 变量。复制为 `.env` 后修改，重启生效；当前仅监听本机。

| 路径 | 内容 |
|---|---|
| `data/state.db` | 任务、设置、执行记录 |
| `data/environment.json` | UI 设置的本地变量 |
| `data/logs/` | 脱敏日志，单份约 2 MiB 上限，每小时清理过期文件 |
| `data/artifacts/revisions/` | 脚本修改前的备份 |
| `data/profiles/` | 后续浏览器插件登录状态 |

`data/`、`.env`、`.venv` 不提交 Git。备份前停止服务，复制整个 `data/`。独立进程用于故障隔离，脚本仍以本机用户身份执行，请加载可信插件。

重复启动由实例锁阻止。服务中断的运行标记为「服务中断」，排队任务恢复，错过的定时任务按补跑策略最多补跑一次。电脑关机时服务暂停。

可选 Windows 登录后自动启动，默认未安装：

```powershell
./scripts/install_startup.ps1
./scripts/install_startup.ps1 -Remove  # 移除
```

## 测试

```powershell
./scripts/test.ps1
# 浏览器测试需要本机 Edge，先启动后台服务
./.venv/Scripts/python.exe -m pip --isolated install --index-url https://pypi.org/simple -r requirements-dev.txt
./.venv/Scripts/python.exe scripts/ui_smoke.py
```

浏览器测试使用示例脚本，覆盖增删改、频率、单个/批量运行、日志、配置、变量、脚本编辑和响应式布局，不执行 WorkBuddy 领取。截图保存在 `data/artifacts/`。

## GitHub 发布

从环境读取 `GITHUB_TOKEN` 或 `GH_TOKEN`，默认创建私有仓库。令牌需要仓库创建和内容写入权限，密钥不进入远程 URL 或 Git 配置。

```powershell
./.venv/Scripts/python.exe scripts/github_repo.py create --name automation-task-manager
git add .
git commit -m "feat: completed step"
./.venv/Scripts/python.exe scripts/github_repo.py push
```

已有空仓库时，用 `git remote add origin https://github.com/OWNER/REPO.git` 后推送。名称冲突时创建脚本停止，避免覆盖现有仓库。

实现过程按阶段保留提交，已推送到私有仓库 [jackfeicoder/automation-task-manager](https://github.com/jackfeicoder/automation-task-manager)。环境令牌需要 Administration 读写权限以创建仓库、Contents 读写权限以推送代码，Metadata 只读权限由 GitHub 自动要求。

模块与接口见 [架构文档](docs/architecture.md)。
