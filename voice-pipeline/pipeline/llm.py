"""Streams the caller's reply from an OpenAI-compatible chat server (vLLM).
Also asks it for one whole reply, to score a finished call (scoring.py).

Settings (environment variables):
    LLM_URL     default http://localhost:8100/v1/chat/completions
    LLM_MODEL   default Qwen/Qwen3-8B-FP8
    LLM_REPETITION_PENALTY, LLM_PRESENCE_PENALTY
                against the caller looping on the same lines (seen in testing)

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

import httpx

log = logging.getLogger("pipeline.llm")


class ChatLLM:
    def __init__(self, url: str, model: str, *, repetition_penalty: float = 1.1,
                 presence_penalty: float = 0.3, temperature: float = 0.8,
                 max_tokens: int = 160, client: httpx.AsyncClient | None = None):
        self.url = url
        self.model = model
        self.client = client or httpx.AsyncClient(timeout=60)
        self.options = {
            "model": model, "stream": True, "max_tokens": max_tokens,
            "temperature": temperature, "repetition_penalty": repetition_penalty,
            "presence_penalty": presence_penalty,
            # Qwen3 thinks before answering unless told not to; too slow for a call.
            "chat_template_kwargs": {"enable_thinking": False},
        }

    @classmethod
    def from_env(cls) -> ChatLLM:
        env = os.environ.get
        return cls(
            env("LLM_URL", "http://localhost:8100/v1/chat/completions"),
            env("LLM_MODEL", "Qwen/Qwen3-8B-FP8"),
            repetition_penalty=float(env("LLM_REPETITION_PENALTY", "1.1")),
            presence_penalty=float(env("LLM_PRESENCE_PENALTY", "0.3")),
        )

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Yield the reply's text as it is generated."""
        body = {**self.options, "messages": messages}
        async with self.client.stream("POST", self.url, json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                choices = json.loads(line[6:]).get("choices") or [{}]
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta

    async def complete(self, messages: list[dict], *, max_tokens: int = 500) -> str:
        """One whole reply, asked for as a JSON object. Used to score a call.

        Steadier settings than the caller's: low temperature and no penalties,
        which would push the model away from repeating the key names.
        """
        body = {**self.options, "messages": messages, "stream": False,
                "max_tokens": max_tokens, "temperature": 0.2,
                "repetition_penalty": 1.0, "presence_penalty": 0.0,
                "response_format": {"type": "json_object"}}
        response = await self.client.post(self.url, json=body)
        if response.status_code == 400:
            # Most likely a server that does not know response_format. The
            # prompt asks for JSON as well, and the caller checks the answer
            # either way. Servers word this error differently, so any 400 is
            # tried again once; a 400 comes back at once, so it costs little.
            # The error's text is not logged: it can quote the conversation.
            log.warning("the LLM refused the scoring request (400, names "
                        "response_format: %s); trying without response_format",
                        "response_format" in response.text)
            del body["response_format"]
            response = await self.client.post(self.url, json=body)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"] or ""
