# Options Alpha Agent

Autonomous options trading agents built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (lablab.ai x Alpaca, 28 Aug - 4 Sep 2026).

Run as **three comparable, independently-tracked companies** on three separate paper accounts, under the same risk guardrails -- the difference is who decides what to trade, and (for Company C) what signal drives that decision:

- **Company A** -- deterministic. A mechanical 2-of-4 technical-criteria gate (`rules_engine.py`) decides entries; Featherless AI can only veto a candidate, never originate or resize one; no LLM ever gets execution authority.
- **Company B** -- fully autonomous. A LangChain agent backed by Featherless AI reasons over the same signals and market data, decides what (if anything) to trade, and has direct tool access to place orders -- bounded by the same hard guardrails as Company A, plus a second, independent Featherless veto pass before any order reaches the market.
- **Company C** -- deterministic, volatility-edge. Instead of the shared technical rules, buys puts only when the underlying's own computed implied volatility is cheap relative to trailing realized volatility, delta-hedged with the underlying so P&L comes from the vol edge, not direction. Featherless is veto-only, same as Company A. See "Company C: volatility-edge strategy" below.

## Strategy (shared by all three companies)

**Watchlist** (human-approved; agent may recommend additions -- see `watchlist.py`):
- US-listed stocks, market cap >= $10B
- SPY, SPX, QQQ (proxy for NDX/NDQ -- Alpaca does not support NDX index options, only SPX/SPXW/VIX/VIXW/DJX/XSP)
- Crypto: BTC, ETH, LINK only

**Trading list**: watchlist tickers meeting >= 2 of these 4 criteria (`rules_engine.py`):
1. Support & resistance -- weekly candle highs/lows confirmed by 1-2 touches; daily chart takes over the weekly level ahead of a third touch
2. Trend alignment -- higher highs / higher lows on daily, 4h, and 15m charts
3. Daily MA20/MA50/MA100 acting as strong support/resistance
4. Monthly candle closes bullish above the weekly-timeframe MA10

Direction (bullish/bearish) is derived from the same criteria (`CriteriaResult.direction`) -- Company A uses it mechanically; Company B can also reason about it itself. Company C doesn't use this 2-of-4 signal at all (see below), but scans the same approved watchlist -- a statistical mispricing check doesn't need the same "is this a quality name" curation a technical/trend strategy does.

**Blackout rules** (`src/calendar_blackout.py`, hard gate for both companies):
- No trades 2h before/after the 9:30 ET market open
- No trades the day before/of/after a Tier-1 macro event (FOMC decision, NFP jobs report)

**Risk guardrails** (`src/guardrails.py` -- the actual security boundary for both companies, enforced in code, not just a prompt instruction): a kill switch (checked first, always), the blackout calendar, watchlist membership, defined-risk structure only (single-leg long calls/puts -- vertical spreads deferred, see Known Limitations), a 3%-of-equity per-trade risk cap, a max of 8 concurrent positions, a 25%-of-equity aggregate risk cap, a 15/day new-entry cap, and a daily-loss circuit breaker. Exits and trims always bypass every cap except the kill switch, so neither company can be blocked from cutting a loss by its own risk limits.

**Featherless AI's role**:
- **Company A**: veto-only, as originally designed (`featherless_review.py`) -- it cannot approve a larger size, a different structure, or a trade that bypasses the stated risk limit, and it has no tool access to place orders itself.
- **Company B**: the reasoning model behind a LangChain tool-calling agent (`trading_agent.py`) that decides which candidates to propose -- but every proposal still passes through the exact same guardrails *and* the same independent veto call (`featherless_review.review_candidate`) before `execution.submit_order` is ever reached. Two separately-prompted model calls, not one -- the veto pass has zero tool access of its own even if the reasoning pass were somehow compromised.
- **Company C**: veto-only, same contract as Company A -- the entry decision here is a computed number (the vol edge), not something an LLM originates.

**Execution**: Alpaca Trading API via CLI (`execution.py`) for all three companies' unattended decision loops -- satisfies the hackathon's requirement to use Alpaca's own MCP server or CLI tooling. (Alpaca's MCP server is separately registered for the human-in-the-loop watchlist-curation workflow inside a Claude Code session.) Options only, defined-risk, single-leg (long calls/puts) for now, on three fresh paper accounts seeded at $100,000 each, each dedicated to this hackathon.

## Company C: volatility-edge strategy

