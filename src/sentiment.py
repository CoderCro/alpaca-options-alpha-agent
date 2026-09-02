"""FinBERT sentiment scoring for Company C's Featherless veto context only.

Fetches recent news headlines for a ticker via Alpaca's own news endpoint
(same CLI, same credential path as everything else -- no separate data
source), then scores them with ProsusAI/finbert via Hugging Face's hosted
Inference API (no local model load -- keeps the scheduler's per-cycle
subprocess startup fast and avoids adding torch/transformers as dependencies
for one signal).

Best-effort only, by design: any failure (missing token, no news, API
error, unrecognized response shape) returns None rather than raising --
sentiment is extra context for the veto call, same as the rest of
signal_details, never a requirement to trade. Company A/B never call this.
"""

import os
import statistics

import requests

from src import execution

HF_MODEL = "ProsusAI/finbert"
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
NEWS_LOOKBACK_ARTICLES = 5
REQUEST_TIMEOUT_SECONDS = 15


def _score_text(text: str, token: str) -> dict[str, float] | None:
    try:
        response = requests.post(
            HF_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"inputs": text},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        scores = payload[0] if payload and isinstance(payload[0], list) else payload
        return {item["label"]: item["score"] for item in scores}
    except Exception:
        return None


def get_sentiment(ticker: str) -> str | None:
    """Returns a short summary string like "positive (0.87, 4 headlines)",
    or None if unavailable for any reason -- fails closed, never raises."""
    token = os.environ.get("HUGGINGFACE_API_TOKEN")
    if not token:
        return None

    try:
        news = execution.get_news(ticker, limit=NEWS_LOOKBACK_ARTICLES)
    except execution.AlpacaCliError:
        return None

    headlines = [a["headline"] for a in news.get("news", []) if a.get("headline")]
    if not headlines:
        return None

    scored = [s for s in (_score_text(h, token) for h in headlines) if s is not None]
    if not scored:
        return None

    avg_scores = {
        label: statistics.mean(s.get(label, 0.0) for s in scored) for label in ("positive", "negative", "neutral")
    }
    top_label = max(avg_scores, key=avg_scores.get)
    return f"{top_label} ({avg_scores[top_label]:.2f}, {len(scored)} headlines)"
