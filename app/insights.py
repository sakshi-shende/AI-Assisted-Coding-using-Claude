"""Generate short structured spending insights from expense data via Claude."""

from __future__ import annotations

import json
import logging
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 300
_FALLBACK: dict[str, object] = {
    "summary": "Insights are temporarily unavailable. Please try again later.",
    "bullets": [],
}
_SYSTEM_PROMPT = (
    "Respond with JSON only, no prose, no code fences. "
    'Return a single JSON object matching exactly this shape: '
    '{"summary": string, "bullets": [string, string, string]}. '
    "The bullets array must contain exactly three short strings."
)


def _build_summary(expenses: list[dict]) -> str:
    """Build a compact text summary of expenses for the prompt."""
    lines = [
        f"- {expense.get('amount_base_minor')} paise, "
        f"category={expense.get('category')}, status={expense.get('status')}"
        for expense in expenses
    ]
    return "\n".join(lines)


def _parse_insight(text: str) -> dict[str, object] | None:
    """Parse and validate a model response against the expected insight shape."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    summary = data.get("summary")
    bullets = data.get("bullets")
    if not isinstance(summary, str):
        return None
    if not isinstance(bullets, list) or len(bullets) != 3:
        return None
    if not all(isinstance(bullet, str) for bullet in bullets):
        return None

    return {"summary": summary, "bullets": bullets}


def generate_insight(expenses: list[dict]) -> dict[str, object]:
    """Ask Claude for a short JSON summary and three bullet insights.

    Args:
        expenses: A list of expense dicts, each expected to carry
            amount_base_minor, category, and status keys.

    Returns:
        A dict with a "summary" string and a "bullets" list of exactly
        three strings, or a safe fallback object if the API call fails
        or the response can't be parsed as valid JSON after one retry.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key.strip() if api_key else api_key)

    summary = _build_summary(expenses)
    prompt = (
        "Here is a list of expenses (amount in minor units of the base "
        "currency, category, status):\n\n"
        f"{summary}\n\n"
        "Summarize the spending in one short sentence and give exactly "
        "three short bullet insights about it."
    )

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIStatusError as e:
            logger.error("Anthropic API error (%s): %s", e.status_code, e.message)
            return _FALLBACK
        except anthropic.APIConnectionError as e:
            logger.error("Network error calling Anthropic API: %s", e)
            return _FALLBACK

        text = next((block.text for block in response.content if block.type == "text"), "")
        parsed = _parse_insight(text)
        if parsed is not None:
            return parsed

        logger.warning("Insight response failed JSON validation (attempt %d): %r", attempt + 1, text)

    return _FALLBACK
