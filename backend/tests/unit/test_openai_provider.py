"""`OpenAIProvider` against a mocked transport — no network, no real key.

What these pin down: real results are never marked synthetic; only transient
failures are retried; the key never reaches an error message; and an answer
with no usable text is an error rather than an empty listing field.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.ai.exceptions import (
    AIProviderNotConfiguredError,
    AIProviderRejectedError,
    AIProviderUnavailableError,
    AIResponseMalformedError,
)
from app.ai.openai_provider import OpenAIProvider
from app.ai.provider import CompletionRequest, ImageAnalysisRequest

pytestmark = pytest.mark.unit

FAKE_KEY = "sk-test-never-sent-anywhere"


def _chat_body(content: str | None, *, finish: str = "stop") -> dict[str, Any]:
    return {
        "model": "test-model-2026",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


class _Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _provider(recorder: _Recorder, *, max_retries: int = 2) -> tuple[OpenAIProvider, list[float]]:
    sleeps: list[float] = []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = OpenAIProvider(
        api_key=FAKE_KEY,
        model="test-model",
        base_url="https://api.openai.test/v1/",
        request_timeout_seconds=5,
        connect_timeout_seconds=1,
        max_retries=max_retries,
        transport=httpx.MockTransport(recorder),
        sleep=_sleep,
    )
    return provider, sleeps


class TestComplete:
    async def test_a_real_answer_is_not_synthetic_and_carries_usage(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_chat_body("A sturdy steel bottle.")))
        provider, _ = _provider(recorder)

        result = await provider.complete(
            CompletionRequest(prompt="Write a title", system="You write listings")
        )

        assert result.text == "A sturdy steel bottle."
        assert result.is_synthetic is False
        assert result.provider == "openai"
        assert result.model == "test-model-2026"
        assert (result.input_tokens, result.output_tokens) == (12, 7)

    async def test_the_request_shape_sent_to_openai(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_chat_body("ok")))
        provider, _ = _provider(recorder)

        await provider.complete(
            CompletionRequest(prompt="P", system="S", max_output_tokens=64, temperature=0.2)
        )

        request = recorder.requests[0]
        assert str(request.url) == "https://api.openai.test/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["messages"] == [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "P"},
        ]
        assert payload["max_completion_tokens"] == 64
        # Reasoning models reject a non-default temperature (module docstring).
        assert "temperature" not in payload

    async def test_no_text_is_an_error_not_an_empty_listing(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_chat_body(None, finish="content_filter")))
        provider, _ = _provider(recorder)

        with pytest.raises(AIResponseMalformedError, match="content_filter"):
            await provider.complete(CompletionRequest(prompt="P"))


class TestRetries:
    async def test_a_5xx_is_retried_then_succeeds(self) -> None:
        recorder = _Recorder(
            httpx.Response(503, json={"error": {"message": "overloaded"}}),
            httpx.Response(200, json=_chat_body("done")),
        )
        provider, sleeps = _provider(recorder)

        result = await provider.complete(CompletionRequest(prompt="P"))

        assert result.text == "done"
        assert len(recorder.requests) == 2
        assert len(sleeps) == 1

    async def test_a_timeout_is_retried_until_the_budget_runs_out(self) -> None:
        recorder = _Recorder(*(httpx.ReadTimeout("slow") for _ in range(3)))
        provider, sleeps = _provider(recorder, max_retries=2)

        with pytest.raises(AIProviderUnavailableError):
            await provider.complete(CompletionRequest(prompt="P"))

        assert len(recorder.requests) == 3
        assert len(sleeps) == 2

    async def test_a_rate_limit_honours_retry_after_within_the_cap(self) -> None:
        recorder = _Recorder(
            httpx.Response(429, headers={"Retry-After": "3"}, json={"error": {"message": "slow"}}),
            httpx.Response(200, json=_chat_body("ok")),
        )
        provider, sleeps = _provider(recorder)

        await provider.complete(CompletionRequest(prompt="P"))

        assert sleeps and sleeps[0] >= 3

    async def test_a_400_is_not_retried(self) -> None:
        recorder = _Recorder(
            httpx.Response(400, json={"error": {"message": "model does not exist"}})
        )
        provider, sleeps = _provider(recorder)

        with pytest.raises(AIProviderRejectedError, match="model does not exist"):
            await provider.complete(CompletionRequest(prompt="P"))

        assert len(recorder.requests) == 1
        assert sleeps == []


class TestCredentials:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_an_auth_failure_is_not_retried_and_never_echoes_the_key(
        self, status: int
    ) -> None:
        # OpenAI's real 401 text includes a masked fragment of the key.
        recorder = _Recorder(
            httpx.Response(
                status, json={"error": {"message": f"Incorrect API key provided: {FAKE_KEY}"}}
            )
        )
        provider, _ = _provider(recorder)

        with pytest.raises(AIProviderNotConfiguredError) as raised:
            await provider.complete(CompletionRequest(prompt="P"))

        assert FAKE_KEY not in str(raised.value)
        assert len(recorder.requests) == 1


class TestAnalyseImage:
    async def test_a_json_answer_becomes_caption_and_alt_text(self) -> None:
        answer = json.dumps({"caption": "A red mug on a desk.", "alt_text": "Red ceramic mug"})
        recorder = _Recorder(httpx.Response(200, json=_chat_body(answer)))
        provider, _ = _provider(recorder)

        result = await provider.analyse_image(
            ImageAnalysisRequest(image_url="https://cdn.example/mug.png", instructions="Describe")
        )

        assert result.caption == "A red mug on a desk."
        assert result.alt_text == "Red ceramic mug"
        assert result.is_synthetic is False
        payload = json.loads(recorder.requests[0].content)
        assert payload["response_format"] == {"type": "json_object"}
        parts = payload["messages"][0]["content"]
        assert parts[1] == {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example/mug.png"},
        }

    @pytest.mark.parametrize(
        "content",
        ["not json", json.dumps({"caption": "only a caption"}), json.dumps(["a", "b"])],
    )
    async def test_an_unusable_answer_is_an_error(self, content: str) -> None:
        recorder = _Recorder(httpx.Response(200, json=_chat_body(content)))
        provider, _ = _provider(recorder)

        with pytest.raises(AIResponseMalformedError):
            await provider.analyse_image(
                ImageAnalysisRequest(image_url="https://cdn.example/x.png")
            )
