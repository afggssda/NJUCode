# SBOM 清单（Software Bill of Materials）

## 代码来源说明

本项目所有代码采用统一来源路径：

> **吸收 Claude Code 精髓 → 自然语言描述需求 → 大模型生成代码 → 人工检测修正**

开发流程为：先深入理解 Claude Code 的设计理念与工程实践，提炼核心模式（如三栏布局、Patch 生命周期、上下文压缩策略、Skills 插件机制、MCP 协议集成等）；再用自然语言将需求、接口约束、编码规范描述给大模型；大模型生成初版代码后，由对应模块负责人进行人工审查、测试验证与缺陷修复。因此，下表中“来源”统一标注为上述流程，不再逐行赘述。

---

## 系统概述

NJUCode 是一个基于 Python Textual 框架的终端代码助手（Code Agent），系统按功能划分为六大模块：

| 模块 | 负责人 | 核心文件数 | 说明 |
|------|--------|-----------|------|
| CLI/TUI 与交互框架 | 李承泽 | 12 | 终端 UI 布局、事件调度、组件编排 |
| Session 与上下文压缩 | 丁一鸣 | 3 | 会话生命周期管理、上下文 token 压缩 |
| 代码检索与多文件分析 | 周靖宇 | 5 | 项目扫描、符号检索、依赖分析、代码指标 |
| Patch/回滚与执行引擎 | 程楷诺 | 2 | 补丁生命周期、diff 生成、备份回滚 |
| Skills 插件系统 | 曹喆 | 13 | 技能注册/执行、权限控制、审计日志、MCP 集成 |
| 模型路由、成本统计、测试与CI | 陈志远 | 5 | 多模型兼容接口、配置持久化、综合测试套件 |

---

## 文件清单（按目录结构）

### 根目录

| 文件 | 用途 | 来源 |
|------|------|------|
| `main.py` | 应用入口，创建 NjuCodeApp 实例并启动 Textual 事件循环 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `requirements.txt` | Python 依赖声明（textual、openai、python-dotenv 等） | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `test_all_features.py` | 综合回归测试套件（约 1574 行），基于 unittest 标准库，覆盖核心服务（代码分析、上下文压缩、Patch 引擎、Skills、MCP、Project Doctor）与跨模块集成，自动生成 Markdown/JSON 报告 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `hello_world.py` | 示例文件，用于测试运行时工具链 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `.env.example` | 环境变量模板（API Key、Base URL、Model 等配置参考） | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `LICENSE` | 项目开源许可证 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |

### frontend/ — TUI 应用核心

| 文件 | 用途 | 来源 |
|------|------|------|
| `frontend/__init__.py` | 包声明 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/app.py` | **主应用类**（约 1728 行），NjuCodeApp(Textual App)，负责三栏布局管理、键盘快捷键绑定（Ctrl+N/H/C/Q）、所有 UI 面板的创建与挂载、事件消息路由（MessageSubmitted、StreamInterruptRequested、WorkspaceChanged 等 20+ 种自定义消息）、流式输出控制与中断、斜杠命令分发、Skills/MCP/Patch 面板集成 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/app.tcss` | Textual CSS 样式文件，定义三栏布局、面板颜色、分割条、按钮、输入框等全部 UI 样式 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/models.py` | 核心数据模型：ChatMessage（消息）、ChatSession（会话，含摘要/压缩计数/中断状态）、ToolToggle（工具开关）、ModelConfig（模型配置）、镜像预设映射 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/state.py` | **全局状态管理**（约 1117 行），AppState 类集中管理会话列表、当前激活会话、工具权限、模型配置、SettingsStore 持久化、SkillRegistry、AuditLogger、SkillExecutor、PermissionChecker、MCPManager、MCPToolExecutor、PatchEngine、PatchHistoryStore 等全部子系统单例，负责会话 CRUD、消息追加、压缩触发、摘要生成、会话导入导出 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |

### frontend/services/ — 业务服务层

