"""A tiny in-memory RAG knowledge base backed by FastEmbed embeddings."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Mock e-commerce FAQ used as the agent's knowledge base.
DOCUMENTS: list[str] = [
    "Welcome to MockStore! We are open 24/7 online.",
    "Our return policy allows returns within 30 days of purchase with a valid receipt. "
    "Items must be in original condition.",
    "Refunds are processed to the original method of payment within 5-7 business days "
    "after we receive the returned item.",
    "If you want to exchange an item, please return the original item for a full refund "
    "and place a new order.",
    "Shipping takes 3-5 business days for standard delivery, and 1-2 days for express delivery.",
    "Shipping costs $5.99 for standard and $14.99 for express. Orders over $100 ship for free.",
    "You can track your order using the tracking link sent to your email after checkout.",
    "We accept all major credit cards (Visa, Mastercard, Amex), PayPal, and Apple Pay.",
    "For customer support, please email support@mockstore.com or call 1-800-MOCK-STORE.",
    "Yes, we ship internationally to over 50 countries. International shipping takes "
    "7-14 business days.",
    "If you receive a damaged or defective item, please contact us within 48 hours for a "
    "free replacement.",
    "Members of our loyalty program get free shipping on all orders over $50 and exclusive "
    "discounts.",
    "To reset your password, click 'Forgot Password' on the login screen and follow the "
    "emailed instructions.",
    "We do not offer price matching at this time.",
    "Pre-ordered items will be shipped as soon as they become available in our warehouse.",
    "You can cancel your order within 1 hour of placing it by visiting your account dashboard.",
    "Gift cards are available in amounts ranging from $10 to $500 and never expire.",
    "You can apply promo codes at checkout in the 'Discount Code' box.",
    "Only one promo code can be used per order.",
    "We source our products sustainably and ensure fair trade practices.",
    "Subscribe to our newsletter to receive a 10% discount on your first order.",
]

Embedder = Callable[[Sequence[str]], Iterable[Sequence[float]]]


def fastembed_embedder(model_name: str) -> Embedder:
    """Create an embedder backed by a local FastEmbed ONNX model."""
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=model_name)
    return lambda texts: model.embed(list(texts))


class KnowledgeBase:
    """Cosine-similarity search over a fixed set of documents.

    Embeddings are computed lazily on the first search so that importing the
    application (or running tests) never triggers a model download.
    """

    def __init__(self, documents: Sequence[str], embedder_factory: Callable[[], Embedder]):
        if not documents:
            raise ValueError("KnowledgeBase requires at least one document")
        self.documents = list(documents)
        self._embedder_factory = embedder_factory
        self._embedder: Embedder | None = None
        self._matrix: np.ndarray | None = None
        self._lock = threading.Lock()

    @property
    def is_ready(self) -> bool:
        return self._matrix is not None

    def warm_up(self) -> None:
        """Load the embedding model and index the documents (idempotent, thread-safe)."""
        if self._matrix is not None:
            return
        with self._lock:
            if self._matrix is not None:
                return
            logger.info("Indexing %d knowledge-base documents", len(self.documents))
            embedder = self._embedder_factory()
            matrix = np.asarray(list(embedder(self.documents)), dtype=np.float32)
            self._embedder = embedder
            self._matrix = _normalize(matrix)

    def search(self, query: str, top_k: int = 2) -> str:
        """Return the ``top_k`` most similar documents joined by newlines."""
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        self.warm_up()
        assert self._embedder is not None and self._matrix is not None
        query_vec = np.asarray(next(iter(self._embedder([query]))), dtype=np.float32)
        query_vec = _normalize(query_vec[np.newaxis, :])[0]
        scores = self._matrix @ query_vec
        top = np.argsort(scores)[::-1][:top_k]
        return "\n".join(self.documents[i] for i in top)


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms
