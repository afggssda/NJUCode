# NJUCode

`NJUCode` 是一个基于 Textual 的终端代码助手原型。项目正在从“聊天壳”逐步演进为可检索、可分析、可安全修改代码的 Code Agent。

## 使用前请自行替换API key https://www.modelscope.cn/my/access/token
### 替换.env文件的OPENAI_API_KEY即可

## 项目结构
```text
NJUCode/
├── pyproject.toml
├── .env.example
├── main.py
├── requirements.txt
├── LICENSE
├── README.md
├── hello_world.py
├── 手册/
│   ├── 需求文档.md
│   ├── 项目管理计划.md
│   ├── SBOM清单.md
│   ├── 安装说明.md
│   └── njucode-0.5.0-py3-none-any.whl
├── 改动/
│   ├── 3.31_jingyu_change.md
│   ├── 4.16_jingyu_change.md
│   └── idea.md
├── njucode/
│   ├── __init__.py
│   ├── app.py
│   ├── app.tcss
│   ├── models.py
│   ├── state.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── openai_client.py
│   │   ├── code_analysis.py
│   │   ├── code_extractor.py
│   │   ├── code_metrics.py
│   │   ├── context_compressor.py
│   │   ├── patch_engine.py
│   │   ├── project_testing.py
│   │   ├── settings_store.py
│   │   ├── task_index.py
│   │   └── runtime_tools.py
│   ├── skills/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── registry.py
│   │   ├── executor.py
│   │   ├── permissions.py
│   │   ├── audit_log.py
│   │   ├── builtin/
│   │   │   └── __init__.py
│   │   └── plugins/
│   │       └── __init__.py
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── client.py
│   │   ├── manager.py
│   │   ├── executor.py
│   │   └── tool_adapter.py
│   └── ui/
│       ├── __init__.py
│       └── widgets/
│           ├── __init__.py
│           ├── chat_panel.py
│           ├── code_viewer_panel.py
│           ├── config_panel.py
│           ├── file_tree_panel.py
│           ├── session_panel.py
│           ├── tools_panel.py
│           ├── skills_panel.py
│           ├── mcp_panel.py
│           ├── patch_panel.py
│           └── splitter.py
├── .nju_code/settings.json
├── test_all_features.py
├── 需求文档.md
└── 项目管理计划.md
```

## 已实现功能

### 1) TUI 与会话能力
- 三栏布局（Explorer / Code+Tools+Model / Chat）
- 分割条拖拽调宽、聊天栏显示/隐藏
- 会话创建/切换/重命名/删除
- 流式输出与中断

### 2) 工作区与文件能力
- 在界面中打开工作区目录
- 文件树浏览与周期刷新
- 新建文件/目录、删除文件/目录
- 删除进入 `.nju_code/trash`，支持 `Undo Delete` 与 `Ctrl+Z` 连续撤销
- 代码查看与语法高亮
- 代码编辑保存（`Ctrl+S`）与重载
- Code Viewer 空态：未打开文件时显示 `NJU` 艺术字引导，占位态不显示代码框

### 3) 模型与上下文能力
- OpenAI 兼容流式接口接入
- 模型配置（`base_url`、`api_key`、`model`、`model_file`）
- 镜像预设切换
- 发送前支持 `@relative/path` 文件上下文注入
- 普通对话支持自动识别文件名、路径、函数名、类名并注入相关上下文

### 4) 检索与多文件分析（本地）
- 项目扫描与索引，自动过滤目录（`.git`、`venv`、`.venv`、`node_modules`、`__pycache__`）
- 文本检索（关键词/正则/大小写）
- Python 符号检索（`class`、`def`、`async def`、`import`）
- 文件摘要（类/函数/依赖/入口/作用）
- 基于 import 的依赖图与 1~2 层邻接分析
- 自然语言 Top-K 文件召回
- 影响面分析（风险等级 + 建议阅读顺序）
- 同名符号命中时支持自动排序，仅优先保留最相关的 1~2 个结果

### 5) 检索工作台（Tools 面板）
- 按钮：`Help`、`Scan`、`Search`、`Symbol`、`Summary`、`Deps`、`Recall`、`Impact`、`Tasks`、`Metrics`、`Doctor`
- 参数输入：`query`、`path`、`depth`、`top_k`
- 与聊天中的斜杠命令共用同一执行链路

### 6) Project Doctor 与综合测试
- 新增 Project Doctor 服务：检查项目结构、依赖声明、Python 语法、入口导入、分析引擎、代码块提取、上下文压缩、Settings、Patch、Skills、MCP、UI 结构、README 命令一致性和基础安全项
- 新增斜杠命令：`/doctor` 
- Tools 面板新增 `Doctor` 按钮，可直接触发项目自检
- 新增 `test_all_features.py`，使用标准库 `unittest` 对核心功能和整项工程进行综合回归测试

## 运行方式

### 环境准备
```bash
conda activate nju_code
pip install -r requirements.txt
```

### 启动应用
```bash
python main.py
```

