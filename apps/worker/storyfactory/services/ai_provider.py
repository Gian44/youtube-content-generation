"""AI Provider abstraction for text generation (OpenAI & Gemini)."""

import json
from enum import Enum

from storyfactory.channel_context import effective_config, resolve_api_key, resolve_model
from storyfactory.logger import get_logger
from storyfactory.services.api_tracker import track_api_call

log = get_logger("ai_provider")


class AIProvider(Enum):
    OPENAI = "openai"
    GEMINI = "gemini"


def generate_text(
    prompt: str,
    provider: AIProvider = AIProvider.OPENAI,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1000,
    response_format: str | None = None,
) -> str:
    """Generate text using the specified AI provider.

    Args:
        prompt: The prompt to send
        provider: Which AI provider to use
        model: Specific model override
        temperature: Sampling temperature
        max_tokens: Maximum tokens in response
        response_format: Optional response format (e.g., 'json')

    Returns:
        Generated text content

    Raises:
        RuntimeError: If API key is missing or API call fails
    """
    if provider == AIProvider.OPENAI:
        return _generate_openai(prompt, model, temperature, max_tokens, response_format)
    elif provider == AIProvider.GEMINI:
        return _generate_gemini(prompt, model, temperature, max_tokens)
    else:
        raise ValueError(f"Unknown provider: {provider}")


def _generate_openai(
    prompt: str,
    model: str | None,
    temperature: float,
    max_tokens: int,
    response_format: str | None,
) -> str:
    """Generate text using OpenAI API."""
    api_key = resolve_api_key("text.openai", "openai_api_key")
    if not api_key or api_key.startswith("sk-your"):
        raise RuntimeError(
            "OpenAI API key not configured for the active channel. "
            "Configure the text.openai integration (or set OPENAI_API_KEY in .env).\n"
            "Get your key at: https://platform.openai.com/api-keys"
        )

    import openai

    client = openai.OpenAI(api_key=api_key)
    model_name = resolve_model("text.openai", "openai_text_model", explicit=model) or "gpt-4o"

    kwargs = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are a creative fiction writer specializing in engaging short stories. Always respond with valid JSON when requested."},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    log.info("openai_request", model=model_name, max_tokens=max_tokens)

    try:
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""

        # Track API usage
        track_api_call(
            provider="openai",
            endpoint=f"chat.completions/{model_name}",
            tokens_used=(response.usage.total_tokens if response.usage else 0),
            cost_estimate=_estimate_openai_cost(model_name, response.usage),
        )

        log.info(
            "openai_response",
            model=model_name,
            tokens=response.usage.total_tokens if response.usage else 0,
        )
        return content

    except openai.APIError as e:
        track_api_call(
            provider="openai",
            endpoint=f"chat.completions/{model_name}",
            error=str(e),
            status_code=getattr(e, "status_code", None),
        )
        log.error("openai_error", error=str(e))
        raise RuntimeError(f"OpenAI API error: {e}")


def _generate_gemini(
    prompt: str,
    model: str | None,
    temperature: float,
    max_tokens: int,
) -> str:
    """Generate text using Gemini API."""
    api_key = resolve_api_key("text.gemini", "gemini_api_key")
    if not api_key or api_key == "your-gemini-api-key":
        raise RuntimeError(
            "Gemini API key not configured for the active channel. "
            "Configure the text.gemini integration (or set GEMINI_API_KEY in .env).\n"
            "Get your key at: https://aistudio.google.com/apikey"
        )

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model_name = resolve_model("text.gemini", "gemini_text_model", explicit=model) or "gemini-2.0-flash"

    gen_model = genai.GenerativeModel(model_name)

    log.info("gemini_request", model=model_name, max_tokens=max_tokens)

    try:
        response = gen_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )

        content = response.text or ""

        track_api_call(
            provider="gemini",
            endpoint=f"generateContent/{model_name}",
            tokens_used=getattr(response, "usage_metadata", {}).get("total_token_count", 0) if hasattr(response, "usage_metadata") else 0,
        )

        log.info("gemini_response", model=model_name)
        return content

    except Exception as e:
        track_api_call(
            provider="gemini",
            endpoint=f"generateContent/{model_name}",
            error=str(e),
        )
        log.error("gemini_error", error=str(e))
        raise RuntimeError(f"Gemini API error: {e}")


def _estimate_openai_cost(model: str, usage) -> float:
    """Estimate cost in USD for OpenAI API call."""
    if not usage:
        return 0.0

    # Approximate pricing (per 1M tokens)
    pricing = {
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    }

    rates = pricing.get(model, {"input": 0.50, "output": 1.50})
    input_cost = (usage.prompt_tokens / 1_000_000) * rates["input"]
    output_cost = (usage.completion_tokens / 1_000_000) * rates["output"]
    return round(input_cost + output_cost, 6)


def select_provider_by_ratio() -> AIProvider:
    """Select a provider based on the active channel's configured ratios."""
    import random
    ratio = effective_config("tts_openai_ratio", 0.8)
    return AIProvider.OPENAI if random.random() < ratio else AIProvider.GEMINI
