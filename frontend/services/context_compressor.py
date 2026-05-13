"""上下文压缩模块。

负责估算会话 token 用量、判断是否需要压缩，
并在需要时调用模型生成历史摘要，将长历史裁剪为"摘要 + 近期消息"。

改进内容：
  - 双语 token 估算（CJK 字符与 ASCII 字符分别计算，提高准确度）
  - 增量压缩支持（已有摘要时合并旧摘要而非丢弃，避免信息丢失）
  - 摘要质量验证与自动重试（确保结构化输出合规）
  - 基于消息平均大小的自适应 keep_recent（防止压缩后仍超限）
  - 压缩历史元数据追踪（CompressionRecord，支持统计与展示）
  - 降级摘要结构化输出（即使无 API Key 也保持摘要格式一致）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from ..models import ChatMessage, ModelConfig
    from .openai_client import OpenAICompatibleClient


# -----------------------------------------------------------------------
# Token 估算常量
# -----------------------------------------------------------------------
# CJK（中日韩）统一表意文字：LLM tokenizer 中通常 1 字符 ≈ 1.5 token
_CJK_CHARS_PER_TOKEN: float = 1.5
# ASCII 字母/数字/标点：BPE 统计均值约 4 字符 = 1 token
_ASCII_CHARS_PER_TOKEN: float = 4.0
# 每条消息固定开销（role 前缀 + 边界标记）
_MSG_OVERHEAD_TOKENS: int = 4
# 每个 Unicode 代理对字符（emoji 等）估算 token 开销
_SURROGATE_TOKEN_COST: int = 2

# 结构化摘要中至少须包含其中一个节标题才视为有效
_REQUIRED_SUMMARY_SECTIONS: List[str] = ["【用户意图】", "【关键结论】"]

# 摘要文本的最低字符数，低于此值视为无效摘要
_MIN_SUMMARY_LENGTH: int = 30


@dataclass
class CompressionResult:
    """封装一次压缩操作的全量结果。

    Attributes:
        summary: 模型生成（或降级生成）的历史摘要文本。
        kept_messages: 压缩后保留的近期消息列表。
        removed_count: 被摘要替代而移除的消息数量。
        token_before: 压缩前消息列表的估算 token 数。
        token_after: 压缩后（摘要 + 近期消息）的估算 token 数。
        compression_ratio: 压缩率（token_after / token_before），越小压缩效果越好。
        generated_at: 压缩操作完成时间。
        used_fallback: 是否因为 API 不可用或摘要质量不足而使用了降级摘要。
    """

    summary: str
    kept_messages: List["ChatMessage"]
    removed_count: int
    token_before: int
    token_after: int
    compression_ratio: float = 1.0
    generated_at: datetime = field(default_factory=datetime.now)
    used_fallback: bool = False


@dataclass
class CompressionRecord:
    """记录单次压缩操作的元数据，用于历史追踪与统计。

    Attributes:
        compressed_at: 压缩操作发生的时间。
        messages_removed: 本次被移除（转为摘要）的消息数量。
        token_before: 压缩前估算 token 数。
        token_after: 压缩后估算 token 数。
        tokens_saved: 本次压缩节省的 token 数量（token_before - token_after）。
        summary_length: 生成的摘要文本字符数。
        used_fallback: 是否使用了降级摘要策略。
        session_title: 发生压缩的会话标题（可选）。
    """

    compressed_at: datetime
    messages_removed: int
    token_before: int
    token_after: int
    tokens_saved: int
    summary_length: int
    used_fallback: bool
    session_title: str = ""

    def format_summary_line(self) -> str:
        """格式化为单行展示文本，用于日志或 UI 提示。"""
        ts = self.compressed_at.strftime("%H:%M:%S")
        ratio_pct = int((1.0 - self.token_after / self.token_before) * 100) if self.token_before else 0
        fallback_mark = " [降级]" if self.used_fallback else ""
        return (
            f"[{ts}] 移除 {self.messages_removed} 条消息，"
            f"节省 {self.tokens_saved} tokens（{ratio_pct}%）{fallback_mark}"
        )


class ContextCompressor:
    """会话上下文压缩器。

    核心职责：
    - 估算消息列表的 token 用量（双语感知）
    - 判断是否需要压缩（与阈值比较）
    - 执行压缩：将旧消息摘要化 + 保留近期消息
    - 支持增量压缩：已有摘要时合并而非丢弃
    - 自动验证摘要质量并重试

    Args:
        client: 已初始化的 OpenAI 兼容客户端。
        model_config: 当前模型配置，用于摘要生成请求。
        token_threshold: 超过此 token 数时触发压缩，默认 3000。
        keep_recent: 压缩后最少保留的最近消息数量（自适应策略可能超过此值），默认 6。
        max_summary_retries: 摘要质量不合格时的最大重试次数，默认 2。
        min_messages_to_compress: 消息总数低于此值时不触发压缩，默认 4。
    """

    def __init__(
        self,
        client: "OpenAICompatibleClient",
        model_config: "ModelConfig",
        token_threshold: int = 3000,
        keep_recent: int = 6,
        max_summary_retries: int = 2,
        min_messages_to_compress: int = 4,
    ) -> None:
        self._client = client
        self._model_config = model_config
        self.token_threshold = token_threshold
        self.keep_recent = keep_recent
        self.max_summary_retries = max_summary_retries
        self.min_messages_to_compress = min_messages_to_compress
        self._compression_history: List[CompressionRecord] = []

    # ------------------------------------------------------------------
    # Token 估算
    # ------------------------------------------------------------------

    @staticmethod
    def _count_cjk_chars(text: str) -> int:
        """统计文本中 CJK（中日韩）及相关 Unicode 块的字符数量。

        覆盖范围：
        - CJK 统一表意文字（U+4E00–U+9FFF）
        - 平假名 / 片假名（U+3040–U+30FF）
        - 韩文音节（U+AC00–U+D7AF）
        - CJK 扩展 A（U+3400–U+4DBF）
        - CJK 兼容表意文字（U+F900–U+FAFF）
        """
        count = 0
        for ch in text:
            cp = ord(ch)
            if (
                0x4E00 <= cp <= 0x9FFF
                or 0x3040 <= cp <= 0x30FF
                or 0xAC00 <= cp <= 0xD7AF
                or 0x3400 <= cp <= 0x4DBF
                or 0xF900 <= cp <= 0xFAFF
            ):
                count += 1
        return count

    @classmethod
    def estimate_text_tokens_static(cls, text: str) -> int:
        """使用双语规则估算任意文本 token 数量（不含消息固定开销）。"""
        if not text:
            return 0
        cjk_count = cls._count_cjk_chars(text)
        ascii_count = len(text) - cjk_count
        return max(0, int(cjk_count / _CJK_CHARS_PER_TOKEN + ascii_count / _ASCII_CHARS_PER_TOKEN))

    @classmethod
    def estimate_message_tokens_from_content(cls, content: str) -> int:
        """使用双语规则估算单条消息 token 数量（含消息固定开销）。"""
        return max(1, cls.estimate_text_tokens_static(content or "")) + _MSG_OVERHEAD_TOKENS

    def estimate_message_tokens(self, message: "ChatMessage") -> int:
        """估算单条消息的 token 数量（双语感知）。

        采用双语分区策略：
        - CJK 字符按 1.5 字符/token 计算
        - 其余字符按 4 字符/token 计算
        - role 前缀固定加 4 token 开销

        Returns:
            估算 token 数（最小为 1 + overhead）。
        """
        return self.estimate_message_tokens_from_content(message.content or "")

    def estimate_text_tokens(self, text: str) -> int:
        """估算任意文本的 token 数量（不含消息固定开销）。

        Args:
            text: 任意字符串。

        Returns:
            估算 token 数（最小为 0）。
        """
        return self.estimate_text_tokens_static(text)

    def estimate_tokens(self, messages: List["ChatMessage"]) -> int:
        """估算消息列表的总 token 数量。

        Args:
            messages: 待估算的消息列表。

        Returns:
            所有消息 token 估算值之和。
        """
        return sum(self.estimate_message_tokens(m) for m in messages)

    def needs_compression(self, messages: List["ChatMessage"]) -> bool:
        """判断当前消息列表是否达到需要压缩的阈值。

        Args:
            messages: 当前会话的消息列表。

        Returns:
            True 表示 token 估算值已达到或超过阈值，应触发压缩。
        """
        return self.estimate_tokens(messages) >= self.token_threshold

    def get_token_usage_ratio(self, messages: List["ChatMessage"]) -> float:
        """返回当前 token 用量与阈值的比值。

        Returns:
            比值（例如 0.85 表示已用 85%）；阈值为 0 时返回 0.0。
        """
        if self.token_threshold <= 0:
            return 0.0
        return self.estimate_tokens(messages) / self.token_threshold

    def _compute_adaptive_keep_recent(self, messages: List["ChatMessage"]) -> int:
        """根据消息平均 token 大小自适应调整保留消息数量。

        策略：目标是让保留部分的 token 预算约为压缩阈值的 50%，
        从而在压缩后为后续新消息留出足够空间。

        Args:
            messages: 当前消息列表。

        Returns:
            建议保留的消息条数，限制在 [2, keep_recent * 2] 范围内。
        """
        if not messages:
            return self.keep_recent
        total = self.estimate_tokens(messages)
        avg = total / len(messages) if messages else 1
        target_budget = int(self.token_threshold * 0.50)
        adaptive = max(2, int(target_budget / max(avg, 1)))
        return max(2, min(adaptive, self.keep_recent * 2))

    # ------------------------------------------------------------------
    # 摘要质量验证
    # ------------------------------------------------------------------

    def _validate_summary(self, summary: str) -> bool:
        """验证摘要是否符合最低质量要求。

        检查项：
        1. 摘要不为空且长度不低于最低字符数
        2. 摘要中包含至少一个必要的结构化节标题

        Args:
            summary: 模型返回的摘要文本。

        Returns:
            True 表示摘要质量达标；False 表示需要重试或降级。
        """
        if not summary or len(summary.strip()) < _MIN_SUMMARY_LENGTH:
            return False
        return any(section in summary for section in _REQUIRED_SUMMARY_SECTIONS)

    # ------------------------------------------------------------------
    # 摘要提示词构建
    # ------------------------------------------------------------------

    def _build_summary_prompt(
        self,
        messages: List["ChatMessage"],
        existing_summary: Optional[str] = None,
        session_title: Optional[str] = None,
    ) -> str:
        """将消息列表格式化为摘要请求的提示词。

        支持增量压缩模式：当传入 existing_summary 时，生成合并旧摘要与
        新消息历史的综合摘要，保证历史信息不因多次压缩而丢失。

        Args:
            messages: 待摘要的消息列表（历史中较早的部分）。
            existing_summary: 会话已有的旧摘要（可选）。
            session_title: 会话标题，为摘要提供背景（可选）。

        Returns:
            格式化完毕的摘要提示词字符串。
        """
        prefix_parts: List[str] = []

        if session_title and session_title not in ("New Chat",) and not session_title.startswith("Chat "):
            prefix_parts.append(f"【会话标题】{session_title}\n")

        if existing_summary:
            prefix_parts.append(
                "【已有历史摘要（更早期的对话）】\n"
                f"{existing_summary}\n\n"
                "注意：以上是更早期对话的摘要。请在整理下方新增对话片段时，"
                "将已有摘要的关键结论纳入考虑，生成一份**合并后的完整摘要**，"
                "确保不遗漏已有摘要中的重要信息。\n"
            )

        context_prefix = "\n".join(prefix_parts)
        if context_prefix:
            context_prefix += "\n"

        header = (
            f"{context_prefix}"
            "你是一个对话历史整理助手。请将下方的对话历史整理为结构化摘要，"
            "供后续对话继续使用。要求如下：\n"
            "1. **【用户意图】**：用1-2句话说明用户在这段对话中想解决什么问题。\n"
            "2. **【关键结论】**：列出助手给出的重要结论、建议或决策（可多条）。\n"
            "3. **【代码与文件】**：若涉及代码修改、函数名、文件路径，逐条列出，不要省略。\n"
            "4. **【未解决问题】**：若有用户提出但尚未完成的任务，单独列出。\n"
            "5. **【重要上下文】**：记录对后续对话有影响的背景信息"
            "（如使用的语言、框架、配置、已确认的架构决策等）。\n\n"
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
            # 截断过长消息，避免 prompt 超出模型限制
            snippet = msg.content[:1000].strip()
            if len(msg.content) > 1000:
                snippet += "…（已截断）"
            lines.append(f"[{role_label}]: {snippet}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 降级摘要生成
    # ------------------------------------------------------------------

    def _build_fallback_summary(
        self,
        messages: List["ChatMessage"],
        existing_summary: Optional[str] = None,
    ) -> str:
        """生成降级摘要（无 API Key 或模型调用失败时使用）。

        降级摘要尽量保持与正常摘要相同的结构化格式，
        以便后续代码无需区分来源即可正常渲染和使用。

        Args:
            messages: 待摘要的消息列表。
            existing_summary: 已有旧摘要（若有则一并保留）。

        Returns:
            结构化降级摘要文本。
        """
        lines: List[str] = ["【用户意图】（自动降级摘要——模型不可用）"]

        if existing_summary:
            # 保留已有摘要中的关键信息（截取前 400 字符避免过长）
            condensed = existing_summary[:400].replace("\n", " ").strip()
            lines.append(f"\n已有摘要片段：{condensed}")

        lines.append("\n【关键结论】")
        for msg in messages[:8]:
            role_label = "用户" if msg.role == "user" else "助手"
            snippet = msg.content[:200].replace("\n", " ").strip()
            if snippet:
                lines.append(f"- [{role_label}]: {snippet}")

        lines.append(
            "\n【重要上下文】历史消息已被截断，"
            "以上为系统自动生成的片段预览，仅供参考。"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 摘要生成（带重试）
    # ------------------------------------------------------------------

    def generate_summary(
        self,
        messages: List["ChatMessage"],
        existing_summary: Optional[str] = None,
        session_title: Optional[str] = None,
    ) -> Tuple[str, bool]:
        """调用模型为给定消息列表生成结构化摘要。

        流程：
        1. 若无 API Key，直接返回降级摘要。
        2. 构建提示词并调用模型，最多重试 max_summary_retries 次。
        3. 每次生成后进行质量验证；验证通过则立即返回。
        4. 所有尝试均失败或质量不足时，返回降级摘要。

        Args:
            messages: 需要摘要的消息列表（通常是历史中较早的部分）。
            existing_summary: 已有旧摘要（增量压缩时传入，合并到新摘要中）。
            session_title: 会话标题（可选，提供上下文背景）。

        Returns:
            (摘要文本, 是否使用了降级) 的元组。
            降级标志为 True 表示摘要质量或来源不完全可靠。
        """
        if not self._model_config.api_key.strip():
            return self._build_fallback_summary(messages, existing_summary), True

        from .openai_client import OpenAIRequest

        last_error: str = ""
        for attempt in range(self.max_summary_retries + 1):
            prompt = self._build_summary_prompt(messages, existing_summary, session_title)
            request = OpenAIRequest(
                base_url=self._model_config.base_url,
                api_key=self._model_config.api_key,
                model=self._model_config.model,
                messages=[{"role": "user", "content": prompt}],
            )
            result = self._client.chat(request)

            if result.startswith("[系统错误]"):
                last_error = result
                # 模型调用本身失败，重试无意义，直接降级
                break

            if self._validate_summary(result):
                return result, False

            # 摘要质量不足：若还有重试机会则继续，否则接受当前结果（优于降级）
            if attempt >= self.max_summary_retries:
                # 质量不达标但已超重试次数：若摘要有一定内容则接受，否则降级
                if len(result.strip()) >= _MIN_SUMMARY_LENGTH:
                    return result, False
                break

        # 所有尝试均以失败/质量不足告终
        _ = last_error  # 保留供后续调试
        return self._build_fallback_summary(messages, existing_summary), True

    # ------------------------------------------------------------------
    # 压缩主入口
    # ------------------------------------------------------------------

    def compress(
        self,
        messages: List["ChatMessage"],
        existing_summary: Optional[str] = None,
        session_title: Optional[str] = None,
    ) -> CompressionResult:
        """对消息列表执行压缩并返回结果。

        压缩策略：
        1. 若消息数量 ≤ min_messages_to_compress，不触发压缩，直接返回。
        2. 根据消息平均大小自适应计算保留条数（_compute_adaptive_keep_recent）。
        3. 将历史部分（非近期）送入模型生成摘要。
        4. 若已有旧摘要，执行增量压缩——生成合并了旧摘要的新摘要。
        5. 记录本次压缩的元数据到 _compression_history。

        Args:
            messages: 当前会话的完整消息列表。
            existing_summary: 已有旧摘要文本（增量压缩时传入）。
            session_title: 会话标题（可选）。

        Returns:
            CompressionResult 封装的压缩结果。
        """
        token_before = self.estimate_tokens(messages)

        # 消息数量不足时直接返回，不记录历史
        if len(messages) <= self.min_messages_to_compress:
            return CompressionResult(
                summary=existing_summary or "",
                kept_messages=list(messages),
                removed_count=0,
                token_before=token_before,
                token_after=token_before,
                compression_ratio=1.0,
            )

        adaptive_keep = self._compute_adaptive_keep_recent(messages)
        to_summarize = messages[:-adaptive_keep]
        kept = list(messages[-adaptive_keep:])

        summary, used_fallback = self.generate_summary(
            to_summarize, existing_summary, session_title
        )
        summary_tokens = self.estimate_text_tokens(summary)
        token_after = self.estimate_tokens(kept) + summary_tokens

        compression_ratio = token_after / token_before if token_before > 0 else 1.0
        tokens_saved = max(0, token_before - token_after)

        # 记录本次压缩元数据
        record = CompressionRecord(
            compressed_at=datetime.now(),
            messages_removed=len(to_summarize),
            token_before=token_before,
            token_after=token_after,
            tokens_saved=tokens_saved,
            summary_length=len(summary),
            used_fallback=used_fallback,
            session_title=session_title or "",
        )
        self._compression_history.append(record)

        return CompressionResult(
            summary=summary,
            kept_messages=kept,
            removed_count=len(to_summarize),
            token_before=token_before,
            token_after=token_after,
            compression_ratio=compression_ratio,
            generated_at=record.compressed_at,
            used_fallback=used_fallback,
        )

    # ------------------------------------------------------------------
    # 压缩历史查询
    # ------------------------------------------------------------------

    def get_compression_history(self) -> List[CompressionRecord]:
        """返回本次运行期间所有压缩操作的历史记录列表（按时间顺序）。"""
        return list(self._compression_history)

    def get_total_tokens_saved(self) -> int:
        """返回本次运行期间所有压缩操作累计节省的 token 总量。"""
        return sum(r.tokens_saved for r in self._compression_history)

    def get_compression_count(self) -> int:
        """返回本次运行期间发生的压缩操作总次数。"""
        return len(self._compression_history)

    def format_compression_stats(self) -> str:
        """将压缩历史格式化为可读统计摘要字符串。

        Returns:
            若无历史记录返回提示文本；否则返回按行排列的统计信息。
        """
        if not self._compression_history:
            return "本次运行期间尚未发生压缩操作。"
        lines = [f"压缩操作记录（共 {len(self._compression_history)} 次）："]
        for record in self._compression_history:
            lines.append("  " + record.format_summary_line())
        lines.append(f"累计节省 token：{self.get_total_tokens_saved()}")
        return "\n".join(lines)
