# NJUCode

`NJUCode` 是一个基于 Textual 的终端代码助手原型。项目正在从“聊天壳”逐步演进为可检索、可分析、可安全修改代码的 Code Agent。

## 使用前请自行替换API key https://www.modelscope.cn/my/access/token
### 替换.env文件的OPENAI_API_KEY即可

## 当前进度
- 已完成：WBS-1（项目初始化）、WBS-2（CLI 与会话系统）
- 基本完成：WBS-3（上下文分析与检索，含检索工作台）
- 说明：`Session 管理增强`已补强到“多会话 + 文件安全删除/撤销 + 基础交互修复”，`上下文压缩`仍未完成
- 下一重点：WBS-4（基于 Patch 的工程修改能力）

## 项目结构
```text
NJUCode/
├── .env.example
├── main.py
├── requirements.txt
├── LICENSE
├── README.md
├── hello_world.py
├── 改动/
│   ├── 3.31_jingyu_change.md
│   ├── 4.16_jingyu_change.md
│   └── idea.md
├── frontend/
│   ├── __init__.py
│   ├── app.py
│   ├── app.tcss
│   ├── models.py
│   ├── state.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── openai_client.py
│   │   ├── code_analysis.py
│   │   ├── settings_store.py
│   │   └── runtime_tools.py
│   └── ui/
│       ├── __init__.py
│       └── widgets/
│           ├── __init__.py
│           ├── chat_panel.py
│           ├── code_viewer_panel.py
│           ├── config_panel.py
│           ├── file_tree_panel.py
│           ├── session_panel.py
│           ├── splitter.py
│           └── tools_panel.py
├── .nju_code/settings.json
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
- 按钮：`Help`、`Scan`、`Search`、`Symbol`、`Summary`、`Deps`、`Recall`、`Impact`
- 参数输入：`query`、`path`、`depth`、`top_k`
- 与聊天中的斜杠命令共用同一执行链路

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
- 支持 dry-run 与回滚

### WBS-5 Skills 体系
- 将检索/分析/补丁/测试拆分为可编排技能
- 增加技能级权限与调用日志

### WBS-6 模型与成本管理
- 统计 token、时延、失败率
- 支持多模型路由策略

### WBS-7 测试与质量保障
- 为分析引擎补齐单元测试
- 为关键 UI 交互补齐回归检查

## 变更记录
- 2026-03-31 详细改动见：`改动/3.31_jingyu_change.md`
- 2026-04-16 详细改动见：`改动/4.16_jingyu_change.md`
