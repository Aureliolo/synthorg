"""Welch's unequal-variance two-sample t-test.

Implements the t-statistic, the Welch-Satterthwaite approximation for
the degrees of freedom, and a two-sided p-value via the regularized
incomplete beta function using the classic Numerical Recipes continued
fraction. No numpy/scipy dependency.

The two-sided p-value for Student-t(df) with statistic ``t`` is::

    p = I_{df / (df + t**2)}(df / 2, 1/2)

where ``I_x(a, b)`` is the regularized incomplete beta function.
"""

import math
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.observability import get_logger, safe_error_description

logger = get_logger(__name__)

_MIN_SAMPLES_PER_ARM: Final[int] = 2


class InsufficientDataError(
    ValueError,
):  # lint-allow: domain-error-hierarchy -- statistical precondition
    """Raised when either arm has fewer than two observations."""


class ZeroVarianceError(
    ValueError,
):  # lint-allow: domain-error-hierarchy -- statistical precondition
    """Raised when the combined Welch statistics are undefined.

    The guard fires when ``se_sq <= 0`` (both arms flat, so the
    t-statistic divides by zero) or when ``df_den <= 0`` (the
    Satterthwaite denominator collapses). A single constant arm is
    allowed: the non-zero variance of the other arm keeps ``se_sq``
    and ``df_den`` positive, so Welch still runs. Callers should
    treat this error as insufficient data.
    """


class WelchResult(BaseModel):
    """Result of a Welch's t-test.

    Attributes:
        t: The t-statistic ``(mean_a - mean_b) / se``.
        df: Welch-Satterthwaite degrees of freedom.
        p_two_sided: Two-sided p-value in ``[0, 1]``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    t: float
    df: float = Field(gt=0.0)
    p_two_sided: float = Field(ge=0.0, le=1.0)


def welch_t_test(
    a: Sequence[float],
    b: Sequence[float],
) -> WelchResult:
    """Compute Welch's unequal-variance two-sample t-test.

    Args:
        a: Samples from the first group (``n_a >= 2``).
        b: Samples from the second group (``n_b >= 2``).

    Returns:
        Result with ``t``, ``df``, and two-sided p-value.

    Raises:
        InsufficientDataError: If either arm has fewer than 2 samples.
        ZeroVarianceError: If the combined standard-error squared
            (``se_sq``) or the Welch degrees-of-freedom denominator
            (``df_den``) is non-positive. A single constant arm is
            accepted; both arms flat is rejected.
    """
    n_a = len(a)
    n_b = len(b)
    if n_a < _MIN_SAMPLES_PER_ARM or n_b < _MIN_SAMPLES_PER_ARM:
        msg = f"need >=2 samples per arm; got n_a={n_a}, n_b={n_b}"
        raise InsufficientDataError(msg)

    mean_a = math.fsum(a) / n_a
    mean_b = math.fsum(b) / n_b
    var_a = math.fsum((x - mean_a) ** 2 for x in a) / (n_a - 1)
    var_b = math.fsum((x - mean_b) ** 2 for x in b) / (n_b - 1)

    # Welch can still run when exactly one arm is constant: the pooled
    # standard error stays positive and the df ratio stays finite. Only
    # reject when both arms are flat (se_sq == 0, t undefined) or when
    # the df denominator collapses.
    se_sq = var_a / n_a + var_b / n_b
    df_den = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    if se_sq <= 0.0 or df_den <= 0.0:
        msg = (
            f"Welch requires positive se_sq and df_den; "
            f"got se_sq={se_sq}, df_den={df_den} "
            f"(var_a={var_a}, var_b={var_b})"
        )
        raise ZeroVarianceError(msg)

    t = (mean_a - mean_b) / math.sqrt(se_sq)
    df_num = se_sq * se_sq
    df = df_num / df_den

    x = df / (df + t * t)
    try:
        p_two_sided = _regularized_incomplete_beta(df / 2.0, 0.5, x)
    except RuntimeError as exc:
        # The continued-fraction routine can fail to converge on
        # pathological inputs. Translate to the public Welch failure
        # type so callers treat it like any other Welch precondition
        # miss instead of leaking a raw RuntimeError.
        msg = f"Welch p-value computation failed to converge: {safe_error_description(exc)}"  # noqa: E501
        raise InsufficientDataError(msg) from exc
    p_two_sided = min(1.0, max(0.0, p_two_sided))
    return WelchResult(t=t, df=df, p_two_sided=p_two_sided)


_BETACF_MAX_ITER: Final[int] = 200
_BETACF_EPS: Final[float] = 3.0e-15
_BETACF_FPMIN: Final[float] = 1.0e-300


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta ``I_x(a, b)`` via log-gamma + CF.

    Implements Numerical Recipes' ``betai`` / ``betacf`` algorithm.
    Returns ``0.0`` at ``x <= 0`` and ``1.0`` at ``x >= 1``. The
    log-gamma normalisation keeps the computation numerically stable
    for the degrees-of-freedom values encountered by Welch's t-test.

    Returns:
        Resulting numeric value.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _betacf(a: float, b: float, x: float) -> float:
    """Continued-fraction evaluation of the incomplete beta function.

    Converges when ``x < (a + 1) / (a + b + 2)``; callers must swap
    arguments (``a <-> b``, ``x -> 1 - x``) when ``x`` is larger.
    Iterates up to ``_BETACF_MAX_ITER`` with a convergence tolerance
    of ``_BETACF_EPS``; raises ``RuntimeError`` if the fraction fails
    to converge (indicates pathological input, not a normal failure
    mode).

    Returns:
        Resulting numeric value.

    Raises:
        RuntimeError: Raised on the corresponding failure path.
    """
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _BETACF_FPMIN:
        d = _BETACF_FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _BETACF_MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _BETACF_FPMIN:
            d = _BETACF_FPMIN
        c = 1.0 + aa / c
        if abs(c) < _BETACF_FPMIN:
            c = _BETACF_FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _BETACF_FPMIN:
            d = _BETACF_FPMIN
        c = 1.0 + aa / c
        if abs(c) < _BETACF_FPMIN:
            c = _BETACF_FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _BETACF_EPS:
            return h
    msg = f"betacf failed to converge for a={a}, b={b}, x={x}"
    raise RuntimeError(msg)
