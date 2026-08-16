"""Provider-agnostic LLM interface for semantic extraction.

No vendor SDK imports. Concrete implementations live in llm_openrouter.py
(and future providers). Only the pipeline (SemanticExtractor, BackgroundProcessor)
imports from this module — never from a concrete provider directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from personal_os.core.extraction.models import RawExtraction


class LLMProviderError(Exception):
    """Raised when an LLM provider call fails unrecoverably."""


class LLMProvider(ABC):
    """Async LLM provider for semantic intent extraction."""

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

    @abstractmethod
    async def close(self) -> None:
        """Release held resources (HTTP sessions, thread pools, etc.)."""


def get_provider(config: Any) -> LLMProvider:
    """Factory — returns the configured LLM provider instance.

    Args:
        config: VaultConfig or ModelConfig object with .provider and .model_name.

    Returns:
        A concrete LLMProvider ready for use.

    Raises:
        ValueError: If the configured provider is unknown.
    """
    from personal_os.core.llm_openrouter import OpenRouterProvider

    provider = getattr(config, "provider", "openrouter")
    if provider == "openrouter":
        return OpenRouterProvider(
            model=getattr(config, "model_name", "google/gemma-4-31b-it:free"),
            base_url=getattr(config, "base_url", "https://openrouter.ai/api/v1"),
        )
    raise ValueError(f"Unknown LLM provider: {provider!r}")
