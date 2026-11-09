"""Optional multi-provider LLM router with automatic fallback.

Used by the pipeline ONLY when USE_LLM=true. Primary route is OmniRoute
(which handles its own routing/fallback across providers). If that fails,
it falls back to Gemini, then Groq, then NVIDIA.

All providers are reached through OpenAI-compatible chat/completions
endpoints using the `requests` library, so no extra SDKs are required.
"""
import os
from typing import Optional

import requests

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 30

PROVIDERS = [
    {
        "name": "omniroute",
        "enabled": True,
        "api_key_var": "OMNIROUTE_API_KEY",
        "base_url": "https://api.omniroute.ai/v1",
        "model_var": "OMNIROUTE_MODEL",
        "default_model": DEFAULT_MODEL,
    },
    {
        "name": "gemini",
        "enabled": True,
        "api_key_var": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_var": "GEMINI_MODEL",
        "default_model": "gemini-2.0-flash",
    },
    {
        "name": "groq",
        "enabled": True,
        "api_key_var": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "model_var": "GROQ_MODEL",
        "default_model": "llama-3.3-70b-versatile",
    },
    {
        "name": "nvidia",
        "enabled": True,
        "api_key_var": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model_var": "NVIDIA_MODEL",
        "default_model": "meta/llama-3.1-405b-instruct",
    },
]


def _chat_completion(provider: dict, model: str, messages: list[dict], timeout: int) -> str:
    """Call one provider's chat/completions endpoint and return the text."""
    url = f"{provider['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {os.getenv(provider['api_key_var'], '')}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "messages": messages}
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def call_llm(
    prompt: str,
    system_prompt: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Try providers in order (OmniRoute -> Gemini -> Groq -> NVIDIA).

    Raises RuntimeError if every configured provider fails.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    for provider in PROVIDERS:
        api_key = os.getenv(provider["api_key_var"], "")
        if not api_key:
            continue
        model = os.getenv(provider["model_var"], provider["default_model"])
        try:
            text = _chat_completion(provider, model, messages, timeout)
            return text
        except Exception as exc:  # noqa: BLE001 - fall through on any error
            print(f"[llm] {provider['name']} failed: {exc}")

    raise RuntimeError("All LLM providers failed (no valid API key or unreachable).")


if __name__ == "__main__":
    # Standalone test: works with ANY single provider key set. Uses a
    # simple extractive prompt so it can run against the sample resume.
    try:
        result = call_llm("Reply with exactly: LLM OK")
        print("RESULT:", result)
    except RuntimeError as exc:
        print("NO PROVIDER:", exc)
