# module-kind: adapter
"""Neural text embedder backed by the optional ``sentence-transformers`` extra.

This is the canonical home for the sentence-transformers integration: every
neural-embedding consumer (the meeting conflict detector, future semantic
search) builds its embedder from here rather than importing the SDK directly,
so the optional dependency is bound at one boundary. The default install stays
dependency-light -- the embedder raises :class:`MemoryEmbedderUnavailableError`
when the extra is absent and callers degrade to a dependency-free embedder.
"""

from typing import Final

from synthorg.memory.errors import MemoryEmbedderUnavailableError

_DEFAULT_ST_MODEL: Final[str] = "all-MiniLM-L6-v2"


class SentenceTransformerEmbedder:
    """Neural embedder backed by the optional ``sentence-transformers`` extra.

    Args:
        model_name: The sentence-transformers model id to load.

    Raises:
        MemoryEmbedderUnavailableError: When the ``sentence-transformers``
            extra is not installed.
    """

    def __init__(self, *, model_name: str = _DEFAULT_ST_MODEL) -> None:
        try:
            from sentence_transformers import (  # noqa: PLC0415
                SentenceTransformer,
            )
        except ImportError as exc:
            msg = (
                "sentence-transformers extra not installed; the "
                "'sentence_transformer' embedder strategy is unavailable"
            )
            raise MemoryEmbedderUnavailableError(msg) from exc
        self._model = SentenceTransformer(model_name)

    def embed(self, text: str) -> tuple[float, ...]:
        """Embed *text* with the loaded sentence-transformers model.

        Returns:
            The model's normalised embedding vector.
        """
        vector = self._model.encode(text, normalize_embeddings=True)
        return tuple(vector.tolist())
