# FileMate

使用 **DeepSeek Harness 原版 Web UI + PostgreSQL + Python** 的本机文件调查与整理助手。dsh 负责按需检索、读取证据、制定及修订计划；文件服务负责审核绑定、执行、验证和恢复。

技术方案见 [docs/technical-design-v2.md](docs/technical-design-v2.md)，接口见 [docs/api-contract.md](docs/api-contract.md)。当前实现 V2 首版，pgvector、OCR、Excel 和跨盘移动属于后续阶段。

## 已实现

- 用户在 dsh 中对话，在内嵌 FileMate 面板管理授权目录、查看证据和审核计划。
- TXT/Markdown、文本 PDF、DOCX 的按需扫描和内容提取；名称、正文、扩展名、时间检索。
- Agent 可持续调查、创建任务和计划、修订、查询实际报告；文件正文不作为指令。
- 重命名/同盘移动，缺失的目标父目录随计划创建；不覆盖、不永久删除。
- 用户明确批准精确版本/hash，随后单独执行；模型没有批准、执行、shell 或任意代码工具。
- PG 持久化逐项记录，Worker 中断核对、取消和需要再次审核的撤销计划。

## 本机启动（Windows）

需要 Node.js 24、Python 3.11+ 和 PostgreSQL 16+。dsh 固定为 `0.2.2-rc.5`；依赖由 package-lock.json 锁定。先进入项目目录：

```powershell
cd D:\dev\FileMate
npm ci --ignore-scripts
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
npm run init:local
npm run build
```

`init:local` 仅在 `.env` 不存在时生成独立随机凭据，不覆盖已有设置。Windows 若没有可用的 `python` 命令，请使用已安装 Python 的完整路径。不要把 `.env` 提交到版本库。

已有 PG：修改 `.env` 中 `FILEMATE_DATABASE_URL` 指向专用的 FileMate 数据库。URL 中的特殊字符密码需要 URL 编码。

使用 Docker 创建 PG：

```powershell
docker compose --env-file .env -f infra/compose.yaml up -d
```

初始化业务表：

```powershell
npm run db:migrate
```

分别在三个终端运行：

```powershell
npm run backend
npm run worker
npm start
```

打开 dsh 打印的带临时登录令牌的本机链接；成功登录后地址栏会移除令牌。点击右上角 **FileMate** 打开侧栏。直接访问 `/filemate` 也需要先登录 dsh。

在 dsh 模型设置中填写 DeepSeek API Key；Qwen 使用该固定版本 dsh 支持的兼容模型提供方配置。未提供真实 API Key 时，文件服务和人工审核可验证，但无法完成真实模型对话。云端模型会接收你交给它的问题和必要的文件片段，本项目不宣称推理完全离线。

## 首次使用

1. 在 FileMate 面板登记一个专用于试运行的目录并扫描。
2. 在 dsh 新建会话，使用 FileMate preset，输入例如：“查看已授权目录，按客户整理合同；有疑问的先问我，不要动草稿。”
3. Agent 调查后返回计划链接，在面板查看依据、修改目标路径或排除条目。
4. 校验计划，核对版本和条目，勾选确认，点击“批准此版本”。
5. 单独点击“执行已批准版本”。Worker 必须在运行，才能处理队列。
6. 在报告中查看真实结果；“生成撤销计划”仍需校验、批准和执行。

聊天中的“同意”不会执行文件变更。修订使已有审核失效。失败、源文件变化、目标占用时不会覆盖其他文件。撤销可能留下先前创建的空目录。

## 生成保护与本次升级

目录概览只返回最多 30 个样本，现在明确标注截断；批量整理使用 `search_files` 的 `directory` 子目录筛选和分页，取齐候选后再创建计划。按扩展名整理不要求读取正文。工具返回可读 UTC 修改时间。

