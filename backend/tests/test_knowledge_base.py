import pytest

from app.knowledge_base import DOCUMENTS, KnowledgeBase


def test_search_is_lazy_and_returns_relevant_document(kb):
    assert not kb.is_ready
    result = kb.search("what is your return policy", top_k=1)
    assert kb.is_ready
    assert "return policy" in result


def test_search_returns_top_k_lines(kb):
    lines = kb.search("shipping costs for express delivery", top_k=3).split("\n")
    assert len(lines) == 3
    assert all(line in DOCUMENTS for line in lines)
    assert any("Shipping" in line for line in lines)


def test_search_rejects_invalid_top_k(kb):
    with pytest.raises(ValueError):
        kb.search("anything", top_k=0)


def test_empty_knowledge_base_is_rejected():
    with pytest.raises(ValueError):
        KnowledgeBase([], lambda: None)


def test_warm_up_only_builds_embedder_once():
    calls = []

    def factory():
        calls.append(1)
        return lambda texts: [[1.0, 0.0] for _ in texts]

    kb = KnowledgeBase(["a", "b"], factory)
    kb.warm_up()
    kb.search("x")
    assert len(calls) == 1
