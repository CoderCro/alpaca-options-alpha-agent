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

## GitHub repository
- Public repo: https://github.com/CoderCro/alpaca-options-alpha-agent
- Getting `gh` authenticated took several detours worth remembering: (1) the sandbox blocks Keychain access, so every `gh`/`git push` needing the stored credential must run with the sandbox disabled; (2) an unrelated pre-existing bug on this machine -- `~/.config` was owned by `root` (likely from an earlier `sudo`-run install), so `gh auth login` silently failed to persist any credential until fixed with `sudo chown -R martinvidovic:staff ~/.config`; (3) a background `gh auth login --web` device-code flow was accidentally killed early on the first attempt (a 5-second sleep-then-kill), which looked like a hang but was actually me cutting it off before the user could approve it in the browser -- the retry without a premature kill worked.
- `gh repo create --public ... --push` was blocked by the auto-mode safety classifier (creating public content) -- surfaced to the user for explicit confirmation before proceeding, per this project's own action-category rules.

## Parallel session: Company A vs Company B
- While this session built the pieces above, the user ran a second Claude Code session **in the same working directory** (not a separate clone) to explore a second approach for the same hackathon submission. This wasn't visible until enough changed files triggered on-disk-change notices; the full scope only became clear by running `git status` directly, since new *untracked* files never trigger those notices (only previously-read files that change do).
- The two sessions turned out to be building one coherent design, not competing alternatives: **Company A** (this session's original deterministic 2-of-4 gate, Featherless veto-only) and **Company B** (a LangChain tool-calling agent, backed by Featherless, with real order-placement authority), run side by side on two separate dedicated paper accounts, trading the identical watchlist -- the comparison itself is the hackathon demo's narrative.
- Company B's execution authority is bounded by a new shared safety floor, `guardrails.py`: a hard, code-level chokepoint (`pre_trade_check`) that every write tool calls unconditionally before `execution.submit_order` -- kill switch checked first always, then (for new entries only) blackout calendar, watchlist membership, long-only structure, a 3%-of-equity per-trade risk cap, 8 concurrent positions max, 25%-of-equity aggregate risk cap, 15/day entry cap, and a daily-loss circuit breaker. Exits/trims only ever need to clear the kill switch and a held-qty check, so risk-cutting can never be blocked by the caps that guard risk-adding. On top of that, every proposed trade from Company B's LangChain agent still passes through an independent `featherless_review.review_candidate` veto call before execution -- a second, separately-prompted model call with zero tool access of its own.
- Verified this design directly by reading the actual code (`guardrails.py`, `agent_tools.py`, `trading_agent.py`), not just trusting the README's description: confirmed `pre_trade_check` really does gate every write tool first, the independent veto call really does happen before `execution.submit_order`, no tool grants raw shell/code access (only 10 purpose-built functions), and the agent loop fails closed (a 6-tool-turn cap that returns "no action taken" rather than forcing a trade if exceeded).
- The other session also independently hit and fixed the same `httpx2` response-decompression bug this session found (via the same `Accept-Encoding: identity` header workaround, applied to `ChatOpenAI` this time) -- consistent diagnosis across both.
- 128/128 tests passing (114 base + 14 for the company split) at time of the final merge/push below.

## Final verification & push (this session, after the parallel session finished)
- Confirmed `.env`, `.env.company_b`, and `state/` all stay correctly gitignored (`.env.company_*` excluded, `!.env.company_*.example` explicitly re-included as a template) -- no secrets in what got committed.
- Re-ran the full suite fresh: 128/128 passed.
- Committed and pushed everything to the public repo.

## Open / pending
- Automate both companies' decision loops on a schedule during market hours
- Minimal dashboard/log: open positions, P&L curve, reasoning trail per trade -- per company, now that logs are independently namespaced
- Let both companies trade live paper sessions to build real, comparable P&L track records before the Sep 3-4 NFP blackout shuts the window
- Start "build in public" X/LinkedIn posts (tag @lablabai + @AlpacaHQ)
- Video, slide deck, one-page write-up (including the Company A vs. Company B comparison) — not started
- Vertical spreads deferred (single-leg only for now -- see README's Known Limitations)
- Scheduling not yet wired up -- both companies currently run one decision cycle per manual invocation
