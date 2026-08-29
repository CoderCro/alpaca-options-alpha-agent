# Milestone Plan — Alpaca AI Trading Agents Hackathon

Deadline: **Sep 4, 2026, 5:00 PM CEST**. NFP (jobs report) lands Sep 4 -- our own T1-news blackout rule forbids new trades Sep 3-4, so real P&L needs to exist by end of Sep 2.

## Aug 28 (Kickoff)
- [x] Confirm strategy (watchlist rules, 2-of-4 entry criteria, blackout rules, exit ladder, Featherless's bounded role)
- [x] Create dedicated fresh Alpaca paper account, confirm $100,000 balance
- [x] Get Alpaca API keys into local `.env`, verify connectivity (account ID captured)
- [x] Get Featherless API key active
- [x] Scaffold local repo (README, requirements, .gitignore, git init)
- [x] **Decided MCP vs CLI**: use both, each where it fits — MCP server for the human-in-the-loop watchlist workflow, CLI for the unattended autonomous execution loop
- [ ] Push repo to a GitHub remote (currently local-only, zero commits)

## Aug 29 (today)
- [x] Alpaca CLI installed (`alpacahq/cli` v0.0.14 via Homebrew), authenticated via `.env` API keys (no interactive OAuth needed — works headless), verified against the live paper account: account/balance, positions, and options chain+Greeks all confirmed working
- [x] Alpaca MCP server registered for this project (`alpacahq/alpaca-mcp-server`, local scope) — needs a fresh Claude Code session to actually connect and become usable
- [x] Proved a real round-trip order through the CLI: submitted an AAPL options limit order, confirmed it open, canceled it, verified final status `canceled` — full cycle on an actual options contract, not just a stock
- [x] Built `execution.py` (Python wrapper around the CLI) and `indicators.py` + `rules_engine.py` (the 2-of-4 entry-criteria engine) in parallel — one via a background subagent, one directly. All verified: 44/44 tests pass, plus a live (non-mocked) smoke test of the CLI wrapper against the real account
- [x] Confirmed Featherless with a live call (not mocked): API key valid, `Qwen/Qwen2.5-32B-Instruct` accessible, real trade-review verdict returned through the actual `featherless_review.py` code path. Fixed a response-decompression bug in this environment's HTTP stack along the way (disabled compression on the client). Credit balance itself isn't exposed via API -- check the Featherless dashboard directly.
- [ ] Build the indicators module: S&R touch detection, multi-timeframe trend (HH/HL), MA-as-S&R, monthly-vs-weekly-MA10 — needs tolerance/lookback parameters decided first

## Aug 30-31
- [ ] Wire rules_engine -> watchlist/trading-list state (the human-approval workflow)
- [ ] Wire featherless_review.py and position_manager.py into a single decision loop, calling execution.py
- [ ] Build the options selector (strike/expiry/structure choice for a given signal + direction)

## Sep 1
- [ ] Automate the loop on a schedule during market hours
- [ ] Minimal dashboard/log: open positions, P&L curve, reasoning trail per trade

## Sep 2
- [ ] Let the agent trade live paper sessions to build a real P&L track record — last full day before the NFP blackout shuts the window
- [ ] Start "build in public" X/LinkedIn posts (tag @lablabai + @AlpacaHQ)

## Sep 3 (blackout: no new trades)
- [ ] Record video demo, build slide deck, write the one-page write-up (AI logic, risk gates, Alpaca infra)
- [ ] Clean README, confirm account ID is documented

## Sep 4, before 5:00 PM CEST (blackout: no new trades)
- [ ] Final submission: title, descriptions, tags, cover image, video, slides, repo link, demo URL, Alpaca account ID, up to 5 social links
