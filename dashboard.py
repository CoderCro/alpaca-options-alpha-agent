"""Streamlit dashboard: live account state, P&L curve, open positions, and
reasoning trail for all three companies side by side.

Doubles as the hackathon's required "Demo Application Platform" (must be
Streamlit, Replit, or Vercel) -- one build satisfies both needs.

Each company has its own Alpaca account/credentials, loaded fresh
immediately before every call (see _load_company_env) rather than trusting
process-wide os.environ state -- the same credential-isolation concern that
made scheduler.py shell out to subprocesses instead of importing in-process.
Streamlit runs this as a single process, so that discipline matters here too.

Run with: streamlit run dashboard.py
"""

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src import execution

REPO_ROOT = Path(__file__).resolve().parent

COMPANIES = {
    "a": {"label": "Company A — Rules Engine", "env_file": None},
    "b": {"label": "Company B — LangChain Agent", "env_file": ".env.company_b"},
    "c": {"label": "Company C — Volatility Edge", "env_file": ".env.company_c"},
}

st.set_page_config(page_title="Options Alpha Agent", layout="wide")


def _load_company_env(company: str) -> None:
    # Deployed (Streamlit Cloud): no .env files exist there -- they're
    # gitignored and never pushed. Secrets are configured per-company under
    # distinct names (ALPACA_A_API_KEY, ALPACA_B_API_KEY, ...) since a single
    # shared ALPACA_API_KEY name couldn't distinguish which company's call is
    # in flight -- os.environ is process-wide, and Streamlit runs as one
    # long-lived process, unlike the per-company subprocess isolation the
    # CLI scripts get for free. Falls through to local .env files when no
    # matching secret exists (e.g. running locally with no secrets.toml).
    prefix = company.upper()
    try:
        cloud_key = st.secrets.get(f"ALPACA_{prefix}_API_KEY")
    except Exception:
        cloud_key = None
    if cloud_key:
        os.environ["ALPACA_API_KEY"] = cloud_key
        os.environ["ALPACA_SECRET_KEY"] = st.secrets[f"ALPACA_{prefix}_SECRET_KEY"]
        os.environ["FEATHERLESS_API_KEY"] = st.secrets.get("FEATHERLESS_API_KEY", "")
        return

    load_dotenv(REPO_ROOT / ".env", override=True)
    env_file = COMPANIES[company]["env_file"]
    if env_file:
        load_dotenv(REPO_ROOT / env_file, override=True)


@st.cache_data(ttl=60, show_spinner=False)
def fetch_account(company: str) -> dict:
    _load_company_env(company)
    try:
        return execution.get_account()
    except execution.AlpacaCliError as e:
        return {"error": str(e)}


@st.cache_data(ttl=60, show_spinner=False)
def fetch_positions(company: str) -> list[dict]:
    _load_company_env(company)
    try:
        return execution.list_positions()
    except execution.AlpacaCliError:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def fetch_portfolio_history(company: str) -> pd.DataFrame:
    _load_company_env(company)
    try:
        # period="1D": Alpaca's portfolio-history endpoint mishandles a period
        # longer than the account's actual age (confirmed live on Company B,
        # ~2 days old -- a 5D query anchors base_value to a date before the
        # account existed and reports near-zero garbage for that whole span,
        # not an error). 1D is safe for every account regardless of creation
        # date, and is the more honest chart anyway: Aug 31 is the first real
        # trading day for all three companies, so there's no multi-day
        # history yet to show.
        history = execution.get_portfolio_history(period="1D", timeframe="15Min")
    except execution.AlpacaCliError:
        return pd.DataFrame()
    timestamps = history.get("timestamp") or []
    equity = history.get("equity") or []
    if not timestamps:
        return pd.DataFrame()
    df = pd.DataFrame({"time": pd.to_datetime(timestamps, unit="s"), "equity": equity})
    return df[df["equity"] > 0]  # drop the pre-account-existence zero backfill


def load_recent_events(company: str, limit: int = 25) -> list[dict]:
    log_dir = REPO_ROOT / "logs" / company
    events = []
    for path in sorted(log_dir.glob("audit_*.jsonl")):
        with path.open() as f:
            for line in f:
                events.append(json.loads(line))
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]


def _summarize_event(e: dict) -> str:
    t = e.get("event_type", "?")
    if t == "gate_result":
        verdict = "allowed" if e.get("allowed") else "blocked"
        return f"[gate:{e.get('gate')}] {verdict} — {e.get('reason')} ({e.get('symbol', '')})"
    if t == "featherless_verdict":
        verb = "VETOED" if e.get("veto") else "approved"
        return f"[featherless] {verb} (confidence {e.get('confidence')}) — {e.get('rationale')}"
    if t == "order_submitted":
        return f"[order] {e.get('side')} {e.get('qty')} {e.get('symbol')} @ {e.get('limit_price')}"
    if t == "order_result":
        return f"[order] {e.get('symbol')} → {e.get('status')}"
    extra = {k: v for k, v in e.items() if k not in ("timestamp", "event_type")}
    return f"[{t}] {json.dumps(extra, default=str)}"


def render_company(company: str) -> None:
    meta = COMPANIES[company]
    st.subheader(meta["label"])

    account = fetch_account(company)
    if "error" in account:
        st.error(f"Couldn't reach account: {account['error']}")
        return

    equity = float(account["equity"])
    last_equity = float(account.get("last_equity") or equity)
    day_pnl = equity - last_equity
    day_pnl_pct = (day_pnl / last_equity * 100) if last_equity else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Equity", f"${equity:,.2f}", f"{day_pnl:+,.2f} today")
    c2.metric("Buying power", f"${float(account['buying_power']):,.2f}")
    c3.metric("Today's P&L %", f"{day_pnl_pct:+.2f}%")

    history_df = fetch_portfolio_history(company)
    if not history_df.empty:
        st.line_chart(history_df.set_index("time")["equity"])
    else:
        st.caption("No portfolio history yet.")

    st.markdown("**Open positions**")
    positions = fetch_positions(company)
    if positions:
        pos_df = pd.DataFrame(positions)
        cols = [c for c in ["symbol", "qty", "side", "avg_entry_price", "current_price", "unrealized_pl"] if c in pos_df.columns]
        st.dataframe(pos_df[cols], hide_index=True, use_container_width=True)
    else:
        st.caption("No open positions.")

    st.markdown("**Reasoning trail (most recent)**")
    events = load_recent_events(company)
    if events:
        for e in events:
            ts = e["timestamp"].replace("T", " ").split(".")[0]
            st.text(f"{ts}  {_summarize_event(e)}")
    else:
        st.caption("No logged events yet.")


def main() -> None:
    st.title("Options Alpha Agent")
    st.caption(
        "Alpaca AI Trading Agents Hackathon — Company A (rules engine) vs "
        "Company B (LangChain agent) vs Company C (volatility edge), same "
        "watchlist, same guardrails."
    )
    if st.button("Refresh"):
        st.cache_data.clear()

    cols = st.columns(3)
    for col, company in zip(cols, COMPANIES):
        with col:
            render_company(company)


if __name__ == "__main__":
    main()
