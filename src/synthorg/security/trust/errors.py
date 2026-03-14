"""Trust domain error hierarchy."""


class TrustError(Exception):
    """Base error for all trust operations."""


class TrustEvaluationError(TrustError):
    """Error during trust evaluation."""
