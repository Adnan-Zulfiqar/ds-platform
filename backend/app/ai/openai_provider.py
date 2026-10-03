"""`AIProvider` backed by the OpenAI Chat Completions API.

Plain ``httpx`` rather than the ``openai`` SDK: the two calls needed here are
one POST each, ``httpx`` is already a pinned, hash-locked runtime dependency,
and the retry policy has to match the platform's own (transient failures
only, full-jitter backoff) rather than the SDK's defaults.

**Temperature is not sent.** OpenAI's reasoning models reject any value other
than their default, and a provider that fails depending on which model an
operator picks is worse than one that ignores a tuning hint. Every model then
runs at its own default; ``CompletionRequest.temperature`` is honoured only by
providers whose models all accept it.

Nothing here logs a prompt, a response or the key. Errors carry OpenAI's own
error message (which never contains the key) so a FAILED prompt execution row
says why.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from app.ai.exceptions import (
    AIError,
    AIProviderNotConfiguredError,
    AIProviderRejectedError,
    AIProviderUnavailableError,
    AIResponseMalformedError,
)
from app.ai.provider import (
    CompletionRequest,
    CompletionResult,
    ImageAnalysisRequest,
    ImageAnalysisResult,
)
from app.core.logging import get_logger
from app.integrations.rate_limiter import compute_backoff

logger = get_logger(__name__)

_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0
#: OpenAI's error text is short, but it ends up in a database row.
_ERROR_DETAIL_LIMIT = 500

_IMAGE_JSON_INSTRUCTION = (
    'Reply with a JSON object with exactly two string fields: "caption" '
    '(one sentence describing the product image) and "alt_text" (concise '
    "alt text for a shop listing, under 125 characters)."
)

Sleep = Callable[[float], Awaitable[None]]


class OpenAIProvider:
    """Real model calls; every result has ``is_synthetic=False``."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        request_timeout_seconds: float,
        connect_timeout_seconds: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout = httpx.Timeout(request_timeout_seconds, connect=connect_timeout_seconds)
        self._max_retries = max_retries
        # Injected only by tests (httpx.MockTransport, an instant sleep).
        self._transport = transport
        self._sleep = sleep

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        body = await self._post(
            {
                "model": self._model,
                "messages": messages,
                "max_completion_tokens": request.max_output_tokens,
            }
        )
        choice, text = _first_choice_text(body)
        usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else {}
        assert isinstance(usage, Mapping)
        return CompletionResult(
            text=text,
            provider=self.name,
            model=str(body.get("model") or self._model),
            input_tokens=_int_or_none(usage.get("prompt_tokens")),
            output_tokens=_int_or_none(usage.get("completion_tokens")),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            is_synthetic=False,
        )

    async def analyse_image(self, request: ImageAnalysisRequest) -> ImageAnalysisResult:
        instructions = (
            f"{request.instructions}\n\n{_IMAGE_JSON_INSTRUCTION}"
            if request.instructions
            else _IMAGE_JSON_INSTRUCTION
        )
        body = await self._post(
            {
                "model": self._model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instructions},
                            {"type": "image_url", "image_url": {"url": request.image_url}},
                        ],
                    }
                ],
                "response_format": {"type": "json_object"},
            }
        )
        _, text = _first_choice_text(body)
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise AIResponseMalformedError(
                "OpenAI returned image analysis that is not JSON."
            ) from exc
        caption = parsed.get("caption") if isinstance(parsed, dict) else None
        alt_text = parsed.get("alt_text") if isinstance(parsed, dict) else None
        if not (isinstance(caption, str) and caption.strip()):
            raise AIResponseMalformedError("OpenAI image analysis has no caption.")
        if not (isinstance(alt_text, str) and alt_text.strip()):
            raise AIResponseMalformedError("OpenAI image analysis has no alt text.")
        return ImageAnalysisResult(
            caption=caption.strip(),
            alt_text=alt_text.strip(),
            provider=self.name,
            model=str(body.get("model") or self._model),
            is_synthetic=False,
        )

    async def _post(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        """POST with retries for transient failures only; never for a 4xx
        other than 429."""
        attempt = 0
        while True:
            try:
                return await self._post_once(payload)
            except AIError as exc:
                if not exc.retryable or attempt >= self._max_retries:
                    raise
                delay = compute_backoff(
                    attempt, base_seconds=_BACKOFF_BASE_SECONDS, max_seconds=_BACKOFF_MAX_SECONDS
                )
                retry_after = getattr(exc, "retry_after_seconds", None)
                if retry_after:
                    delay = max(delay, min(float(retry_after), _BACKOFF_MAX_SECONDS))
                logger.warning(
                    "ai_provider_retrying",
                    provider=self.name,
                    attempt=attempt + 1,
                    error_code=exc.code,
                    delay_seconds=round(delay, 2),
                )
                await self._sleep(delay)
                attempt += 1

    async def _post_once(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(self._url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AIProviderUnavailableError("OpenAI did not answer in time.") from exc
        except httpx.TransportError as exc:
            raise AIProviderUnavailableError("OpenAI could not be reached.") from exc

        status = response.status_code
        if status < 400:
            try:
                body = response.json()
            except ValueError as exc:
                raise AIResponseMalformedError("OpenAI returned a non-JSON response.") from exc
            if not isinstance(body, dict):
                raise AIResponseMalformedError("OpenAI returned an unexpected response shape.")
            return body

        if status in (401, 403):
            # No detail: OpenAI's 401 text echoes a masked fragment of the key.
            raise AIProviderNotConfiguredError(
                f"OpenAI refused the platform credentials (HTTP {status})."
            )
        detail = _error_detail(response)
        if status == 429:
            raise AIProviderUnavailableError(
                f"OpenAI rate limit reached (HTTP 429): {detail}",
                retry_after_seconds=_retry_after(response),
            )
        if status >= 500:
            raise AIProviderUnavailableError(f"OpenAI server error (HTTP {status}): {detail}")
        raise AIProviderRejectedError(f"OpenAI rejected the request (HTTP {status}): {detail}")


def _first_choice_text(body: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise AIResponseMalformedError("OpenAI returned no choices.")
    choice = choices[0]
    message = choice.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        # A refusal or a length cut-off with no text: nothing usable to store.
        reason = choice.get("finish_reason") or "unknown"
        raise AIResponseMalformedError(f"OpenAI returned no text (finish_reason={reason}).")
    return choice, content


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "no error body"
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return str(message)[:_ERROR_DETAIL_LIMIT] if message else "no error message"


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


__all__ = ["OpenAIProvider"]
