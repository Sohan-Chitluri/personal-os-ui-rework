"""Provider-agnostic LLM interface for semantic extraction.

No vendor SDK imports. Concrete implementations live in llm_openrouter.py
(and future providers). Only the pipeline (SemanticExtractor, BackgroundProcessor)
imports from this module — never from a concrete provider directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personal_os.core.extraction.models import RawExtraction


class LLMProviderError(Exception):
    """Raised when an LLM provider call fails unrecoverably."""


@dataclass
class ToolDefinition:
    """Provider-agnostic tool definition."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    """Tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    """A single turn in a conversational exchange."""

    role: str  # "user", "assistant", "system", "tool"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class TokenUsage:
    """Input and output token accounting."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResponse:
    """Complete response returned by an LLM provider."""

    message: ChatMessage
    stop_reason: str = "end_turn"  # "end_turn", "tool_use", "max_tokens", "stop_sequence"
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: float = 0.0
    model: str = ""
    estimated_cost_usd: float = 0.0


@dataclass
class ChatStreamChunk:
    """Incremental streaming chunk emitted by chat_stream()."""

    delta_text: str | None = None
    delta_tool_call: dict[str, Any] | None = None
    stop_reason: str | None = None
    usage: TokenUsage | None = None


class LLMProvider(ABC):
    """Async LLM provider for semantic intent extraction and conversational interactions."""

    @abstractmethod
    async def extract(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> list[RawExtraction]:
        """Extract semantic knowledge items from a capture string.

        Args:
            text: Raw capture text to process.
            context: Optional hints — known project names, recent captures, etc.
                     Used for disambiguation; never required for correctness.

        Returns:
            RawExtraction list ordered by confidence descending. Empty list is
            valid (e.g. pure greetings, noise).

        Raises:
            LLMProviderError: On unrecoverable API or JSON parsing failure.
        """

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Execute a conversational completion with optional structured tool calling.

        Args:
            messages: List of conversation messages.
            tools: Optional list of available tool definitions.
            system_prompt: Optional instructions for model persona/rules.
            **kwargs: Extra model parameters (temperature, max_tokens, etc.).

        Returns:
            ChatResponse containing the assistant message, tool calls, and usage.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not implement chat()")

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Stream conversational chunks for real-time output.

        Default implementation raises NotImplementedError.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not implement chat_stream()")
        yield ChatStreamChunk()

    @abstractmethod
    async def close(self) -> None:
        """Release held resources (HTTP sessions, thread pools, etc.)."""


def get_provider(config: Any) -> LLMProvider:
    """Factory — returns the configured LLM provider instance.

    Args:
        config: Config, Settings, VaultConfig, or ModelConfig object with model configuration.

    Returns:
        A concrete LLMProvider ready for use.

    Raises:
        ValueError: If the configured provider is unknown.
    """
    from personal_os.core.llm_openrouter import OpenRouterProvider

    if isinstance(config, dict):
        model_cfg = config.get("model", config)
    else:
        model_cfg = getattr(config, "model", config)

    if isinstance(model_cfg, dict):
        provider = (
            model_cfg.get("provider")
            or model_cfg.get("llm_provider")
            or (config.get("llm_provider") if isinstance(config, dict) else None)
            or (config.get("provider") if isinstance(config, dict) else None)
            or "bedrock"
        )
        model = (
            model_cfg.get("model_name")
            or model_cfg.get("model")
            or model_cfg.get("bedrock_model_id")
            or (config.get("bedrock_model_id") if isinstance(config, dict) else None)
            or (config.get("model_name") if isinstance(config, dict) else None)
            or (config.get("model") if isinstance(config, dict) else None)
            or "us.amazon.nova-2-lite-v1:0"
        )
        base_url = (
            model_cfg.get("base_url")
            or (config.get("base_url") if isinstance(config, dict) else None)
            or "https://openrouter.ai/api/v1"
        )
        api_key = (
            model_cfg.get("api_key")
            or model_cfg.get("openrouter_api_key")
            or (config.get("openrouter_api_key") if isinstance(config, dict) else None)
            or (config.get("api_key") if isinstance(config, dict) else None)
        )
        region = (
            model_cfg.get("bedrock_region")
            or model_cfg.get("aws_region")
            or model_cfg.get("region")
            or (config.get("bedrock_region") if isinstance(config, dict) else None)
            or (config.get("aws_region") if isinstance(config, dict) else None)
            or "us-east-1"
        )
        timeout = float(
            model_cfg.get("bedrock_timeout_seconds")
            or (config.get("bedrock_timeout_seconds") if isinstance(config, dict) else None)
            or 30.0
        )
        max_tokens = int(
            model_cfg.get("bedrock_max_tokens")
            or (config.get("bedrock_max_tokens") if isinstance(config, dict) else None)
            or 4096
        )
        temperature = float(
            model_cfg.get("bedrock_temperature")
            or (config.get("bedrock_temperature") if isinstance(config, dict) else None)
            or 0.1
        )
    else:
        if not hasattr(model_cfg, "__dict__") and not hasattr(model_cfg, "provider"):
            model_cfg = config

        provider = (
            getattr(model_cfg, "llm_provider", None)
            or getattr(model_cfg, "provider", None)
            or getattr(config, "llm_provider", None)
            or getattr(config, "provider", None)
            or "bedrock"
        )
        model = (
            getattr(model_cfg, "bedrock_model_id", None)
            or getattr(model_cfg, "model_name", None)
            or getattr(model_cfg, "model", None)
            or getattr(config, "bedrock_model_id", None)
            or getattr(config, "model_name", None)
            or getattr(config, "model", None)
            or "us.amazon.nova-2-lite-v1:0"
        )
        if not isinstance(model, str):
            model = "us.amazon.nova-2-lite-v1:0"

        base_url = (
            getattr(model_cfg, "base_url", None)
            or getattr(config, "base_url", None)
            or "https://openrouter.ai/api/v1"
        )
        api_key = (
            getattr(model_cfg, "openrouter_api_key", None)
            or getattr(model_cfg, "api_key", None)
            or getattr(config, "openrouter_api_key", None)
            or getattr(config, "api_key", None)
        )
        region = (
            getattr(model_cfg, "bedrock_region", None)
            or getattr(model_cfg, "aws_region", None)
            or getattr(config, "bedrock_region", None)
            or getattr(config, "aws_region", None)
            or "us-east-1"
        )
        timeout = float(
            getattr(model_cfg, "bedrock_timeout_seconds", None)
            or getattr(config, "bedrock_timeout_seconds", None)
            or 30.0
        )
        max_tokens = int(
            getattr(model_cfg, "bedrock_max_tokens", None)
            or getattr(config, "bedrock_max_tokens", None)
            or 4096
        )
        temperature = float(
            getattr(model_cfg, "bedrock_temperature", None)
            or getattr(config, "bedrock_temperature", None)
            or 0.1
        )

    if provider == "openrouter":
        return OpenRouterProvider(
            model=model,
            base_url=base_url,
            api_key=api_key,
        )

    if provider == "bedrock":
        from personal_os.core.llm_bedrock import BedrockNovaProvider

        return BedrockNovaProvider(
            model_id=model,
            region_name=region,
            timeout_seconds=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    raise ValueError(f"Unknown LLM provider: {provider!r}")

