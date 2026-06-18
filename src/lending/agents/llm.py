"""
Gemini-backed reasoning step for agents (shared by #19–#23).

Produces a `ReasonFn` (the injectable step in the #16 scaffold) that calls Gemini
with **structured output** — the agent's Pydantic schema is handed to the model
as a response schema, so we get validated JSON back, never free text. The
scaffold then re-validates and gates it.

Config is env-driven:
  GOOGLE_API_KEY    — API key
  MODEL_NAME        — full model (heavier reasoning)        default gemini-3-flash-preview
  MODEL_NAME_LITE   — lite model (light classification)     default gemini-3.1-flash-lite-preview

Tests inject a fake `reason` instead of calling this, so the suite needs no key;
the live path is exercised with GOOGLE_API_KEY set.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from pydantic import BaseModel

from lending.agent_runtime.models import ReasonFn


def model_pro() -> str:
    return os.environ.get("MODEL_NAME", "gemini-3-flash-preview")


def model_lite() -> str:
    return os.environ.get("MODEL_NAME_LITE", "gemini-3.1-flash-lite-preview")


def _format_prompt(context: dict, tool_result: dict) -> str:
    payload = {"application": context, "tool_result": tool_result}
    return (
        "Classify the following application data and respond ONLY as JSON matching "
        "the provided schema.\n\n" + json.dumps(payload, default=str, indent=2)
    )


def gemini_reason(system_prompt: str, schema: type[BaseModel], *, model: Optional[str] = None) -> ReasonFn:
    """Build a ReasonFn that calls Gemini for structured output against `schema`."""
    chosen_model = model or model_lite()

    def reason(context: dict, tool_result: dict) -> dict:
        # Imported lazily so importing this module never requires the SDK/key.
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set")

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=chosen_model,
            contents=_format_prompt(context, tool_result),
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, BaseModel):
            return parsed.model_dump()
        if isinstance(parsed, dict):
            return parsed
        return json.loads(response.text)

    return reason
