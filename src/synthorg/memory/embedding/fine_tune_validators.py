"""Shared field validators for the fine-tuning pipeline models."""

from pathlib import PurePosixPath, PureWindowsPath


def assert_safe_base_model(value: str | None) -> None:
    """Reject base-model references that could trigger remote-code / SSRF loads.

    The fine-tune backend passes ``base_model`` straight to the embedding
    library's model loader (``SentenceTransformer(base_model)``). A URL scheme
    would let that loader fetch -- and on legacy pickle weight formats, execute
    -- an arbitrary remote artefact, and parent-directory traversal or a Windows
    path would escape the model store. All three are rejected here; Hugging
    Face ``org/name`` identifiers and POSIX local paths pass. This is the
    boundary half of the defence; the call sites also pin
    ``trust_remote_code=False``.

    Raises:
        ValueError: If the reference is a URL, contains parent-directory
            traversal, or uses a backslash / drive letter.
    """
    if value is None:
        return
    if "://" in value:
        msg = "base_model must be a model id or POSIX path, not a URL"
        raise ValueError(msg)
    parts = PureWindowsPath(value).parts + PurePosixPath(value).parts
    if ".." in parts:
        msg = "base_model must not contain parent-directory traversal (..)"
        raise ValueError(msg)
    if "\\" in value or (len(value) >= 2 and value[1] == ":"):  # noqa: PLR2004
        msg = (
            "base_model must be a POSIX path or model id "
            "(no backslashes or drive letters)"
        )
        raise ValueError(msg)


__all__ = ["assert_safe_base_model"]
