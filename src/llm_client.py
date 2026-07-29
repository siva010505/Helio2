"""
LLM Client

Thin wrapper around NVIDIA NIM's OpenAI-compatible API.
Supports text generation and vision-based image scoring.
All calls are retried up to MAX_RETRIES times with exponential backoff.
"""

import os
import json
import time
import logging
from typing import Any

from openai import OpenAI, APIError, RateLimitError, APIConnectionError

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds


class LLMClient:
    """
    Wraps the NIM (NVIDIA) OpenAI-compatible chat-completions endpoint.

    Args:
        model (str): The text/chat model identifier.
        vision_model (str): The model to use for vision/image tasks.
        temperature (float): Sampling temperature for generation calls.
    """

    def __init__(
        self,
        model: str = "meta/llama-3.1-70b-instruct",
        vision_model: str = "meta/llama-3.2-11b-vision-instruct",
        temperature: float = 0.8,
    ):
        self.model = model
        self.vision_model = vision_model
        self.temperature = temperature

        api_key = os.getenv("NIM_API_KEY")
        base_url = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")

        if not api_key:
            raise ValueError(
                "NIM_API_KEY environment variable is not set. "
                "Copy .env.example to .env and add your key."
            )

        self.client = OpenAI(base_url=base_url, api_key=api_key)
        logger.info("LLMClient initialised | model=%s | vision_model=%s", self.model, self.vision_model)

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------

    def _chat_with_retry(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int = 2048,
    ) -> str:
        """Send a chat completion request with exponential backoff retries."""
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                content = response.choices[0].message.content
                if content is None:
                    raise ValueError("LLM returned an empty (None) message content.")
                return content.strip()

            except (RateLimitError, APIConnectionError) as exc:
                last_error = exc
                wait = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt, MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)

            except APIError as exc:
                # Non-retryable server-side errors
                logger.error("LLM APIError (non-retryable): %s", exc)
                raise

        raise RuntimeError(
            f"LLM call failed after {MAX_RETRIES} retries. Last error: {last_error}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """
        Generate a free-form text response from the chat model.

        Args:
            system_prompt: The system/context instruction.
            user_prompt: The user's actual request.
            temperature: Override the default temperature for this call.
            max_tokens: Maximum tokens to generate.

        Returns:
            The model's response as a plain string.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self._chat_with_retry(
            messages,
            model=self.model,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens,
        )

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> Any:
        """
        Generate a JSON-parseable response.

        The system prompt is automatically appended with a strict instruction
        to respond ONLY with valid JSON. Retries once with a stricter rephrasing
        if parsing fails.

        Args:
            system_prompt: The system/context instruction.
            user_prompt: The user's actual request.
            temperature: Override the default temperature.
            max_tokens: Maximum tokens to generate.

        Returns:
            Parsed Python object (dict or list).

        Raises:
            ValueError: If JSON cannot be parsed after retries.
        """
        json_system_prompt = (
            system_prompt.rstrip()
            + "\n\nCRITICAL: Your response MUST be valid JSON only — "
            "no markdown fences, no explanation, no trailing text."
        )
        messages = [
            {"role": "system", "content": json_system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        temp = temperature if temperature is not None else self.temperature

        raw = self._chat_with_retry(
            messages, model=self.model, temperature=temp, max_tokens=max_tokens
        )

        # Strip any accidental markdown fences
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            # Remove first and last fence lines
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            return json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            # One more pass: extract the first JSON object/array found
            import re
            match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1), strict=False)
                except json.JSONDecodeError:
                    pass
            logger.error("generate_json: failed to parse JSON.\nRaw output:\n%s", raw)
            raise ValueError(f"LLM did not return valid JSON. Raw output:\n{raw[:500]}")

    def score_image(
        self,
        description_prompt: str,
        image_data: str,
        temperature: float = 0.2,
    ) -> dict:
        """
        Use the vision model to score an image's suitability for a scene.

        Args:
            description_prompt: What the scene requires (e.g. "futuristic robot in a lab").
            image_data: Raw base64-encoded JPEG string (NOT a URL). The data URI
                        prefix will be added automatically if missing.
            temperature: Low temperature for deterministic scoring.

        Returns:
            dict with keys: score (0-10 int), reason (str).
        """
        system_prompt = (
            "You are a professional video editor's assistant. "
            "You evaluate whether a stock image or video clip is visually appropriate "
            "and high-quality for a given scene in a YouTube Shorts video. "
            "Respond with valid JSON only — no markdown fences."
        )
        user_prompt = (
            f"Scene requirement: {description_prompt}\n\n"
            "Score this image from 0 (completely wrong) to 10 (perfect match). "
            "Consider: visual clarity, relevance to scene, professional quality, "
            "and whether it would look good in a vertical 9:16 frame.\n\n"
            'Respond with JSON: {"score": <int 0-10>, "reason": "<one sentence>"}'
        )
        # Always build a valid data URI from raw base64
        if image_data.startswith("data:"):
            formatted_url = image_data
        elif image_data.startswith("http"):
            formatted_url = image_data  # public URL passthrough
        else:
            formatted_url = f"data:image/jpeg;base64,{image_data}"

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": formatted_url}},
                ],
            },
        ]

        raw = self._chat_with_retry(
            messages,
            model=self.vision_model,
            temperature=temperature,
            max_tokens=256,
        )

        cleaned = raw.strip().strip("`")
        try:
            result = json.loads(cleaned)
            return {"score": int(result.get("score", 0)), "reason": result.get("reason", "")}
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("score_image: could not parse vision response: %s", raw[:200])
            return {"score": 0, "reason": "parse error"}