| 文件 | 用途 | 来源 |
|------|------|------|
| `frontend/services/__init__.py` | 包声明 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/openai_client.py` | **OpenAI 兼容客户端**（约 123 行），OpenAICompatibleClient 封装多镜像/多模型流式调用接口，负责消息组装（拼接 @file 上下文与 model_file 系统提示词）、stream_chat 流式输出（支持 stop_event 中断）、chat 非流式聚合 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/context_compressor.py` | **上下文压缩器**（约 557 行），ContextCompressor 负责双语（CJK+ASCII）token 估算、基于阈值的压缩触发判断、调用模型生成结构化历史摘要（【用户意图】+【关键结论】）、增量压缩（已有摘要时合并而非丢弃）、摘要质量验证与自动重试、自适应 keep_recent 策略、压缩历史元数据追踪（CompressionRecord） | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/code_analysis.py` | **代码分析引擎**（约 834 行），CodeAnalyzer 实现：项目文件扫描与索引（排除 .git/venv/node_modules/__pycache__ 等）、文本/正则检索（大小写敏感可选）、Python 符号检索（class/def/async def/import）、文件摘要（类/函数/依赖/入口）、基于 import 的依赖图与 1~2 层邻接分析、自然语言 Top-K 文件召回（关键词匹配排序）、影响面分析（风险等级+建议阅读顺序）、同名符号自动去重排序 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/code_metrics.py` | **静态代码指标**（约 543 行），CodeMetrics 实现：圈复杂度计算、函数/类级别指标（FunctionMetric）、文件级别依赖统计（fan-in/fan-out）、import 环路检测（Tarjan SCC 算法）、维护热点排序（综合复杂度+依赖+变更频率）、支持 --top/--path/--include-tests 过滤 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/task_index.py` | **任务/TODO 扫描器**（约 384 行），TaskIndex 扫描项目中的 TODO/FIXME/BUG/HACK/NOTE 等代码标记及 Markdown 复选框清单项，构建可检索的任务索引，支持标签/负责人/完成状态过滤 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/code_extractor.py` | **代码块提取器**（约 188 行），从聊天回复中提取 markdown 代码块（```python 等），解析语言标签与行号范围，支持结构化输出供下游模块（Code Viewer、Patch Engine）使用 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/patch_engine.py` | **Patch/回滚执行引擎**（约 757 行），PatchEngine 实现 PatchStatus 生命周期状态机（PENDING→PREVIEWED→CONFIRMED→APPLYING→APPLIED/FAILED/ROLLED_BACK/CANCELLED）、PatchOperation 单文件变更与 unified diff 生成、PatchTask 多文件原子补丁（自动备份到 .nju_code/backups/）、apply 应用、rollback 回滚、cancel 取消、PatchHistoryStore JSON 持久化历史 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/project_testing.py` | **Project Doctor 项目自检**（约 1139 行），独立于 Textual UI 的诊断服务，检查项包括：项目骨架完整性、依赖声明、Python 语法、入口导入、分析引擎可用性、代码块提取、上下文压缩、Settings、Patch、Skills、MCP、UI 结构、斜杠命令一致性、基础安全项；结果输出为 Markdown/JSON 报告 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/settings_store.py` | **配置持久化**（约 255 行），SettingsStore 管理 .nju_code/settings.json 的读写，负责会话、工具状态、模型配置、Skills 状态、MCP 配置、Patch 历史的加载与保存 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/services/runtime_tools.py` | **运行时工具**（约 35 行），run_hello_world 等轻量运行时验证函数 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |

### frontend/ui/widgets/ — UI 组件

| 文件 | 用途 | 来源 |
|------|------|------|
| `frontend/ui/widgets/__init__.py` | 包声明，导出所有 widget 类与自定义消息 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/chat_panel.py` | **聊天面板**（约 374 行），ChatPanel 实现消息列表渲染（Markdown 富文本）、输入框（支持 @file 上下文注入、斜杠命令 /help /scan /search /symbol 等）、发送按钮、流式输出实时更新、Ctrl+C 中断、消息历史滚动 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/code_viewer_panel.py` | **代码查看面板**（约 222 行），CodeViewerPanel 实现代码语法高亮显示（TextArea）、Ctrl+S 编辑保存、文件重载、空态 NJU 艺术字引导、行号显示、文件上下文注入到聊天 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/config_panel.py` | **配置面板**（约 101 行），ConfigPanel 实现模型配置界面（base_url、api_key、model、model_file 编辑）、镜像预设选择（AtlasCloud / ModelScope 等）、配置保存与广播事件 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/file_tree_panel.py` | **文件树面板**（约 411 行），FileTreePanel 实现工作区目录浏览（DirectoryTree）、新建文件/目录、删除文件/目录（进入 .nju_code/trash）、Undo Delete 撤销删除（支持 Ctrl+Z 连续撤销）、周期刷新、点击打开文件 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/session_panel.py` | **会话管理面板**（约 415 行），SessionPanel 实现会话列表展示、创建/切换/重命名/删除会话、会话导出（JSON）、会话导入、压缩计数与 token 估算展示、中断状态标记 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/tools_panel.py` | **Tools 工作台面板**（约 184 行），ToolsPanel 提供按钮触发 Help/Scan/Search/Symbol/Summary/Deps/Recall/Impact/Tasks/Metrics/Doctor，参数输入框（query/path/depth/top_k），与聊天斜杠命令共用执行链路 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/skills_panel.py` | **Skills 面板**（约 264 行），SkillsPanel 展示已注册技能列表、技能启用/禁用开关、技能执行触发、审计日志查看入口、内置技能与插件分类显示 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/mcp_panel.py` | **MCP 面板**（约 313 行），MCPPanel 管理 MCP 服务器连接（添加/删除/连接/断开）、工具列表展示与开关、连接状态指示、服务器配置编辑 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/patch_panel.py` | **Patch 面板**（约 346 行），PatchPanel 展示 PatchTask 列表与状态、diff 预览（unified diff 格式）、确认/取消/回滚操作按钮、任务刷新 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/ui/widgets/splitter.py` | **分割条组件**（约 64 行），VerticalSplitter 实现三栏布局拖拽调宽，发送 SplitterDragged/SplitterDragEnded 事件 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |

### frontend/skills/ — Skills 插件系统

| 文件 | 用途 | 来源 |
|------|------|------|
| `frontend/skills/__init__.py` | 包声明 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/skills/models.py` | **Skills 数据模型**（约 195 行），SkillPermissionLevel（READ_ONLY/MODIFY_LOCAL/EXECUTE_COMMAND/NETWORK_ACCESS/FULL_ACCESS）、SkillStatus、SkillKind（AGENT/COMMAND）、SkillManifest（名称/描述/参数/输出/权限/版本）、SkillToggle、SkillExecutionLog、SkillExecutionResult | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/skills/registry.py` | **技能注册中心**（约 425 行），SkillRegistry 管理内置技能注册、外部插件发现与加载（从 skills/plugins/ 目录）、命令名到技能映射、启用/禁用切换、状态持久化到 .nju_code/settings.json | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/skills/executor.py` | **技能执行引擎**（约 436 行），SkillExecutor 负责参数解析（shlex 分词）与验证（类型/必填/默认值）、权限检查（调用 PermissionChecker）、技能调用（内置函数或插件模块）、结果格式化（文本/Markdown/JSON）、执行日志记录 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/skills/permissions.py` | **权限检查器**（约 156 行），PermissionChecker 根据 SkillPermissionLevel 判断操作是否允许，支持全局权限策略与逐技能权限覆盖 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/skills/audit_log.py` | **审计日志**（约 260 行），AuditLogger 记录每次技能执行的完整审计轨迹（时间/技能名/参数/结果摘要/触发人/权限级别），持久化到 .nju_code/audit/，支持日志查询与导出 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/skills/builtin/__init__.py` | 内置技能包声明，注册 BUILTIN_MANIFESTS 和 BUILTIN_AGENT_MANIFESTS（scan/search/symbol/summary/deps/recall/impact/tasks/metrics/doctor/patch/rollback 等所有内置斜杠命令均在此作为技能注册） | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/skills/plugins/__init__.py` | 外部插件目录声明，支持用户自定义 Python 插件加载 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |

