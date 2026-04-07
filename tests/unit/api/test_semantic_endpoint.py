import asyncio
from types import SimpleNamespace

from app.api.v1.endpoints import semantic


class _FakeRetriever:
    def __init__(self):
        self.reindex_calls = 0
        self.search_calls = []

    def reindex(self):
        self.reindex_calls += 1
        return 7

    def search(self, query, limit=6):
        self.search_calls.append((query, limit))
        return [{"artifact_id": "task_transaction", "score": 0.9}]


def _request(retriever):
    container = SimpleNamespace(semantic_retriever=retriever)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container)))


def test_semantic_reindex_endpoint_returns_indexed_chunk_count(monkeypatch):
    retriever = _FakeRetriever()
    monkeypatch.setattr(
        semantic.DomainRegistry,
        "get_current_domain",
        staticmethod(lambda: SimpleNamespace(name="maintenance")),
    )

    payload = asyncio.run(semantic.reindex_semantic_bundle(_request(retriever)))

    assert payload["status"] == "ok"
    assert payload["domain"] == "maintenance"
    assert payload["indexed_chunks"] == 7
    assert retriever.reindex_calls == 1


def test_semantic_search_endpoint_returns_hits(monkeypatch):
    retriever = _FakeRetriever()
    monkeypatch.setattr(
        semantic.DomainRegistry,
        "get_current_domain",
        staticmethod(lambda: SimpleNamespace(name="maintenance")),
    )

    payload = asyncio.run(semantic.search_semantic_bundle(_request(retriever), query="show tasks", limit=3))

    assert payload["status"] == "ok"
    assert payload["hits"][0]["artifact_id"] == "task_transaction"
    assert retriever.search_calls == [("show tasks", 3)]
