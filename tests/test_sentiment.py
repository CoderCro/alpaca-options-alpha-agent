from unittest.mock import MagicMock, patch

from src.execution import AlpacaCliError
from src.sentiment import get_sentiment

_NEWS_RESPONSE = {"news": [{"headline": "Company beats earnings estimates"}, {"headline": "Analysts raise price target"}]}

_HF_POSITIVE = [[{"label": "positive", "score": 0.9}, {"label": "negative", "score": 0.05}, {"label": "neutral", "score": 0.05}]]


def _hf_response(payload, status=200):
    mock_response = MagicMock()
    mock_response.status_code = status
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock() if status == 200 else MagicMock(side_effect=Exception("HTTP error"))
    return mock_response


def test_returns_none_without_a_token(monkeypatch):
    monkeypatch.delenv("HUGGINGFACE_API_TOKEN", raising=False)
    assert get_sentiment("AAPL") is None


def test_returns_none_when_news_fetch_fails(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf_fake")
    with patch("src.sentiment.execution.get_news", side_effect=AlpacaCliError("boom")):
        assert get_sentiment("AAPL") is None


def test_returns_none_with_no_headlines(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf_fake")
    with patch("src.sentiment.execution.get_news", return_value={"news": []}):
        assert get_sentiment("AAPL") is None


def test_returns_none_when_hf_api_fails(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf_fake")
    with (
        patch("src.sentiment.execution.get_news", return_value=_NEWS_RESPONSE),
        patch("src.sentiment.requests.post", side_effect=ConnectionError("network down")),
    ):
        assert get_sentiment("AAPL") is None


def test_returns_summary_string_on_success(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf_fake")
    with (
        patch("src.sentiment.execution.get_news", return_value=_NEWS_RESPONSE),
        patch("src.sentiment.requests.post", return_value=_hf_response(_HF_POSITIVE)),
    ):
        result = get_sentiment("AAPL")
    assert result is not None
    assert "positive" in result
    assert "2 headlines" in result


def test_fails_closed_on_unrecognized_response_shape(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "hf_fake")
    with (
        patch("src.sentiment.execution.get_news", return_value=_NEWS_RESPONSE),
        patch("src.sentiment.requests.post", return_value=_hf_response({"error": "model loading"})),
    ):
        assert get_sentiment("AAPL") is None