### frontend/mcp/ — MCP 协议集成

| 文件 | 用途 | 来源 |
|------|------|------|
| `frontend/mcp/__init__.py` | 包声明 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/mcp/models.py` | MCP 数据模型：MCPServerConfig（名称/传输类型/命令/参数/环境变量）、MCPConnectionState、MCPTransportType、MCPToolInfo、MCPToolToggle | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/mcp/client.py` | **MCP 客户端**（约 220 行），MCPClient 基于 stdio 传输的 MCP 协议客户端，负责子进程管理、JSON-RPC 消息收发、工具列表获取、工具调用请求 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/mcp/manager.py` | **MCP 管理器**（约 324 行），MCPManager 管理多服务器连接生命周期（connect/disconnect）、工具发现与注册、工具开关状态管理、配置持久化（与 SkillRegistry 模式对等） | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/mcp/executor.py` | **MCP 执行器**（约 222 行），MCPToolExecutor 负责工具调用参数构造、异步执行、结果解析与格式化 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `frontend/mcp/tool_adapter.py` | **工具适配器**（约 127 行），MCPToolAdapter 将外部 MCP 工具描述转换为 NJUCode 内部统一的 ToolToggle 格式，实现与现有工具系统的互通 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |

### demo/ 与 examples/ — 示例与演示

| 文件 | 用途 | 来源 |
|------|------|------|
| `demo/main.py` | 演示入口脚本 | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `examples/tasks_showcase.py` | /tasks 命令功能展示（含 TODO/FIXME/BUG 标记示例） | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |
| `examples/metrics_showcase.py` | /metrics 命令功能展示（含复杂函数/类示例用于验证圈复杂度计算） | 吸收 Claude Code 精髓 → 自然语言描述 → 大模型生成 → 人工检测 |

---

## 依赖组件（第三方开源）

| 组件 | 版本 | 用途 | 许可证 |
|------|------|------|--------|
| textual | -- | TUI 框架，提供 App、Widget、CSS 等终端 UI 基础设施 | MIT |
| openai | -- | OpenAI 兼容 API 客户端，支持流式与非流式调用 | Apache 2.0 |
| python-dotenv | -- | 从 .env 文件加载环境变量 | BSD |

---

## 统计汇总

| 指标 | 数值 |
|------|------|
| Python 源文件总数 | 47 |
| 总代码行数（含注释/空行） | ~14,500 |
| 自定义消息事件类型 | 22 |
| 内置斜杠命令/技能 | 13 |
| UI 面板组件 | 11 |
| 服务模块 | 9 |
| 数据模型定义 | 7（models / state / skills.models / mcp.models） |
| 所有人均代码量 | >2000 行 |
