"""LLM text enhancement over any OpenAI-compatible chat endpoint.

Takes raw Whisper transcript and optionally cleans or rewrites it. The backend
is chosen by config (see config.ENHANCE_PROVIDERS): Ollama locally, or
DeepSeek / OpenRouter / Groq in the cloud. They all speak the OpenAI chat API,
so a single `openai` SDK client — pointed at the provider's base_url — drives
every one of them.

Modes:
  raw     — return transcript completely unchanged (no API call)
  clean   — fix grammar, punctuation, remove filler words ("um", "uh", etc.)
  rewrite — turn rambling speech into polished, well-structured prose

Reasoning-model note:
    Some models (e.g. DeepSeek's v-series) are reasoning models that emit
    hidden reasoning tokens alongside the final answer. The SDK's
    `message.content` returns only the final answer (what we want), but those
    reasoning tokens count against `max_tokens`, so we keep that budget
    generous or a long transcript's answer can get truncated to empty. See the
    empty-content guard in enhance().

Why the meta-response guard exists:
    When Whisper returns very short or odd text, the model may respond with a
    message like "There is no text to correct" instead of the corrected text.
    We detect these meta-phrases and fall back to the raw transcript so the
    user doesn't have that sentence injected into their document.
"""

from openai import OpenAI

_PROMPTS = {
    "clean": (
        "You are a transcription editor. The user dictated the following text. "
        "Fix grammar, punctuation, and capitalization. Remove filler words like "
        "'um', 'uh', 'you know', 'like'. Keep the meaning and tone identical. "
        "Return ONLY the corrected text, nothing else."
    ),
    "rewrite": (
        "You are a professional writer. The user dictated the following rough speech. "
        "Rewrite it as clear, polished prose. Preserve the core meaning and intent. "
        "Return ONLY the rewritten text, nothing else."
    ),
}

# Phrases that indicate the LLM returned a meta-response instead of enhanced text.
# If detected, we return the original raw transcript instead.
_META_PHRASES = (
    "there is no text",
    "nothing to correct",
    "no text to",
    "text is empty",
    "no input",
)

# Generous completion budget. The DeepSeek models are reasoning models, so
# hidden reasoning tokens share this budget with the visible answer — too low
# and a long transcript's answer gets truncated to empty (we then fall back
# to raw). See the module docstring.
_MAX_TOKENS = 4096


class Enhancer:
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
    ):
        # timeout=30s — see transcriber.py for rationale.
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
        self._model = model

    def enhance(self, text: str, mode: str = "clean") -> str:
        """Enhance the transcript according to mode."""
        if mode == "raw" or not text.strip():
            return text

        system_prompt = _PROMPTS.get(mode, _PROMPTS["clean"])
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=_MAX_TOKENS,
        )
        # content is None if the response was truncated mid-reasoning before
        # any answer was produced — fall back to raw rather than crashing.
        result = (response.choices[0].message.content or "").strip()
        if not result:
            return text

        # Guard: fall back to raw if the model returned a meta-response
        if any(p in result.lower() for p in _META_PHRASES):
            return text

        return result
