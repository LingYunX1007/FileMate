# FileMate 技术方案 V2

用户确认：PostgreSQL、DeepSeek Harness（dsh）、复用其 Web UI；通过 API 调用 DeepSeek/Qwen，不使用 Ollama。

## 产品目标

面向 Windows 单用户本机文件管理，让 Agent 调查目录和文档、根据证据制定分类规则、与用户多轮修订计划，执行经过审核的具体变更，解释失败并提供有限撤销。

主线：了解目录 → 检索候选 → 按需读取 → 建立依据 → 澄清 → 创建计划 → 修订 → 人工审核 → 执行验证 → 报告/恢复。

首版支持授权目录、增量扫描、名称/类型/时间/正文关键词搜索、TXT/Markdown/DOCX/文本 PDF、批量重命名和同盘移动、可编辑计划、确认、日志、崩溃核对、撤销。扫描件明确提示不支持。第二阶段是 pgvector、OCR、Excel、规则复用增强；永久删除和覆盖不支持。

## 架构与备选方案

推荐 TypeScript dsh 插件 + Python FastAPI 业务服务 + PostgreSQL。全 TypeScript 可减少进程，但文档和后续 Excel 生态不如 Python 方案贴合。

浏览器复用 dsh 聊天、会话、模型设置；FileMate 扩展提供证据、审核计划、任务报告。dsh 是唯一 Agent 循环。Python 不再实现模型规划器，只提供扫描、解析、检索、计划校验、执行与恢复。PG 存业务事实，原文件留在磁盘。Harness 内部会话持久化保留原实现，以 session_id 关联。

## 发挥 dsh 优势

Agent 自行选择先查看结构还是读文档、是否补查关联资料、是否澄清、何时停止。工具返回结构化错误和可定位证据，驱动修订。会话恢复时从 PG 读取任务、结果集及计划，不靠聊天记忆猜测实际状态。

四个阶段：调查（选工具与证据）、规划（提出规则）、修订（只改受影响条目）、结果处理（解释真实报告）。设置探索/读取预算、取消入口，避免无限循环。

工具：inspect_directory、search_files、get_file_metadata、read_document_excerpt、get_task_context、create_plan、validate_plan、revise_plan、get_job_report、create_undo_plan。扫描只允许已授权根目录。新增授权、批准、执行入口不暴露给模型。

## Web UI 和权限

复用 dsh Web；新增同源 FileMate 业务视图，优先通过插件扩展。工具卡片显示证据/计划链接；计划页提供原路径、目标路径、原因、冲突与修改按钮；报告页提供成功、失败、待恢复状态。

确认来自认证用户操作，不来自模型提供的 approved=true。确认绑定 plan_id/version/hash，修改使旧确认失效。浏览器不持有模型凭据、数据库密码和内部服务令牌。Host 代理内部 API，Python 仅回环监听并验证服务身份。FileMate 专用 profile 限制 shell、任意代码、文件写入、插件安装等旁路。文件正文是不可信数据，不是系统指令。

## PostgreSQL

roots/files 保存授权和文件版本；document_chunks 保存片段与页码；tasks/result_sets 保存目标与稳定候选顺序；evidence 保存来源；plans/plan_items 保存不可混淆的版本；approvals 绑定审核；jobs/operation_events 保存执行和恢复；rule_templates 后续保存用户明确确认的规则。

搜索先元数据过滤+参数化关键词。中文默认子串匹配，不宣称默认英文全文分词支持中文。后续 pgvector 合并关键词与语义候选；嵌入服务独立于聊天模型，记录模型/维度/版本。Top-K 不能代表“所有”。

## 安全执行与恢复

待审核 → 已批准 → 执行中 → 完成/部分失败/待恢复。逐项检查版本、源文件指纹、目录边界、链接/junction、重名和 Windows 非法路径。先持久化准备记录，再执行不覆盖的同盘移动，验证后写完成事件。对写任务加锁。PG 不可用时不发起文件变更。

文件系统和数据库无统一事务；崩溃后核对源/目标，不能盲目重试。无法确定就待人工核对。撤销是新的受审核计划，检查内容变化和原路径占用。并发外部程序仍可能制造竞态，使用平台不覆盖语义，避免存在性检查后覆盖。

## 技术与部署

dsh 固定经验证版本；TypeScript 插件；FastAPI/Pydantic；SQLAlchemy/psycopg/Alembic；PostgreSQL；pypdf/python-docx；PG 任务表+Python Worker。宿主机运行 dsh/Python，PG 可容器化。不引入 Redis、多 Agent 或另一套前端框架。云端推理会发送必要片段，不能宣称数据不出本机。

## 开源借鉴

- Local-File-Organizer：内容到名称/分类、dry-run、进度；其 Copilot 属路线图，不当作已验证实现。https://github.com/QiuYannnn/Local-File-Organizer
- FileButler：证据定位、逐项审核、批量撤销、模板、基础能力与模型解耦。https://github.com/7SteveJohn/FileButler
- AIOrganizer：提案、操作日志、验证、恢复记录、撤销计划。https://github.com/GregoryMorse/AIOrganizer
- dsh 架构与工具接口：https://github.com/deepseek-ai/deepseek-harness

以上来自公开文档，未对这些项目做完整运行或安全审计。只借鉴设计，不直接复制其产品范围。

## 分阶段与验收

1. 接入：dsh Web 中调用只读工具、展示结果、验证认证审核链路。
2. 调查：按需搜索读取、定位依据、保留不确定项、多轮稳定引用。
3. 整理：修订使旧批准失效，审核后执行，无授权不能写。
4. 恢复：重启不重复移动、源变化阻断、撤销不覆盖。
5. 增强：pgvector/OCR/Excel，均保留为后续阶段。

对比一次模型生成计划与多步调查的正确率、修正量、成本和耗时。自动测试覆盖未审核执行、越界、同名、文件占用、版本变化、重复执行和崩溃核对。真实模型验证需要用户配置 API Key，不以模拟测试冒充。
