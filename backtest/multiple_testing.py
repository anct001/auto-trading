"""backtest/multiple_testing.py — §5 data-snooping / multiple-testing correction.

Per-strategy walk-forward is not enough: when you test *many* hypotheses (especially LLM-generated
batches in P2), some pass by luck. This module implements the Probabilistic Sharpe Ratio (PSR) and
the Deflated Sharpe Ratio (DSR) of Bailey & López de Prado (2014):

  - **PSR(SR*)** — the probability the *true* Sharpe exceeds a benchmark SR*, given the observed
    Sharpe, sample length, and the return distribution's skew/kurtosis (non-normal returns are
    penalised). PSR = Φ( (SR − SR*)·√(n−1) / √(1 − γ3·SR + (γ4−1)/4·SR²) ).
  - **Expected max Sharpe** under N independent trials of zero-skill strategies:
    E[max] ≈ σ_SR · [ (1−γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) ], γ = Euler–Mascheroni.
  - **DSR** = PSR evaluated against that expected max as the benchmark. The more variants tried
    (larger N) the higher the bar, so the same observed Sharpe deflates toward noise.

Pure/deterministic and dependency-free: we ship our own Φ (via math.erf) and Φ⁻¹ (Acklam's
rational approximation) so this needs no scipy. ``sr_std`` (the dispersion of Sharpe estimates
across the trials) is naturally supplied by the std of the journal's recorded trial Sharpes (§15).
"""
from __future__ import annotations

import math

_GAMMA = 0.5772156649015329  # Euler–Mascheroni constant
_E = math.e


def norm_cdf(x: float) -> float:
    """Standard normal CDF Φ(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Acklam's inverse-normal-CDF rational approximation (|error| < ~1.15e-9 over (0,1)).
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
_P_LOW = 0.02425
_P_HIGH = 1.0 - _P_LOW


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF Φ⁻¹(p), p in (0, 1)."""
    if not (0.0 < p < 1.0):
        raise ValueError(f"norm_ppf requires p in (0, 1), got {p}")
    if p < _P_LOW:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    if p <= _P_HIGH:
        q = p - 0.5
        r = q * q
        return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
               (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)


def prob_sharpe_ratio(
    observed_sharpe: float,
    *,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    sr_benchmark: float = 0.0,
) -> float:
    """Probabilistic Sharpe Ratio: P(true SR > ``sr_benchmark``). ``kurtosis`` is non-excess (3=normal)."""
    if n_obs < 2:
        raise ValueError("PSR needs at least 2 observations")
    sr = observed_sharpe
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr))
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1) / denom
    return norm_cdf(z)


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """Expected maximum Sharpe across ``n_trials`` zero-skill strategies (the DSR benchmark)."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if n_trials == 1 or sr_std <= 0:
        return 0.0  # no multiple testing (or no dispersion) → nothing to deflate against
    return sr_std * ((1.0 - _GAMMA) * norm_ppf(1.0 - 1.0 / n_trials)
                     + _GAMMA * norm_ppf(1.0 - 1.0 / (n_trials * _E)))


def deflated_sharpe_ratio(
    *,
    observed_sharpe: float,
    n_obs: int,
    sr_std: float,
    n_trials: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio: PSR against the expected-max-Sharpe benchmark for ``n_trials`` (§5)."""
    benchmark = expected_max_sharpe(n_trials, sr_std)
    return prob_sharpe_ratio(
        observed_sharpe, n_obs=n_obs, skew=skew, kurtosis=kurtosis, sr_benchmark=benchmark
    )