Inspired by market-maker vol trading (compare your own theoretical price against the market's, trade the mispricing, hedge out everything else) -- but adapted to what a retail-tier Alpaca paper account can actually do, not a literal reproduction of market-maker infrastructure (no quoting both sides, no colocated feeds).

- **Signal** (`vol_edge.py`): realized volatility (annualized stdev of the underlying's own daily log returns, from `execution.get_bars`) compared against implied volatility. Acts only when implied is at least 3 vol points *cheap* relative to realized -- the inverse, implied being *rich* (the more common case, the usual variance risk premium), can't be acted on, since harvesting it needs short/naked or spread structures this system deliberately doesn't allow (`guardrails.ALLOWED_SIDES_FOR_OPEN = {"buy"}`).
- **Implied vol is never read off Alpaca's chain.** Live-verified (2026-08-30, real SPY chain) that every contract's `greeks` block comes back all-zero and there's no separate IV field in the snapshot at all -- worse than the occasionally-unreliable delta `options_selector.py` already flagged for Company A/B. So both delta and implied vol are computed here (`options_math.py`: Black-Scholes price/delta/vega, a Newton-Raphson-with-bisection-fallback IV solver) from the chain's quoted price instead.
- **Puts only, never calls** (`delta_hedge.py`): a delta-hedged put needs a stock *buy* to hedge (put delta is negative); a delta-hedged call would need a stock *short*, which guardrails has no path for. By put-call parity a delta-hedged put carries essentially the same vol exposure as a delta-hedged call, so nothing is given up.
- **Exit** (`check_vol_edge_exit_actions`): close when the vol edge reverts (implied is no longer cheap vs. current realized) or a hard 7-day-to-expiry cutoff hits, whichever first -- mirrors the hard DTE override in the exit ladder Company A/B use.
- **Position tracking**: `hedge_store.py` links a put position to its hedge-share count (Alpaca's own position list has no notion these are one economic trade) -- a smaller, separate shape from `position_store.py`'s scaled exit-ladder state, since Company C is a single entry/single exit, not a tranche-by-tranche scale-out.

## Known limitations / deferred scope

- **Vertical spreads**: promised structures include verticals, but `execution.py`'s order submission is single-leg only -- sequencing two CLI calls for a spread risks one leg filling and the other failing (an accidental undefined-risk position). Deferred rather than silently dropped.
- **Company C's two-leg entry/exit has the same atomicity gap**: the put and its stock hedge are two separate CLI calls, not one atomic transaction. If the put fills and the hedge then fails, the position is temporarily unhedged -- logged loudly as a `hedge_leg_failed` audit event rather than hidden, but not solved.
- **Watchlist crypto entries are stored without a `/USD` suffix** (`BTC`, not `BTC/USD`), so `_fetch_ohlc`'s crypto-routing (`"/" in symbol`) misses them and treats them as stock tickers instead. Live-verified this doesn't cause bad trades: `BTC` on the stock feed silently resolves to an unrelated ~$34 equity, but since that stock has no options chain, Company C (which needs one) still correctly lands on "no signal" -- wrong reason, safe outcome. Affects the realized-vol/technical-signal quality Company A/B would compute for these tickers too, not just Company C's scope. Flagged, not fixed -- touches shared code, and doesn't change Company C's actual behavior since crypto has no options chain either way.
- **Scheduling**: `scheduler.py` runs all three on a 15-minute interval during market hours, single-day-scoped (exits at close, no restart-on-crash/reboot-survival) -- a fresh `python3 -m src.scheduler` each trading morning, not a persistent multi-day daemon. Deliberate scope given how few trading days remain.
- **Dashboard**: no UI yet -- `logs/<company>/audit_*.jsonl` is the reasoning-trail data source for one, planned but not built.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env                        # Company A's Alpaca keys + shared Featherless keys
cp .env.company_b.example .env.company_b     # Company B's own Alpaca keys
cp .env.company_c.example .env.company_c     # Company C's own Alpaca keys
python -m pytest
```

## Running a decision cycle

```bash
python3 -m src.run_company_a   # deterministic, human-curated
python3 -m src.run_company_b   # fully autonomous LangChain agent
python3 -m src.run_company_c   # deterministic, volatility-edge
```

Or, to cover a full trading day without manually re-running each command: `python3 -m src.scheduler` -- started once, checks every 15 minutes during NYSE market hours and runs all three companies' cycles each time, exiting on its own at market close. Shells out to the three commands above rather than importing them, so each company's credentials stay correctly isolated to its own account.

Each company tracks its own positions and audit log under `state/<company>/` and `logs/<company>/` (gitignored). A `KILL_SWITCH` file dropped in a company's `state/<company>/` directory halts that company only -- the other keeps running.
