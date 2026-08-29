# Options Alpha Agent

Autonomous options trading agents built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (lablab.ai x Alpaca, 28 Aug - 4 Sep 2026).

Run as **two comparable, independently-tracked companies** on two separate paper accounts, trading the same watchlist under the same risk guardrails -- the only difference is who decides what to trade:

- **Company A** -- deterministic. A mechanical 2-of-4 technical-criteria gate (`rules_engine.py`) decides entries; Featherless AI can only veto a candidate, never originate or resize one; no LLM ever gets execution authority.
- **Company B** -- fully autonomous. A LangChain agent backed by Featherless AI reasons over the same signals and market data, decides what (if anything) to trade, and has direct tool access to place orders -- bounded by the same hard guardrails as Company A, plus a second, independent Featherless veto pass before any order reaches the market.

## Strategy (shared by both companies)

**Watchlist** (human-approved; agent may recommend additions -- see `watchlist.py`):
- US-listed stocks, market cap >= $10B
- SPY, SPX, QQQ (proxy for NDX/NDQ -- Alpaca does not support NDX index options, only SPX/SPXW/VIX/VIXW/DJX/XSP)
- Crypto: BTC, ETH, LINK only

**Trading list**: watchlist tickers meeting >= 2 of these 4 criteria (`rules_engine.py`):
1. Support & resistance -- weekly candle highs/lows confirmed by 1-2 touches; daily chart takes over the weekly level ahead of a third touch
2. Trend alignment -- higher highs / higher lows on daily, 4h, and 15m charts
3. Daily MA20/MA50/MA100 acting as strong support/resistance
4. Monthly candle closes bullish above the weekly-timeframe MA10

Direction (bullish/bearish) is derived from the same criteria (`CriteriaResult.direction`) -- Company A uses it mechanically; Company B can also reason about it itself.

**Blackout rules** (`src/calendar_blackout.py`, hard gate for both companies):
- No trades 2h before/after the 9:30 ET market open
- No trades the day before/of/after a Tier-1 macro event (FOMC decision, NFP jobs report)

**Risk guardrails** (`src/guardrails.py` -- the actual security boundary for both companies, enforced in code, not just a prompt instruction): a kill switch (checked first, always), the blackout calendar, watchlist membership, defined-risk structure only (single-leg long calls/puts -- vertical spreads deferred, see Known Limitations), a 3%-of-equity per-trade risk cap, a max of 8 concurrent positions, a 25%-of-equity aggregate risk cap, a 15/day new-entry cap, and a daily-loss circuit breaker. Exits and trims always bypass every cap except the kill switch, so neither company can be blocked from cutting a loss by its own risk limits.

**Featherless AI's role**:
- **Company A**: veto-only, as originally designed (`featherless_review.py`) -- it cannot approve a larger size, a different structure, or a trade that bypasses the stated risk limit, and it has no tool access to place orders itself.
- **Company B**: the reasoning model behind a LangChain tool-calling agent (`trading_agent.py`) that decides which candidates to propose -- but every proposal still passes through the exact same guardrails *and* the same independent veto call (`featherless_review.review_candidate`) before `execution.submit_order` is ever reached. Two separately-prompted model calls, not one -- the veto pass has zero tool access of its own even if the reasoning pass were somehow compromised.

**Execution**: Alpaca Trading API via CLI (`execution.py`) for both companies' unattended decision loops -- satisfies the hackathon's requirement to use Alpaca's own MCP server or CLI tooling. (Alpaca's MCP server is separately registered for the human-in-the-loop watchlist-curation workflow inside a Claude Code session.) Options only, defined-risk, single-leg (long calls/puts) for now, on two fresh paper accounts seeded at $100,000 each, each dedicated to this hackathon.

## Known limitations / deferred scope

- **Vertical spreads**: promised structures include verticals, but `execution.py`'s order submission is single-leg only -- sequencing two CLI calls for a spread risks one leg filling and the other failing (an accidental undefined-risk position). Deferred rather than silently dropped.
- **Scheduling**: both companies currently run one decision cycle per manual invocation (`python3 -m src.run_company_a` / `run_company_b`), not yet wired to a scheduler.
- **Dashboard**: no UI yet -- `logs/<company>/audit_*.jsonl` is the reasoning-trail data source for one, planned but not built.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env                        # Company A's Alpaca keys + shared Featherless keys
cp .env.company_b.example .env.company_b     # Company B's own Alpaca keys
python -m pytest
```

## Running a decision cycle

```bash
python3 -m src.run_company_a   # deterministic, human-curated
python3 -m src.run_company_b   # fully autonomous LangChain agent
```

Each company tracks its own positions and audit log under `state/<company>/` and `logs/<company>/` (gitignored). A `KILL_SWITCH` file dropped in a company's `state/<company>/` directory halts that company only -- the other keeps running.