每次模型生成默认最多 **120 秒或 24,000 个字符**（包括思考、正文和工具参数，按 UTF-16 计数）。达到任一限制时，FileMate 通过 dsh 正式取消接口终止本次请求，不自动重试；页面显示停止原因。计时包括等待首个 token，不包括工具执行时间，下一次生成重新计数。这是有限预算保护，不是保证模型不会重复。

可在 `.env` 配置 `FILEMATE_GENERATION_TIMEOUT_MS`（1,000..600,000）及 `FILEMATE_GENERATION_MAX_CHARS`（1,000..200,000）。无效配置使用默认值；超范围正整数会被限制在范围内。首次使用建议保留默认值。API 服务端是否立即停止计算取决于提供方的断连处理，本项目验证的是客户端取消信号和连接中断。

升级现有运行实例：在原来的三个终端按 Ctrl+C 停止应用，运行 `npm run build`，再分别运行 `npm run backend`、`npm run worker`、`npm start`。PostgreSQL 无需重启，本次不涉及数据库迁移。浏览器刷新，并新建 FileMate 会话进行复测，避免把旧的长篇重复内容继续带入上下文。已有授权、计划和模型配置保留。

验证记录见 [docs/loop-fix-verification.md](docs/loop-fix-verification.md)。停止通知在当前 dsh 进程内保留最近 20 条；永久原因保存在 dsh 会话日志，重启后不会重新弹出旧通知。

## 开发与测试

```powershell
npm test
npm run build
```

后端集成测试需要专用 PG 测试库（测试在独立 schema 中工作）：

```powershell
$env:FILEMATE_TEST_DATABASE_URL='postgresql://test_user:password@127.0.0.1:5432/filemate_test'
npm run test:backend
```

未设置测试库 URL 时数据库测试会跳过；这不代表 PG 集成通过。Windows 受限环境可将 `TEMP`/`TMP` 指向项目内的测试目录。

`scripts/e2e-web.mjs` 是可选真实浏览器联调：需要 Chrome、Playwright，以及使用相同 `.env` 服务令牌启动的测试后端、Worker 和 dsh。dsh 启动日志保存为 `work/dsh-server.log`。它仅在 `work/e2e-*` 创建测试文件，并通过真实页面审核、执行移动。请连接专用测试库。详细验证结果见 [docs/verification.md](docs/verification.md)。

## 目录结构

```text
packages/harness-filemate/  dsh 工具插件、受认证业务代理、内嵌工作台
profiles/                  FileMate 专用 dsh profile 补丁
services/filemate/         API、扫描/解析、计划、PG、执行器和 Worker
migrations/                Alembic 数据库迁移
tests/                     Windows 文件系统及 PG 集成测试
scripts/                   环境初始化、启动与端到端验证
infra/                     PostgreSQL Compose
docs/                      技术方案、接口契约、计划与验证记录
```

FileMate 业务数据使用 PG；dsh 自身的会话沿用上游持久化实现，独立保存在 `.runtime/dsh`。禁止将该配置用于暴露公网的多用户服务；首版面向本机 owner。

## 边界与维护

- 目前默认执行器针对 Windows 实现并验证。其他平台的执行限制以测试和代码为准，不承诺跨平台文件写入。
- 搜索使用最近一次扫描的索引；执行前重新核对文件状态。
- 扫描/正文提取有大小与长度限制，无法解析和截断会返回状态。扫描首版为同步请求，超大目录不属于本版验证范围。
- PG 事务不能回滚文件系统。崩溃通过准备日志、文件身份和内容核对恢复，无法判定时等待人工处理。
- dsh 是预览版本。升级必须重跑插件编译、鉴权、工具白名单和 Web 联调。

## 设计参考

参考了 [Local-File-Organizer](https://github.com/QiuYannnn/Local-File-Organizer) 的内容分类与 dry-run、[FileButler](https://github.com/7SteveJohn/FileButler) 的引用定位/逐项审核/撤销，以及 [AIOrganizer](https://github.com/GregoryMorse/AIOrganizer) 的操作记录与恢复思路。没有复制这些项目的源码。Harness 保留原有品牌与许可信息。
