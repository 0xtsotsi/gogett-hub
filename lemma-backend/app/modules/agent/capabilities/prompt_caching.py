"""Prompt-caching capability for OpenAI-compatible providers.

Caching-aware providers reuse the request prefix automatically; the lever we
control is *session affinity* — routing a conversation's turns to the same
replica so the cached prefix is reused. We key affinity on the conversation id
(stable across turns), NOT the agent-run id (which changes every turn and would
scatter routing, defeating cross-turn reuse).

Provider-specific extensions (e.g. Fireworks ``x-session-affinity`` header) can
be layered in subclasses via ``configure_caching_capability()`` in assembler.py.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai.capabilities import AbstractCapability


class PromptCachingCapability(AbstractCapability[object]):
    """Pin per-conversation session affinity + prompt-cache key on each request."""

    def __init__(
        self, *, conversation_id: UUID, id: str | None = "prompt_caching"
    ) -> None:
        self._conversation_id = str(conversation_id)
        self._id = id

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return self._id

    def get_model_settings(self) -> dict[str, object]:
        affinity = self._conversation_id
        return {
            # OpenAI `user` field — used by compatible providers for sticky
            # replica routing so the cached prefix is hit across turns.
            "openai_user": affinity,
            # OpenAI prompt-cache key; honored by OpenAI and compatible providers.
            "openai_prompt_cache_key": affinity,
        }
