import copy
import json

import pytest

from src.config import load_config


class FakeResponse:
    def __init__(self, payload=None, content=None, status_code=200):
        self.payload = payload
        self.content = content if content is not None else json.dumps(payload).encode()
        self.status_code = status_code

    def json(self):
        return self.payload


@pytest.fixture
def cfg():
    c = copy.deepcopy(load_config())
    c["sources"]["incidents"]["page_size"] = 2     # small pages so tests exercise pagination
    c["http"]["backoff_seconds"] = [0, 0, 0]
    return c
