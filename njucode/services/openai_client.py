from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Iterator

from openai import OpenAI


@dataclass
class OpenAIRequest:
    """封装一次 OpenAI 兼容请求所需参数。

    字段覆盖基础连接信息、模型名称、用户消息，
    以及可选的模型上下文文件路径。
    该对象在 UI 层和服务层之间传递，避免参数散落。
    """

    base_url: str
    api_key: str
    model: str
    messages: list[dict[str, str]]
    model_file: str = ""
    file_contexts: list[tuple[str, str]] | None = None


class OpenAICompatibleClient:
    def __init__(self) -> None:
        """初始化 OpenAI 兼容客户端包装器。

        该类不长期持有底层网络连接对象，
        仅记录最近一次请求参数，便于调试与追踪。
        """
        self.last_request: OpenAIRequest | None = None

    def _build_messages(self, request: OpenAIRequest) -> list[dict[str, str]]:
        """构造发送给模型接口的消息列表。"""
        # 复制外部通过传递得到的完整历史记录
        messages: list[dict[str, str]] = list(request.messages)
        
        system_content = ""
        
        # 组装当前会话被额外 @ 引入的文件内容
        if getattr(request, "file_contexts", None):
            system_content += "会话中附带的文件上下文：\n"
            for rel_path, content in request.file_contexts:
                system_content += f"--- {rel_path} ---\n{content[:2000]}\n\n"
                
        # 组装全局指定的模型系统文件上下文
        if request.model_file.strip():
            file_path = Path(request.model_file).expanduser()
            if file_path.exists() and file_path.is_file():
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                system_content += f"配置的指定文件内容:\n{content[:4000]}\n\n"
                
        # 注入 system prompt
        if system_content.strip():
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": system_content.strip()
                }
            )
            
        return messages

    def stream_chat(self, request: OpenAIRequest, stop_event: Event | None = None) -> Iterator[str]:
        """以流式方式调用模型并逐片返回文本。

        Args:
            request: 本次调用参数。
            stop_event: 可选停止事件；被置位时会尽快退出循环。

        Yields:
            模型连续返回的文本分片。
        """
        self.last_request = request
        if request.api_key.strip() == "":
            yield "[系统提示] 当前未设置 API Key，仅展示前端界面流程。"
            return

        client = OpenAI(base_url=request.base_url, api_key=request.api_key)
        messages = self._build_messages(request)
        response = client.chat.completions.create(
            model=request.model,
            messages=messages,
            stream=True,
        )

        try:
            for chunk in response:
                if stop_event and stop_event.is_set():
                    break
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def chat(self, request: OpenAIRequest) -> str:
        """执行非流式风格调用并返回完整文本。

        该方法内部复用 `stream_chat` 聚合所有分片，
        并在异常时返回可读的系统错误信息。

        Args:
            request: 模型请求参数。

        Returns:
            最终拼接后的回复文本。
        """
        chunks: list[str] = []
        try:
            for text in self.stream_chat(request):
                chunks.append(text)
        except Exception as error:
            return f"[系统错误] 调用模型失败: {error}"

        content = "".join(chunks).strip()
        return content or "[系统提示] 模型返回为空。"
