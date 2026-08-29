from unittest.mock import MagicMock

from src.featherless_review import TradeCandidate, _parse_verdict, review_candidate


def _candidate() -> TradeCandidate:
    return TradeCandidate(
        ticker="AAPL",
        direction="bullish",
        criteria_met=["trend_alignment", "monthly_ma10_bullish"],
        signal_details={
            "trend_alignment": "HH/HL confirmed on daily, 4h, 15m",
            "monthly_ma10_bullish": "August monthly candle closed 2.1% above weekly-MA10",
        },
        proposed_structure="Long $230 call, 30D expiry",
        max_risk_usd=1200.0,
        account_equity_usd=100_000.0,
    )


def test_parses_clean_json():
    verdict = _parse_verdict('{"veto": false, "confidence": 0.8, "rationale": "Confluence is coherent."}')
    assert verdict.veto is False
    assert verdict.confidence == 0.8


def test_fails_closed_on_unparseable_output():
    verdict = _parse_verdict("I cannot comply with this request.")
    assert verdict.veto is True


def test_review_candidate_uses_injected_client():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [
        MagicMock(
            message=MagicMock(
                content='{"veto": true, "confidence": 0.6, "rationale": "Risk too concentrated with existing QQQ position."}'
            )
        )
    ]
    verdict = review_candidate(_candidate(), client=mock_client)
    assert verdict.veto is True
    assert "concentrated" in verdict.rationale


def test_review_candidate_fails_closed_on_api_exception():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = ConnectionError("network unreachable")
    verdict = review_candidate(_candidate(), client=mock_client)
    assert verdict.veto is True
    assert verdict.confidence == 0.0
