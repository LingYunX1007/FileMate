# 生成循环修复验证

对应 [修复计划](loop-fix-plan.md)。

## 已定位的问题与修改

用户实际会话中，inspect 返回整个根目录 51 个文件的统计，但仅附前 30 个样本。Test 子目录统计为 12 个，而样本只有其中 8 个。随后一次生成反复讨论数量和路径，却没有继续调用工具。已有工具次数预算无法限制这种单次生成。

- `services/filemate/api.py`、`catalog.py`：新增安全的相对子目录筛选；概览返回样本数量、上限、截断标记及下一步提示；检索返回完整分页字段和可读 UTC 修改时间。
- `packages/harness-filemate/src/tools.ts`、`agent.ts`：明确完整枚举、分页终点、目标相对路径、扩展名分类的简单流程；不再要求纯类型分类读取正文。
- `generation-guard.ts`：通过真实 dsh 生命周期限制每次生成，默认 120 秒/24,000 UTF-16 字符。包括等待首 token、思考、正文和工具参数，不包括工具等待。达到预算调用正式 `agent.cancel`，不自动重试；取消原因由 dsh 持久化。
- `index.ts`、`generation-notices.ts`、`public/launcher.js`：owner 认证的只读停止通知接口和中文提示。通知上限 20 条，不含问题、文档内容或凭据。浏览器关闭提示后不重复弹出，同一 Agent 重载后的新停止仍可显示。

## 验证结果

使用项目 `.venv`、Windows 文件系统及专用临时 PostgreSQL 16.15；未访问或修改用户测试文档。

| 检查 | 结果 |
| --- | --- |
| Python `pytest -q` | 31 passed，10 subtests passed；1 条已有 Starlette/AnyIO 弃用警告 |
| `npm test` | 24 passed |
| `npm run build` | TypeScript 编译通过 |
| `node scripts/test-generation-notice.mjs` | Chrome 中停止提示、纯文本防注入、关闭后刷新保留、同 attempt ID 后续新提示通过 |
| `node scripts/smoke-dsh.mjs` | 独立 dsh profile/端口 3081 的宿主登录、资源、匿名拦截、同源校验、通知只读接口通过 |

后端新回归先验证旧实现失败，再验证 51/12/8 案例可分页取齐 12 个候选并创建完整有效计划；覆盖 Test 与 Test2 边界、SQL 通配字符作为普通目录字符、绝对路径和越界拒绝、嵌套路径、分页结束及任务上下文中的分页信息。

生成保护测试包含真实 dsh SessionStore、ProjectionRegistry、AgentRegistry、Tools、NativeExecutor 与 AgentLoop。使用本机模拟提供方挂起 HTTP 响应，验证正式 Agent.cancel 将信号传递到 fetch 并中断响应，持久化中文 hook 原因，且仅发生一次请求。另覆盖无工具调用的思考输出、参数输出、完整块重复计数、正常结束、工具等待、用户取消、会话隔离和计时器清理。

独立代码复审发现 attempt ID 在 Agent 生命周期之间可能重复，导致后续通知被当作已关闭。已将 stoppedAt 纳入通知标识，并补充失败到通过的回归及浏览器复测。新增接口权限单测覆盖 401、403、owner GET、禁止 POST 和 no-store。

## 边界及复测方法

本轮没有调用用户的远程模型接口、修改模型参数或重启用户运行中的服务。模拟测试证明取消链路有效，不代表该 Qwen 服务的模型重复生成概率已测定。服务端是否在断连后立即停止推理和计费取决于提供方。字符限制按完整流式 chunk 检查，因此最后一个 chunk 可能超过阈值。

检索分页不是跨请求数据库快照；若扫描同时改变索引，需重新枚举核对数量。directory 按索引中的目录拼写匹配。现有计划审批和文件执行机制不变。

更新后运行 `npm run build`，重启 backend、worker、dsh，刷新浏览器并新建 FileMate 会话。让模型只查询 `Test` 并按扩展名创建计划，应取齐当前索引中的全部候选（Office 临时文件可能影响当前数量），返回校验结果和审核链接。实际文件操作仍需面板批准及执行。
