# nju_code 前端（Textual）

这是一个 **Claude Code 风格的 Python 终端前端 MVP**，专注于你要求的基础能力：
- Chat 界面（支持多会话切换）
- 可查看文件代码
- 类 VSCode 的文件树
- 工具权限开关
- OpenAI 兼容模型配置（默认 ModelScope，可切换镜像源、可指定模型文件路径）

开发目录：`/mnt/c/Files/nju_code`

---

## 1) 目录组织（先规划后实现）

```text
nju_code/
├── main.py                         # 应用入口
├── requirements.txt                # 依赖
├── .env.example                    # 环境变量模板
├── README.md                       # 项目说明 + 模块分工
└── frontend/
    ├── __init__.py
    ├── app.py                      # Textual 主应用（布局/事件总线）
    ├── app.tcss                    # 样式布局
    ├── models.py                   # 数据模型（会话、消息、工具、模型配置）
    ├── state.py                    # 前端状态管理（会话、配置、持久化）
    ├── services/
    │   ├── __init__.py
    │   ├── settings_store.py       # 本地配置读写（.nju_code/settings.json）
    │   ├── openai_client.py        # OpenAI 兼容客户端封装（已接入流式调用）
    │   └── runtime_tools.py        # 最小后端执行能力（Hello World 示例）
    └── ui/
        ├── __init__.py
        └── widgets/
            ├── __init__.py
            ├── session_panel.py    # 多会话窗口（新建/切换）
            ├── chat_panel.py       # 聊天窗口（消息输入/展示）
            ├── file_tree_panel.py  # 文件树窗口（DirectoryTree）
            ├── code_viewer_panel.py# 代码查看窗口（语法高亮）
            ├── tools_panel.py      # 工具权限开关
            └── config_panel.py     # 模型与镜像配置
```

这个结构参考了你提供的 `easy-coding-agents` 的分层思想：
- `app.py` 做编排
- `services/` 放外部能力适配
- `ui/widgets/` 放可复用组件
- `state.py` 统一状态，避免 UI 与逻辑耦合

---

## 2) 每个人负责什么（建议分工）

> 下面是可直接执行的团队分工模板，你可以按成员名字替换。

- **前端架构负责人（Owner A）**
  - 负责 `frontend/app.py`、`frontend/app.tcss`
  - 统一布局规范、事件流、窗口切换体验

- **会话与交互负责人（Owner B）**
  - 负责 `session_panel.py`、`chat_panel.py`
  - 负责会话管理 UX、输入发送、消息渲染策略

- **文件工作区负责人（Owner C）**
  - 负责 `file_tree_panel.py`、`code_viewer_panel.py`
  - 负责文件树性能、代码高亮和大文件加载策略

- **平台接入负责人（Owner D）**
  - 负责 `services/openai_client.py`、`config_panel.py`
  - 负责 OpenAI 兼容 API 接入、镜像切换和模型文件配置

- **状态与配置负责人（Owner E）**
  - 负责 `state.py`、`services/settings_store.py`
  - 负责状态一致性、配置落盘与版本兼容

---

## 3) 当前已实现功能（MVP）

1. **Chat + 多会话**
   - 可新建会话
   - 可在会话列表切换

2. **文件树 + 代码查看**
   - 左侧 `DirectoryTree` 浏览工作区
  - 选择文件后切到右侧 Code Tab 查看语法高亮代码（与 Tools/Model 同区）

3. **工具权限开关**
   - 提供 Read/Write/Terminal/Web/Git 等工具开关
   - 开关状态持久化

4. **模型配置（OpenAI 格式）**
   - 配置 `base_url`、`api_key`、`model`
  - 默认配置为 ModelScope：`https://api-inference.modelscope.cn/v1` + `Qwen/Qwen3.5-35B-A3B`
  - 支持镜像预设：`modelscope` / `official` / `openrouter` / `azure_compatible` / `custom`
   - 支持指定 `model_file` 字段

5. **最小后端动作**
  - Tools 面板支持 `Run Hello World`
  - 会在工作区生成 `hello_world.py` 并执行，结果回显到 Chat

6. **配置持久化**
   - 自动写入：`.nju_code/settings.json`

---

## 4) 运行方式

```bash
cd /mnt/c/Files/nju_code
conda activate nju_code
pip install -r requirements.txt
python main.py
```

---

## 5) 下一步（后续你要我继续我可以直接做）

- 将 `openai_client.py` 从 stub 替换成真实流式调用（SSE / chunk）
- 聊天窗口支持 token 流式输出与中断
- 文件树加入忽略规则（`.gitignore` / 大文件阈值）
- 工具开关与后端真实工具权限绑定

## modelscope API
- 每位魔搭注册用户，当前每天允许进行总数(所有模型加和)为2000次的API-Inference调用。
- 每个模型均有额外单模型每日使用额度：根据资源、使用情况以及模型发布时间等因素动态调整。该额度最高不超过500，实际额度可远小于500。如遇到429错误，请切换其他模型，或等到第二天使用。
- https://www.modelscope.cn/my/access/token 配置API