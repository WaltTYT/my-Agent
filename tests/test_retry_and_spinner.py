from __future__ import annotations

import asyncio

import pytest

from agents import ui
from agents.agent import _is_retryable, _with_retry


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize("message", [
    "Error code: 429 - {'code': '1113'}",
    "429 余额不足或无可用资源包",
    "429 insufficient_quota",
])
def test_quota_errors_are_not_retryable(message: str):
    assert not _is_retryable(ApiError(429, message))


def test_rate_limit_429_remains_retryable():
    assert _is_retryable(ApiError(429, "rate limit exceeded"))


def test_quota_error_is_not_retried():
    attempts = 0

    async def fail_for_empty_balance():
        nonlocal attempts
        attempts += 1
        raise ApiError(429, "余额不足或无可用资源包")

    with pytest.raises(ApiError):
        asyncio.run(_with_retry(fail_for_empty_balance))

    assert attempts == 1


def test_stop_spinner_finishes_line_after_clearing_spinner(monkeypatch):
    output = []

    class StubThread:
        def join(self, timeout):
            pass

    monkeypatch.setattr(ui, "_spinner_thread", StubThread())
    monkeypatch.setattr(ui, "_safe_stdout_write", output.append)

    ui.stop_spinner(newline=True)

    assert output == ["\r\033[K", "\n"]
