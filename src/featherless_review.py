"""Featherless-hosted LLM as a pre-trade veto gate -- it never originates trades.

Only called on candidates that already passed every deterministic filter
(asset universe, market-cap floor, blackout calendar, >=2-of-4 technical
criteria, position sizing). The model may veto; it cannot approve a larger
size, a different structure, or a trade that bypasses the stated risk limit,
and it has no tool access to place orders itself.
"""

import json
import os
import re
from dataclasses import dataclass

from openai import OpenAI

FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-32B-Instruct"

SYSTEM_PROMPT = """You are a pre-trade risk reviewer for an automated options trading agent.
A candidate trade has ALREADY passed every deterministic filter: asset universe, \
market-cap floor, blackout calendar, and the strategy's technical criteria. \
Position size and option structure are already fixed by the caller -- you cannot \
change them. Your only power is to VETO if the setup looks incoherent, contradictory, \
or unsafe given the evidence provided. You cannot approve a larger size, a different \
structure, or a trade that bypasses the stated risk limit. Respond with ONLY a JSON \
object: {"veto": bool, "confidence": <0-1 float>, "rationale": "<one or two sentences>"}."""


@dataclass
class TradeCandidate:
    ticker: str
    direction: str  # "bullish" | "bearish"
    criteria_met: list[str]
    signal_details: dict[str, str]
    proposed_structure: str
    max_risk_usd: float
    account_equity_usd: float
    sentiment: str | None = None  # Company C only: "positive/negative/neutral (score, N headlines)" or None if unavailable -- A/B never pass this


@dataclass
class TradeVerdict:
    veto: bool
    confidence: float
    rationale: str


def _client() -> OpenAI:
    # Accept-Encoding: identity works around a response-decompression bug in
    # this environment's httpx stack (a codec call with an unsupported kwarg)
    # that otherwise breaks every call with an APIConnectionError.
    return OpenAI(
        base_url=FEATHERLESS_BASE_URL,
        api_key=os.environ["FEATHERLESS_API_KEY"],
        default_headers={"Accept-Encoding": "identity"},
    )


def _parse_verdict(raw: str) -> TradeVerdict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            # Fail closed: unparseable output is a veto, never a silent pass-through.
            return TradeVerdict(veto=True, confidence=0.0, rationale=f"unparseable model output: {raw!r}")
        data = json.loads(match.group(0))
    return TradeVerdict(
        veto=bool(data.get("veto", True)),
        confidence=float(data.get("confidence", 0.0)),
        rationale=str(data.get("rationale", "")),
    )


def _build_prompt(candidate: TradeCandidate) -> str:
    evidence = "\n".join(f"  - {k}: {v}" for k, v in candidate.signal_details.items())
    risk_pct = candidate.max_risk_usd / candidate.account_equity_usd
    sentiment_line = f"Recent news sentiment: {candidate.sentiment}\n" if candidate.sentiment else ""
    return (
        f"Ticker: {candidate.ticker}\n"
        f"Direction: {candidate.direction}\n"
        f"Criteria met ({len(candidate.criteria_met)}/4): {', '.join(candidate.criteria_met)}\n"
        f"Evidence:\n{evidence}\n"
        f"{sentiment_line}"
        f"Proposed structure: {candidate.proposed_structure}\n"
        f"Max risk: ${candidate.max_risk_usd:,.0f} on ${candidate.account_equity_usd:,.0f} equity ({risk_pct:.2%})"
    )


def review_candidate(candidate: TradeCandidate, model: str | None = None, client: OpenAI | None = None) -> TradeVerdict:
    model = model or os.environ.get("FEATHERLESS_MODEL") or DEFAULT_MODEL
    client = client or _client()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(candidate)},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content
    except Exception as e:
        # Fail closed: a network/transport/API failure is a veto, never a
        # silent pass-through -- same philosophy as _parse_verdict below.
        return TradeVerdict(veto=True, confidence=0.0, rationale=f"Featherless call failed, failing closed: {e!r}")
    return _parse_verdict(content)
