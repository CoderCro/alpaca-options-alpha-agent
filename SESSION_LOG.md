# Session Log — Alpaca AI Trading Agents Hackathon

## Hackathon identified
- lablab.ai x Alpaca, "Alpaca AI Trading Agents Hackathon"
- Aug 28 - Sep 4 2026, $6,300 prize pool
- Core requirements: autonomous agent, Alpaca Trading API, **MCP server or CLI tools**, options trading required
- Featherless AI is a technology partner ($25 credit/participant)

## Setup
- Installed the community "Karpathy" CLAUDE.md (behavioral guidelines) into the project root
- Researched hackathon rules, dates, submission requirements, judging criteria

## Strategy defined (user-specified)
- **Watchlist**: human-approved; agent may recommend additions. US stocks with market cap >= $10B; SPY, SPX, QQQ (QQQ substitutes for NDX -- Alpaca doesn't support NDX index options, confirmed against their docs); crypto limited to BTC, ETH, LINK
- **Trading list**: >=2 of 4 criteria -- (1) support/resistance via weekly touches with daily chart prepping a 3rd touch, (2) multi-timeframe trend alignment (HH/HL on daily/4h/15m), (3) daily MA20/50/100 acting as strong S&R, (4) monthly candle closing above the weekly-timeframe MA10
- **Blackout rules**: no trades +/-2h around the 9:30 ET open; no trades day-before/of/after Tier-1 macro news (FOMC, NFP)
- **Verified fact**: none of 2026's FOMC meetings fall in the hackathon week, but NFP (August jobs report) lands exactly on **Sep 4 — the submission deadline day**. Our own blackout rule therefore forbids new trades on Sep 3-4, so real P&L needs to be built by end-of-day Sep 2.
- **Featherless's role**: bounded to (a) a pre-trade veto gate that can only block, never originate, a trade, and (b) plain-language reasoning narration for the dashboard/write-up. It never picks structure/size, never bypasses filters, has no order-placement access, and fails closed (treats unparseable output as a veto).
- **Exit ladder** (user-specified in detail): sell 20% of the position at +20% profit; sell another 20% at +45% profit (this arms a breakeven stop on the remainder); from there it's a race — if price falls back to breakeven first, sell 50% of what's left with the final piece exiting on -20% loss or +100% profit; if +95% profit is hit before any pullback, sell 50% of what's left with the final piece's exit timing left to Featherless's discretion (sentiment + S&R). A hard 7-day-to-expiry close overrides every stage regardless of where the ladder is.

## Built & verified (all tests passing at time of writing)
- `src/calendar_blackout.py` + 5 tests — market-open and T1-news blackout windows
- `src/featherless_review.py` + 3 tests — the veto-gate LLM call; fails closed on unparseable JSON
- `src/position_manager.py` + 10 tests — the exit ladder state machine; caught and fixed a real floating-point boundary bug (`2.40/2.00` computing as `19.999999999999996` instead of `20.0`, silently missing exact-threshold comparisons)
- `src/check_alpaca_connection.py` — verified the live paper account: ID `cbef1091-8d97-4c11-8b6b-c05dea60b492`, $100,000 equity, options trading level 3 (max) already approved
- Repo scaffolded: README, requirements.txt, .env/.env.example, .gitignore, local git init (no commits made yet)
- **Resolved the MCP/CLI compliance gap**: decided to use both, each for what it's naturally suited to — Alpaca's MCP server (`alpacahq/alpaca-mcp-server`, registered locally for this project) for the human-in-the-loop watchlist workflow, and the official Alpaca CLI (`alpacahq/cli` v0.0.14, installed via Homebrew) for the unattended autonomous execution loop. CLI authenticates headlessly via the same `.env` API keys (no OAuth browser step needed) and is verified working against the live paper account: account/balance, empty positions list, and full options chain + Greeks all confirmed. MCP server is registered but needs a fresh Claude Code session to actually connect.

- Proved a real place+cancel round trip through the CLI on an actual options contract (AAPL, Oct 2026 expiry): dry-run sanity check, submit (accepted), confirmed open, canceled, verified final status `canceled`. Noted for later: `order cancel` needs `--order-id` as an explicit flag, not positional.
- Built `src/execution.py` (subprocess wrapper around the CLI: get_account, list_positions, get_option_chain, submit_order, cancel_order, get_order, list_open_orders, all funneled through one `_run_cli` seam, raising `AlpacaCliError` on non-zero exit) via a background subagent -- verified by reading the actual code (not just the agent's summary), 12/12 tests (all mocked, no live calls), plus a live non-mocked smoke test confirming it works against the real account.
- Built `src/indicators.py` (swing-high/low fractal detection, SMA, touch-tolerance helpers, 6 tests) and `src/rules_engine.py` (the 2-of-4 criteria combiner: support/resistance, multi-timeframe trend, MA-as-S&R, monthly-vs-weekly-MA10, 9 tests) directly. Concrete numeric definitions chosen (flagged to the user, not yet contested): 0.5% touch tolerance, 26-week S&R lookback, 5-bar fractal swings, MA-as-S&R needs only one of MA20/50/100 to qualify.
- Caught and fixed a real bug along the way: comparisons on pandas/numpy scalars return `numpy.bool_`, and `numpy.bool_(True) is True` evaluates to `False` (identity, not equality) -- `check_monthly_ma10` and `is_near` now explicitly cast to Python `bool`.
- All 44 tests pass across the full suite.

- Confirmed Featherless with a live (non-mocked) call through the actual `review_candidate()` function: API key valid, `Qwen/Qwen2.5-32B-Instruct` accessible, real verdict returned. Found and fixed a genuine bug: this environment's HTTP stack (a non-standard `httpx2`/`httpcore2` pairing under the `openai` package) has a response-decompression bug that broke every SDK call with `APIConnectionError`. Fixed by disabling response compression on the client (`default_headers={"Accept-Encoding": "identity"}`). No API endpoint exists for checking Featherless credit balance -- dashboard-only.

## Open / pending
- Wire rules_engine's output into a watchlist/trading-list state machine (the human-approval workflow)
- Wire featherless_review.py + position_manager.py into one decision loop that calls execution.py
- Build the options selector (strike/expiry/structure choice for a given signal + direction)
- Options selector (strike/expiry/structure choice) — not built
- Watchlist human-approval workflow — not built
- Dashboard / reasoning-trail logging — not built
- GitHub repo not yet pushed to a remote; no commits made locally
- Video, slide deck, one-page write-up — not started
- Build-in-public social posts — not started
