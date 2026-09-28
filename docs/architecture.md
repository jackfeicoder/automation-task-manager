# 架构与维护

前端原生 ES Modules → FastAPI → SQLite → 单实例定时调度器 → 独立任务进程。

## 模块

| 目录 | 用途 |
|---|---|
| `backend/app/main.py` | HTTP API、访问控制、插件扫描、静态前端 |
| `backend/app/models.py` | 配置验证 |
| `backend/app/scheduler/` | 时区及下次执行时间 |
| `backend/app/runner/` | 执行队列、进程树清理、结果协议、日志 |
| `backend/app/storage/` | SQLite 与调度器实例锁 |
| `backend/app/api/files.py` | 脚本读取、语法验证与版本备份 |
| `backend/app/environment.py` | 变量配置、子进程环境、日志脱敏 |
| `frontend/src/` | 页面、API 封装、公共组件 |
| `tasks/` | 插件及发现清单 |
| `scripts/` | 环境安装、启动、测试、GitHub 发布 |

## 任务生命周期

定时触发/手动运行 → 入队（配置快照） → 按并发/资源组启动 → 完成或失败。

数据库事务和部分唯一索引防止同一任务同时出现多个活动记录。Windows 使用 kill-on-close Job Object 清理后代进程，Unix 使用进程组。输出流式读取、脱敏和限量落盘；GitHub 与平台访问密钥不传入任务进程。

每 0.5 秒检查调度和队列。补跑最多入队一次；全局暂停不清空队列。更改频率/启停重新计算下次时间，仅修改描述保留原时间。任务执行配置在入队时固定；磁盘脚本需避开运行时手动修改，UI 会阻止执行期间编辑共用目录。

## 接口

- `GET/POST /api/tasks`：列表和新增。
- `PUT/DELETE /api/tasks/{id}`：修改和删除。
- `POST /api/tasks/{id}/run`：手动入队。
- `POST /api/tasks/batch`：`{ids, action: run|enable|disable}`。
- `POST /api/tasks/discover`：扫描新增插件，逐项报告错误。
- `GET /api/runs`、`GET /api/runs/{id}`：执行历史和日志。
- `POST /api/runs/{id}/stop`：取消或停止。
- `GET/PUT /api/settings`：全局配置。
- `GET/PUT /api/environment`：变量状态和写入；GET 不返回值。
- `GET /api/plugins/workbuddy/doctor`：离线凭据检查。
- `GET /api/tasks/{id}/files`、`GET/PUT /api/tasks/{id}/file`：脚本管理。

管理密钥通过 `.env` 配置，浏览器使用 HttpOnly/SameSite cookie；脚本可用 Bearer 头。界面与 API 同源，Host/Origin 检查限制其他网页调用本机服务。

## WorkBuddy

读取桌面客户端 `auth.accessToken`、`account.uid`、`auth.domain`，支持环境变量覆盖。仅向受支持的腾讯 HTTPS 域名发送凭据，关闭重定向。

2026-09-28 本机直接验证：

- `POST /v2/billing/meter/checkin-activity-status` 返回活动状态，GET 在当前网关返回 404。
- `POST /v2/billing/meter/daily-checkin` 返回积分，本机领取 100 积分。
- 业务码 `10001` 视为今日已完成。
- 401 → 需要登录；403/429 → 需要处理；未知成功响应保持待核验。

当前面向本机个人账号。切换账号时请使用独立任务 ID、登录文件及资源组，避免每日完成缓存混用。客户端内部接口可能变更；当前版本依赖客户端刷新登录，未实现自动刷新令牌、远程多用户、任务依赖图或自动修复上线。
