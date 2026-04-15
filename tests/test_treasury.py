import httpx
import pytest

from nukefm.treasury import SolanaTreasury


def test_rpc_call_retries_rate_limited_http_error(monkeypatch) -> None:
    treasury = object.__new__(SolanaTreasury)
    attempts = {"count": 0}

    def fake_sleep(_: int) -> None:
        return None

    def flaky_operation():
        attempts["count"] += 1
        if attempts["count"] == 1:
            request = httpx.Request("GET", "https://api.mainnet-beta.solana.com")
            response = httpx.Response(status_code=429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)
        return "ok"

    monkeypatch.setattr("nukefm.treasury.sleep", fake_sleep)

    assert treasury._rpc_call(flaky_operation, description="test rpc call") == "ok"
    assert attempts["count"] == 2


def test_rpc_call_does_not_retry_non_retryable_error(monkeypatch) -> None:
    treasury = object.__new__(SolanaTreasury)
    attempts = {"count": 0}

    def fake_sleep(_: int) -> None:
        return None

    def broken_operation():
        attempts["count"] += 1
        request = httpx.Request("GET", "https://api.mainnet-beta.solana.com")
        response = httpx.Response(status_code=500, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)

    monkeypatch.setattr("nukefm.treasury.sleep", fake_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        treasury._rpc_call(broken_operation, description="test rpc call")

    assert attempts["count"] == 1
