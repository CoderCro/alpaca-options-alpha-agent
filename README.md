# Options Alpha Agent

Autonomous options trading agent built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (lablab.ai x Alpaca, 28 Aug - 4 Sep 2026).

## Strategy

**Watchlist** (human-approved; agent may recommend additions):
- US-listed stocks, market cap >= $10B
- SPY, SPX, QQQ (proxy for NDX/NDQ -- Alpaca does not support NDX index options, only SPX/SPXW/VIX/VIXW/DJX/XSP)
- Crypto: BTC, ETH, LINK only

**Trading list**: watchlist tickers meeting >= 2 of these 4 criteria:
1. Support & resistance -- weekly candle highs/lows confirmed by 1-2 touches; daily chart takes over the weekly level ahead of a third touch
2. Trend alignment -- higher highs / higher lows on daily, 4h, and 15m charts
3. Daily MA20/MA50/MA100 acting as strong support/resistance
4. Monthly candle closes bullish above the weekly-timeframe MA10

**Blackout rules** (`src/calendar_blackout.py`):
- No trades 2h before/after the 9:30 ET market open
- No trades the day before/of/after a Tier-1 macro event (FOMC decision, NFP jobs report)

**Featherless AI's role**: the rules engine above is fully deterministic. Featherless (serverless open-source model inference) is used only for (1) a pre-trade sanity check that can veto a candidate but never originates one, and (2) plain-language reasoning narration for the demo dashboard.

**Execution**: Alpaca Trading API via MCP server / CLI, options only (defined-risk: long calls/puts or vertical spreads), on a fresh paper account seeded at $100,000, dedicated to this hackathon.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in Alpaca + Featherless keys
python -m pytest
```
