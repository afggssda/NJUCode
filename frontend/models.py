from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List
from uuid import uuid4


@dataclass
class ChatMessage:
    role: str
    content: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ChatSession:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    title: str = "New Chat"
    messages: List[ChatMessage] = field(default_factory=list)


@dataclass
class ToolToggle:
    key: str
    label: str
    description: str
    enabled: bool = True


@dataclass
class ModelConfig:
    base_url: str = "https://api.atlascloud.ai/v1"
    api_key: str = ""
    model: str = "deepseek-v3"
    model_file: str = ""
    mirror: str = "atlascloud"


DEFAULT_TOOLS: List[ToolToggle] = [
    ToolToggle(key="read_file", label="Read File", description="Allow reading workspace files", enabled=True),
    ToolToggle(key="write_file", label="Write File", description="Allow creating/editing files", enabled=True),
    ToolToggle(key="terminal", label="Terminal", description="Allow shell command execution", enabled=False),
    ToolToggle(key="web_fetch", label="Web Fetch", description="Allow external webpage fetch", enabled=False),
    ToolToggle(key="git", label="Git", description="Allow git status/diff operations", enabled=True),
]

MIRROR_PRESETS: Dict[str, str] = {
    "atlascloud": "https://api.atlascloud.ai/v1",
    "modelscope": "https://api-inference.modelscope.cn/v1",
    "official": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "azure_compatible": "https://YOUR_AZURE_ENDPOINT.openai.azure.com/openai/deployments/YOUR_DEPLOYMENT",
    "custom": "",
}