### 分析命令
可在聊天输入框直接输入，或通过 Tools 工作台按钮触发：
```text
/help
/scan
/search <keyword> [--regex] [--case]
/symbol <name>
/summary <relative_path>
/deps <relative_path> [--depth 1|2]
/recall <requirement text> [--top 5..30]
/impact <symbol_or_relative_path> [--depth 1|2]
/tasks [--tag TODO|FIXME|BUG|HACK|NOTE|CHECKBOX] [--owner name] [--done] [--include-tests] [--top 50]
/metrics [--top 10] [--path text] [--include-tests]
/doctor [--verbose]
```

`/tasks` 用于扫描项目中的 `TODO`、`FIXME`、`BUG`、`HACK`、`NOTE` 标记以及 Markdown 清单项；Tools 面板也提供 `Tasks` 按钮，可用 `query` 输入框传入标签过滤。

`/metrics` 用于执行静态代码指标分析，输出 Python 文件复杂度、依赖 fan-in/fan-out、import 环和维护热点排序，可用于判断优先重构位置。

### 运行综合测试
```bash
python test_all_features.py
```

运行结束后会自动生成 Markdown 和 JSON 测试报告：

```text
.nju_code/reports/test_all_features_*.md
.nju_code/reports/test_all_features_*.json
```

也可以在应用聊天框或 Tools 面板中运行：
```text
/doctor
/doctor --verbose
```

`/doctor` 运行结束后也会自动保存 Markdown 和 JSON 报告：

```text
.nju_code/reports/project_doctor_*.md
.nju_code/reports/project_doctor_*.json
```

## 后续计划

### WBS-3 完善
- Session 记忆分层：短期会话摘要、长期会话归档、会话检索回放
- 上下文压缩链路：按 token 预算进行文件摘要压缩、历史对话压缩与去重
- 召回结果二次裁剪：Top-K 文件先摘要后拼接，避免上下文过长
- 上下文质量策略：优先保留符号定义、调用链与最近改动片段
- 输出结构补齐：保留机器可消费 JSON 到文件（非聊天窗口）用于后续 patch 接入

### WBS-4 Patch 工程修改能力
- 建立统一 patch 任务模型（约束输入，输出可审阅 diff）
- 建立安全流程：检索 -> 影响面分析 -> patch 方案
- 支持回滚

### WBS-5 Skills 体系
- 将检索/分析/补丁/测试拆分为可编排技能
- 增加技能级权限与调用日志

### WBS-6 模型与成本管理
- 统计 token、时延、失败率
- 支持多模型路由策略

### WBS-7 测试与质量保障
- 为分析引擎补齐单元测试
- 为关键 UI 交互补齐回归检查

## 团队成员与分工

| 成员 | 负责模块 | 主要工作 |
|------|----------|----------|
| 李承泽 | CLI/TUI 与交互框架 | 基于 Textual 的三栏布局（Explorer / Code+Tools+Model / Chat）、CSS 样式系统、键盘快捷键绑定、分割条拖拽、流式输出与中断、聊天面板、代码查看面板（语法高亮与编辑保存）、配置面板、文件树面板（工作区浏览、新建/删除/撤销删除）、Tools 工作台面板、整体事件调度与组件编排 |
| 丁一鸣 | Session 与上下文压缩 | 会话管理（创建/切换/重命名/删除/导出/导入）、会话面板 UI、ChatSession/ChatMessage 数据模型、AppState 全局状态管理、ContextCompressor 上下文压缩引擎（双语 token 估算、增量压缩、摘要质量验证与重试、自适应保留策略、压缩历史元数据追踪） |
| 周靖宇 | 代码检索与多文件分析 | CodeAnalyzer 代码分析引擎（项目扫描与索引、文本/正则检索、Python 符号检索、文件摘要、依赖图与邻接分析、自然语言 Top-K 召回、影响面分析）、CodeMetrics 静态代码指标（圈复杂度、fan-in/fan-out、import 环检测、维护热点排序）、TaskIndex 任务/TODO 扫描器、CodeExtractor 代码块提取、Project Doctor 项目自检系统 |
| 程楷诺 | Patch/回滚与执行引擎 | PatchEngine 补丁引擎（PatchStatus 生命周期状态机、PatchOperation 单文件变更与 diff 生成、PatchTask 多文件原子补丁、备份/应用/回滚/取消）、PatchHistoryStore JSON 持久化存储、PatchPanel 补丁 UI 面板（预览/确认/回滚/刷新） |
| 曹喆 | Skills 插件系统 | SkillRegistry 技能注册中心（内置技能/外部插件加载/命令映射/启停管理）、SkillExecutor 执行引擎（参数校验/权限检查/结果格式化）、PermissionChecker 权限控制系统、AuditLogger 审计日志、MCP 协议集成（Manager 管理器/Client 客户端/Executor 执行器/ToolAdapter 适配器）、SkillsPanel 与 MCPPanel UI 面板 |
| 陈志远 | 模型路由、成本统计、测试与CI | OpenAICompatibleClient 模型客户端（多镜像/多模型兼容流式接口）、ModelConfig 模型配置与镜像预设切换、SettingsStore 持久化配置、RuntimeTools 运行时工具、test_all_features.py 综合回归测试套件（unittest 标准库，覆盖核心服务与跨模块集成）、Markdown/JSON 测试报告生成 |

## 变更记录
详细改动见改动目录
