"""
Unified interface over multiple free-tier LLM providers with automatic
fallback. Each provider function takes the same input (system prompt, user
text, tool list) and returns the same output shape:

    {"action": "<tool_name>", "params": {...}, "provider_used": "<name>"}

If a provider errors (rate limit, no key, network issue), we try the next
one in settings.provider_order. This is the "resilience" piece worth
highlighting in interviews - free tiers WILL rate-limit you.
"""
import json
import time
from typing import Any, Dict, Optional

import requests

from config import settings
from schemas import TOOLS_OPENAI_FORMAT, to_gemini_tools

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
# ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


class ProviderError(Exception):
    pass


def _call_groq(system_prompt: str, user_text: str) -> Dict[str, Any]:
    if not settings.groq_api_key:
        raise ProviderError("no groq key configured")
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": settings.groq_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "tools": TOOLS_OPENAI_FORMAT,
            "tool_choice": "required",
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ProviderError(f"groq {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    call = data["choices"][0]["message"]["tool_calls"][0]["function"]
    return {"action": call["name"], "params": json.loads(call["arguments"])}


def _call_mistral(system_prompt: str, user_text: str) -> Dict[str, Any]:
    if not settings.mistral_api_key:
        raise ProviderError("no mistral key configured")
    resp = requests.post(
        MISTRAL_URL,
        headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
        json={
            "model": settings.mistral_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "tools": TOOLS_OPENAI_FORMAT,
            "tool_choice": "any",
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ProviderError(f"mistral {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    call = data["choices"][0]["message"]["tool_calls"][0]["function"]
    return {"action": call["name"], "params": json.loads(call["arguments"])}


# def _call_anthropic(system_prompt: str, user_text: str) -> Dict[str, Any]:
#     if not settings.anthropic_api_key:
#         raise ProviderError("no anthropic key configured")
#     resp = requests.post(
#         ANTHROPIC_URL,
#         headers={
#             "x-api-key": settings.anthropic_api_key,
#             "anthropic-version": "2023-06-01",
#             "content-type": "application/json",
#         },
#         json={
#             "model": settings.anthropic_model,
#             "max_tokens": 512,
#             "system": system_prompt,
#             "messages": [{"role": "user", "content": user_text}],
#             "tools": to_anthropic_tools(),
#             "tool_choice": {"type": "any"},
#         },
#         timeout=20,
#     )
#     if resp.status_code != 200:
#         raise ProviderError(f"anthropic {resp.status_code}: {resp.text[:200]}")
#     data = resp.json()
#     tool_block = next(b for b in data["content"] if b["type"] == "tool_use")
#     return {"action": tool_block["name"], "params": tool_block["input"]}

def _call_gemini(system_prompt: str, user_text: str) -> Dict[str, Any]:
    if not settings.gemini_api_key:
        raise ProviderError("no gemini key configured")
    url = GEMINI_URL_TMPL.format(model=settings.gemini_model, key=settings.gemini_api_key)
    resp = requests.post(
        url,
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "tools": to_gemini_tools(),
            "tool_config": {"function_calling_config": {"mode": "ANY"}},
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ProviderError(f"gemini {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    part = data["candidates"][0]["content"]["parts"][0]["functionCall"]
    return {"action": part["name"], "params": part.get("args", {})}


_PROVIDER_FUNCS = {
    "groq": _call_groq,
    "mistral": _call_mistral,
    # "anthropic": _call_anthropic,
    "gemini": _call_gemini,
}

SYSTEM_PROMPT = (
    "You are a voice assistant intent parser. Given a spoken command, "
    "call exactly one of the available tools with the right arguments. "
    "Never respond with plain text - always use a tool call."
)


def parse_intent(user_text: str, extra_context: Optional[str] = None) -> Dict[str, Any]:
    """Try each configured provider in order until one succeeds."""
    system_prompt = SYSTEM_PROMPT
    if extra_context:
        system_prompt += f"\n\nAdditional context:\n{extra_context}"

    errors = []
    for name in settings.provider_order:
        name = name.strip()
        fn = _PROVIDER_FUNCS.get(name)
        if not fn:
            continue
            
        print(f"[provider] trying {name}...")
        t0 = time.time()
        try:
            result = fn(system_prompt, user_text)
            elapsed = time.time() - t0
            print(f"[provider] {name} succeeded in {elapsed:.2f}s")
            result["provider_used"] = name
            return result
        except ProviderError as e:
            elapsed = time.time() - t0
            print(f"[provider] {name} FAILED in {elapsed:.2f}s: {e}")
            errors.append(str(e))
            continue

    raise ProviderError(f"all providers failed: {errors}")


def _generate_groq(system_prompt: str, user_text: str) -> str:
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": settings.groq_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ProviderError(f"groq {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]


def _generate_mistral(system_prompt: str, user_text: str) -> str:
    resp = requests.post(
        MISTRAL_URL,
        headers={"Authorization": f"Bearer {settings.mistral_api_key}"},
        json={
            "model": settings.mistral_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ProviderError(f"mistral {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]


# def _generate_anthropic(system_prompt: str, user_text: str) -> str:
#     resp = requests.post(
#         ANTHROPIC_URL,
#         headers={
#             "x-api-key": settings.anthropic_api_key,
#             "anthropic-version": "2023-06-01",
#             "content-type": "application/json",
#         },
#         json={
#             "model": settings.anthropic_model,
#             "max_tokens": 400,
#             "system": system_prompt,
#             "messages": [{"role": "user", "content": user_text}],
#         },
#         timeout=20,
#     )
#     if resp.status_code != 200:
#         raise ProviderError(f"anthropic {resp.status_code}: {resp.text[:200]}")
#     return next(b["text"] for b in resp.json()["content"] if b["type"] == "text")

def _generate_gemini(system_prompt: str, user_text: str) -> str:
    url = GEMINI_URL_TMPL.format(model=settings.gemini_model, key=settings.gemini_api_key)
    resp = requests.post(
        url,
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise ProviderError(f"gemini {resp.status_code}: {resp.text[:200]}")
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


_GENERATE_FUNCS = {
    "groq": (_generate_groq, "groq_api_key"),
    "mistral": (_generate_mistral, "mistral_api_key"),
    # "anthropic": (_generate_anthropic, "anthropic_api_key"),
    "gemini": (_generate_gemini, "gemini_api_key"),

}


def generate_text(system_prompt: str, user_text: str) -> str:
    """Plain (non-tool-calling) generation, used for drafting message
    content in the user's style. Same provider fallback pattern."""
    errors = []
    for name in settings.provider_order:
        name = name.strip()
        entry = _GENERATE_FUNCS.get(name)
        if not entry:
            continue
        fn, key_attr = entry
        if not getattr(settings, key_attr):
            continue
        try:
            return fn(system_prompt, user_text)
        except ProviderError as e:
            errors.append(str(e))
            continue
    raise ProviderError(f"all providers failed: {errors}")
