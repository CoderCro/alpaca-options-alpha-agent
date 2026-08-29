# Milestone Plan — Alpaca AI Trading Agents Hackathon

Deadline: **Sep 4, 2026, 5:00 PM CEST**. NFP (jobs report) lands Sep 4 -- our own T1-news blackout rule forbids new trades Sep 3-4, so real P&L needs to exist by end of Sep 2.

## Aug 28 (Kickoff)
- [x] Confirm strategy (watchlist rules, 2-of-4 entry criteria, blackout rules, exit ladder, Featherless's bounded role)
- [x] Create dedicated fresh Alpaca paper account, confirm $100,000 balance
- [x] Get Alpaca API keys into local `.env`, verify connectivity (account ID captured)
- [x] Get Featherless API key active
- [x] Scaffold local repo (README, requirements, .gitignore, git init)
- [x] **Decided MCP vs CLI**: use both, each where it fits — MCP server for the human-in-the-loop watchlist workflow, CLI for the unattended autonomous execution loop
- [x] Pushed to a public GitHub remote: https://github.com/CoderCro/alpaca-options-alpha-agent

## Aug 29 (today)
- [x] Alpaca CLI installed (`alpacahq/cli` v0.0.14 via Homebrew), authenticated via `.env` API keys (no interactive OAuth needed — works headless), verified against the live paper account: account/balance, positions, and options chain+Greeks all confirmed working
- [x] Alpaca MCP server registered for this project (`alpacahq/alpaca-mcp-server`, local scope) — needs a fresh Claude Code session to actually connect and become usable
- [x] Proved a real round-trip order through the CLI: submitted an AAPL options limit order, confirmed it open, canceled it, verified final status `canceled` — full cycle on an actual options contract, not just a stock
- [x] Built `execution.py` (Python wrapper around the CLI) and `indicators.py` + `rules_engine.py` (the 2-of-4 entry-criteria engine) in parallel — one via a background subagent, one directly. All verified: 44/44 tests pass, plus a live (non-mocked) smoke test of the CLI wrapper against the real account
- [x] Confirmed Featherless with a live call (not mocked): API key valid, `Qwen/Qwen2.5-32B-Instruct` accessible, real trade-review verdict returned through the actual `featherless_review.py` code path. Fixed a response-decompression bug in this environment's HTTP stack along the way (disabled compression on the client). Credit balance itself isn't exposed via API -- check the Featherless dashboard directly.

## Aug 30-31
- [x] **Decided to run two comparable companies** for the hackathon demo instead of committing to one architecture: **Company A** (deterministic 2-of-4 gate, Featherless veto-only, no LLM execution authority -- the original design) and **Company B** (fully autonomous LangChain agent backed by Featherless, direct order-placement authority), each on its own dedicated $100k paper account, trading the identical watchlist so the only variable is decision-making approach
- [x] Built the shared safety floor both companies route through: `guardrails.py` (kill switch checked first always, blackout, watchlist membership, defined-risk-structure check, per-trade/aggregate risk caps, daily order-count and loss-circuit-breaker caps -- all hard, code-level checks, never just a prompt instruction), `audit_log.py` (JSONL reasoning trail), `watchlist.py` (human-approved + agent-recommended-pending lists), `position_store.py` (exit-ladder stage tracking, since Alpaca itself has no notion of it)
- [x] Built `options_selector.py` -- deterministic strike/expiry pre-filter (DTE window + strike-to-spot moneyness). Live-verified the option chain feed's `greeks.delta` often comes back `0` for real contracts, so delta is kept as informational context only, not a hard filter
- [x] Built Company B: `agent_tools.py` (10 LangChain tools -- read-only market/signal/position lookups plus three guardrail-gated write tools) and `trading_agent.py` (`ChatOpenAI` -> Featherless, hand-rolled tool-calling loop, 6-turn cap, fails closed to "no trade"). Verified live that the `ChatOpenAI`-via-Featherless header workaround actually avoids the decompression bug in practice
- [x] Built Company A: `company_a_agent.py` -- a mechanical loop that reuses Company B's exact same tool functions (via their `.func` attribute) with rules-derived arguments instead of LLM judgment, so both companies share the identical guardrail/veto/execution/audit path -- only the decision-maker differs
- [x] Small additive change to `rules_engine.py`: `CriteriaResult` now exposes a `direction` (bullish/bearish/None), needed for Company A's mechanical entries -- reuses the trend-alignment computation that already existed internally, no existing test broken
- [x] Set up the second paper account and its own `.env.company_b` credentials; confirmed live that Company A and Company B resolve to genuinely distinct account IDs
- [x] Seeded `watchlist.json` with the six explicitly-named tickers from the strategy (SPY, SPX, QQQ, BTC, ETH, LINK) -- the >=$10B-market-cap stock bucket is still unpopulated, a human call, not something to invent
- [x] 128/128 tests passing throughout (114 base + 14 for the company split)

## Sep 1
- [ ] Automate both companies' loops on a schedule during market hours
- [ ] Minimal dashboard/log: open positions, P&L curve, reasoning trail per trade -- per company, now that `logs/a/` and `logs/b/` are independently namespaced

## Sep 2
- [ ] Let both companies trade live paper sessions to build real, comparable P&L track records — last full day before the NFP blackout shuts the window
- [ ] Start "build in public" X/LinkedIn posts (tag @lablabai + @AlpacaHQ)

## Sep 3 (blackout: no new trades)
- [ ] Record video demo, build slide deck, write the one-page write-up (AI logic, risk gates, Alpaca infra, and the Company A vs. Company B comparison)
- [ ] Clean README, confirm both account IDs are documented

## Sep 4, before 5:00 PM CEST (blackout: no new trades)
- [ ] Final submission: title, descriptions, tags, cover image, video, slides, repo link, demo URL, both Alpaca account IDs, up to 5 social links
