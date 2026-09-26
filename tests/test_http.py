import pytest
import requests

from src.utils.http import HTTPFailure, get
from tests.conftest import FakeResponse

HTTP_CFG = {"timeout_seconds": 1, "retries": 3, "backoff_seconds": [2, 4, 8]}


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retries_then_succeeds():
    waits = []
    session = FakeSession([requests.ConnectionError("down"), FakeResponse(status_code=503), FakeResponse([1])])
    r = get("http://x", http_cfg=HTTP_CFG, session=session, sleep=waits.append)
    assert r.json() == [1]
    assert session.calls == 3
    assert waits == [2, 4]


def test_gives_up_after_all_attempts():
    session = FakeSession([FakeResponse(status_code=500)] * 4)
    with pytest.raises(HTTPFailure, match="after 4 attempts"):
        get("http://x", http_cfg=HTTP_CFG, session=session, sleep=lambda s: None)
    assert session.calls == 4


def test_client_error_is_not_retried():
    session = FakeSession([FakeResponse(status_code=404)])
    with pytest.raises(HTTPFailure, match="not retried"):
        get("http://x", http_cfg=HTTP_CFG, session=session, sleep=lambda s: None)
    assert session.calls == 1
