# 首版验证记录

日期：2026-11-23。环境：Windows、Node.js 24.14.1、Python 3.12、PostgreSQL 16.15、dsh 0.2.2-rc.5、Chrome（Playwright 驱动）。

## 已完成的验证

- `npm test`：10 项通过，覆盖真实 dsh 工具注册、模型工具权限、会话恢复、HTTP 凭据隔离、重定向阻断、同源校验、引用偏移和审核版本绑定。
- `npm run build`：TypeScript 编译通过，按实际安装的 dsh 版本验证接口。
- 后端真实 PG 与 Windows 文件系统测试：29 项测试及 10 项子测试通过；一条上游 Starlette/AnyIO 弃用警告。覆盖范围见 [backend-verification.md](backend-verification.md)。
- Alembic 首次迁移、结构对比、降级及重新升级通过；后端 wheel 构建通过。
- `node scripts/smoke-dsh.mjs`：实际宿主登录、内嵌资源、匿名访问拦截、跨源写请求拦截和代理路由白名单通过。
- `node scripts/e2e-web.mjs`：Chrome 中登录原版 dsh、打开内嵌 FileMate 面板、打开证据计划、校验、批准具体版本、执行、等待该作业完成，核对磁盘文件确实移动；无页面脚本异常。

端到端测试仅在 `work/e2e-*` 创建和移动测试文件。测试脚本等待当前作业详情中的完成状态，避免历史作业列表中的“已完成”文字造成提前判定。截图保存在 `work/dsh-embedded.png` 和 `work/filemate-report.png`。

## 验证边界

没有用户模型 API Key，因此未调用真实 DeepSeek/Qwen，也不将工具集成通过等同于模型任务成功率。端到端测试通过 API 构造计划，再以真实浏览器完成审核执行。

此机器没有可直接使用的 PATH Python、Docker 或 PostgreSQL。验证使用已有的 Python 运行时、项目内的依赖目录和临时 PostgreSQL 实例（回环端口 55432）。标准 `.venv` 安装遇到依赖下载超时；不能宣称标准环境已配置完成。用户正常运行仍需按 README 安装 Python 依赖并配置自己的 PG；`.env` 默认数据库配置没有被替换为临时测试库。

最终后端全量测试由集成方独立重跑，结果为 29 passed、10 subtests passed。临时 API、Worker、dsh 和测试 PG 在验收后停止；未改动其他数据库实例。

首版仅验证 Windows 单用户、同盘移动和受限大小文档。pgvector、OCR、Excel、跨盘移动不在本轮交付范围。扫描为同步请求，不宣称大目录后台索引已实现。

## 审查

实现经过独立代码审查，问题及复核结果保存在 [code-review.md](code-review.md)。修复包含源版本绑定、Unicode 文件名、会话及作业发现、执行前证据校验、引用准确定位。
