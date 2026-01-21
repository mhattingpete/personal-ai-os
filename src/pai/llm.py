"""LLM provider abstraction for PAI.

Supports both Claude API and local llama.cpp server.
All LLM calls should go through this module.
"""

import json
from typing import Any, Protocol, TypeVar

import anthropic
import httpx
from pydantic import BaseModel

from pai.config import get_settings

T = TypeVar("T", bound=BaseModel)


class Message(BaseModel):
    """Chat message."""

    role: str  # "user", "assistant", or "system"
    content: str


class Response(BaseModel):
    """LLM response."""

    content: str
    model: str
    usage: dict[str, int] | None = None
    stop_reason: str | None = None


class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    async def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Response:
        """Generate a completion from messages."""
        ...

    async def complete_structured(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> T:
        """Generate a structured response matching the schema."""
        ...


class ClaudeProvider:
    """Claude API provider using the Anthropic SDK."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        settings = get_settings().llm.claude
        self.api_key = api_key or settings.api_key
        self.model = model or settings.model

        if not self.api_key:
            raise ValueError(
                "Claude API key not set. Set ANTHROPIC_API_KEY env var or configure in ~/.config/pai/config.yaml"
            )

        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)

    async def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Response:
        """Generate a completion from messages."""
        # Convert messages to Anthropic format
        anthropic_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]

        # Build system prompt from system messages
        system_parts = [m.content for m in messages if m.role == "system"]
        if system:
            system_parts.insert(0, system)
        system_prompt = "\n\n".join(system_parts) if system_parts else None

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt or anthropic.NOT_GIVEN,
            messages=anthropic_messages,
        )

        return Response(
            content=response.content[0].text if response.content else "",
            model=response.model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            stop_reason=response.stop_reason,
        )

    async def complete_structured(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> T:
        """Generate a structured response matching the schema."""
        # Build schema description
        schema_json = schema.model_json_schema()
        schema_str = json.dumps(schema_json, indent=2)

        # Augment system prompt with schema instructions
        schema_instruction = f"""You must respond with valid JSON that matches this schema:

{schema_str}

Respond ONLY with the JSON object, no markdown code blocks or explanations."""

        full_system = f"{system}\n\n{schema_instruction}" if system else schema_instruction

        response = await self.complete(
            messages,
            system=full_system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Parse and validate response
        content = response.content.strip()
        # Remove markdown code blocks if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        data = json.loads(content)
        return schema.model_validate(data)


class LlamaCppProvider:
    """Local llama.cpp server provider via OpenAI-compatible API."""

    def __init__(self, url: str | None = None, model: str | None = None):
        settings = get_settings().llm.local
        self.url = (url or settings.url).rstrip("/")
        self.model = model or settings.model
        self.client = httpx.AsyncClient(timeout=120.0)

    async def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Response:
        """Generate a completion from messages."""
        # Build messages list with system prompt
        api_messages: list[dict[str, str]] = []
        if system:
            api_messages.append({"role": "system", "content": system})

        for m in messages:
            api_messages.append({"role": m.role, "content": m.content})

        response = await self.client.post(
            f"{self.url}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": api_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        return Response(
            content=choice["message"]["content"],
            model=data.get("model", self.model),
            usage=data.get("usage"),
            stop_reason=choice.get("finish_reason"),
        )

    async def complete_structured(
        self,
        messages: list[Message],
        schema: type[T],
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> T:
        """Generate a structured response matching the schema."""
        # Build schema description
        schema_json = schema.model_json_schema()
        schema_str = json.dumps(schema_json, indent=2)

        schema_instruction = f"""You must respond with valid JSON that matches this schema:

{schema_str}

Respond ONLY with the JSON object, no markdown code blocks or explanations."""

        full_system = f"{system}\n\n{schema_instruction}" if system else schema_instruction

        # Build messages list
        api_messages: list[dict[str, str]] = [
            {"role": "system", "content": full_system}
        ]
        for m in messages:
            api_messages.append({"role": m.role, "content": m.content})

        # Request with JSON mode if supported
        response = await self.client.post(
            f"{self.url}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": api_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return schema.model_validate(parsed)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()


def get_provider(name: str | None = None) -> LLMProvider:
    """Get an LLM provider by name.

    Args:
        name: Provider name ("claude" or "local"). Defaults to config setting.

    Returns:
        Configured LLM provider.
    """
    settings = get_settings().llm
    provider_name = name or settings.default

    if provider_name == "claude":
        return ClaudeProvider()
    elif provider_name == "local":
        return LlamaCppProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}")


def should_use_local(context: dict[str, Any] | None = None) -> bool:
    """Determine if local model should be used based on routing rules.

    Args:
        context: Optional context with "domains" key listing data domains involved.

    Returns:
        True if local model should be used.
    """
    settings = get_settings().llm.routing

    # User override: always use local
    if settings.force_local:
        return True

    # Check if any sensitive domains are involved
    if context and "domains" in context:
        for domain in context["domains"]:
            if domain.lower() in settings.sensitive_domains:
                return True

    return False
