"""上下文压缩模块。

负责估算会话 token 用量、判断是否需要压缩，
并在需要时调用模型生成历史摘要，将长历史裁剪为"摘要 + 近期消息"。

"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..models import ChatMessage, ModelConfig
    from .openai_client import OpenAICompatibleClient


# 中文字符平均约 1.5 token，英文约 0.25 token/字符，此处统一用保守估算
_CHARS_PER_TOKEN = 3


@dataclass
class CompressionResult:
    """封装一次压缩操作的结果。"""

    summary: str
    kept_messages: List["ChatMessage"]
    removed_count: int
    token_before: int
    token_after: int


class ContextCompressor:
    """会话上下文压缩器。

    Args:
        client: 已初始化的 OpenAI 兼容客户端。
        model_config: 当前模型配置，用于摘要生成请求。
        token_threshold: 超过此 token 数时触发压缩，默认 3000。
        keep_recent: 压缩后保留的最近消息数量，默认 6。
    """

    def __init__(
        self,
        client: "OpenAICompatibleClient",
        model_config: "ModelConfig",
        token_threshold: int = 3000,
        keep_recent: int = 6,
    ) -> None:
        self._client = client
        self._model_config = model_config
        self.token_threshold = token_threshold
        self.keep_recent = keep_recent

    # ------------------------------------------------------------------
    # Token 估算
    # ------------------------------------------------------------------

    def estimate_message_tokens(self, message: "ChatMessage") -> int:
        """估算单条消息的 token 数量。

        使用字符数 / _CHARS_PER_TOKEN 近似，避免引入 tiktoken 依赖。
        role 前缀固定加 4 token 开销。
        """
        return max(1, len(message.content) // _CHARS_PER_TOKEN) + 4

    def estimate_tokens(self, messages: List["ChatMessage"]) -> int:
        """估算消息列表的总 token 数量。"""
        return sum(self.estimate_message_tokens(m) for m in messages)

    def needs_compression(self, messages: List["ChatMessage"]) -> bool:
        """判断当前消息列表是否超过压缩阈值。"""
        return self.estimate_tokens(messages) >= self.token_threshold

    # ------------------------------------------------------------------
    # 摘要生成
    # ------------------------------------------------------------------

    def _build_summary_prompt(self, messages: List["ChatMessage"]) -> str:
        """将消息列表格式化为摘要请求的提示词。"""
        header = (
            "你是一个对话历史整理助手。请将下方的对话历史整理为结构化摘要，"
            "供后续对话继续使用。要求如下：\n"
            "1. **用户意图**：用1-2句话说明用户在这段对话中想解决什么问题。\n"
            "2. **关键结论**：列出助手给出的重要结论、建议或决策（可多条）。\n"
            "3. **代码与文件**：若涉及代码修改、函数名、文件路径，逐条列出，不要省略。\n"
            "4. **未解决问题**：若有用户提出但尚未完成的任务，单独列出。\n"
            "5. **重要上下文**：记录对后续对话有影响的背景信息（如使用的语言、框架、配置等）。\n\n"
            "输出格式示例：\n"
            "【用户意图】用户希望实现 Python 异步文件读取功能。\n"
            "【关键结论】\n- 推荐使用 aiofiles 库\n- 需要在调用处加 await\n"
            "【代码与文件】\n- 修改了 utils/file_reader.py 中的 read() 方法\n"
            "【未解决问题】\n- 错误处理逻辑尚未实现\n"
            "【重要上下文】项目使用 Python 3.11，框架为 FastAPI。\n\n"
            "现在请整理以下对话历史：\n"
        )
        lines = [header]
        for msg in messages:
            role_label = "用户" if msg.role == "user" else "助手"
            snippet = msg.content[:800].strip()
            if len(msg.content) > 800:
                snippet += "…（已截断）"
            lines.append(f"[{role_label}]: {snippet}")
        return "\n".join(lines)

    def generate_summary(self, messages: List["ChatMessage"]) -> str:
        """调用模型为给定消息列表生成摘要。

        若 API Key 未配置或调用失败，返回降级文本摘要（拼接前 N 条内容）。
        """
        if not self._model_config.api_key.strip():
            # 降级：拼接前几条消息的内容片段
            parts = [f"[{m.role}]: {m.content[:100]}" for m in messages[:4]]
            return "（无 API Key，降级摘要）\n" + "\n".join(parts)

        from .openai_client import OpenAIRequest

        prompt = self._build_summary_prompt(messages)
        request = OpenAIRequest(
            base_url=self._model_config.base_url,
            api_key=self._model_config.api_key,
            model=self._model_config.model,
            messages=[{"role": "user", "content": prompt}],
        )
        result = self._client.chat(request)
        if result.startswith("[系统错误]"):
            # 降级处理
            parts = [f"[{m.role}]: {m.content[:100]}" for m in messages[:4]]
            return "（摘要生成失败，降级摘要）\n" + "\n".join(parts)
        return result

    # ------------------------------------------------------------------
    # 压缩主入口
    # ------------------------------------------------------------------

    def compress(self, messages: List["ChatMessage"]) -> CompressionResult:
        """对消息列表执行压缩，返回压缩结果。

        压缩策略：
        1. 保留最近 keep_recent 条消息
        2. 对其余历史调用模型生成摘要
        3. 返回摘要文本 + 近期消息列表

        若消息数量不足以压缩（≤ keep_recent），直接返回空摘要结果。
        """
        token_before = self.estimate_tokens(messages)

        if len(messages) <= self.keep_recent:
            return CompressionResult(
                summary="",
                kept_messages=list(messages),
                removed_count=0,
                token_before=token_before,
                token_after=token_before,
            )

        to_summarize = messages[: -self.keep_recent]
        kept = list(messages[-self.keep_recent :])

        summary = self.generate_summary(to_summarize)
        token_after = self.estimate_tokens(kept) + max(1, len(summary) // _CHARS_PER_TOKEN)

        return CompressionResult(
            summary=summary,
            kept_messages=kept,
            removed_count=len(to_summarize),
            token_before=token_before,
            token_after=token_after,
        )
