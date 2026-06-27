"""Tests for backtest/multiple_testing.py — §5 data-snooping correction.

Probabilistic Sharpe Ratio (PSR) and Deflated Sharpe Ratio (DSR), per Bailey & López de Prado
(2014). The point: a strategy that looks great in isolation may be noise once you account for how
many variants were tried. DSR raises the bar with the number of trials (§5).
"""
from __future__ import annotations

import math

import pytest

from backtest.multiple_testing import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    norm_cdf,
    norm_ppf,
    prob_sharpe_ratio,
)


# ---- the normal helpers (we ship our own; pin them) ------------------------------------------

def test_norm_cdf_known_points():
    assert math.isclose(norm_cdf(0.0), 0.5, abs_tol=1e-12)
    assert math.isclose(norm_cdf(1.959963985), 0.975, abs_tol=1e-6)


def test_norm_ppf_is_inverse_of_cdf():
    for p in (0.05, 0.5, 0.84134, 0.975, 0.999):
        assert math.isclose(norm_cdf(norm_ppf(p)), p, abs_tol=1e-6)


# ---- PSR -------------------------------------------------------------------------------------

def test_psr_at_zero_excess_is_half():
    # Gaussian returns, SR == benchmark → 50/50 the true SR beats the benchmark
    assert math.isclose(prob_sharpe_ratio(0.0, n_obs=101), 0.5, abs_tol=1e-9)


def test_psr_grows_with_sharpe_and_sample():
    # higher observed SR, and more observations, both raise confidence
    base = prob_sharpe_ratio(0.3, n_obs=101)
    assert prob_sharpe_ratio(0.5, n_obs=101) > base
    assert prob_sharpe_ratio(0.3, n_obs=400) > base


def test_psr_gaussian_closed_form():
    # normal returns (skew0, kurt3): σ(SR) = √((1+0.5·SR²)/(n-1)) (Lo 2002), so
    # PSR = Φ( SR·√(n-1) / √(1+0.5·SR²) ); SR=0.5, n=101 → Φ(5/√1.125)
    expected = norm_cdf(0.5 * math.sqrt(100) / math.sqrt(1.0 + 0.5 * 0.25))
    assert math.isclose(prob_sharpe_ratio(0.5, n_obs=101), expected, abs_tol=1e-9)


def test_psr_negative_skew_hurts():
    # fat-left-tail returns lower the confidence in the same SR (denominator grows)
    sym = prob_sharpe_ratio(0.4, n_obs=200, skew=0.0, kurtosis=3.0)
    skewed = prob_sharpe_ratio(0.4, n_obs=200, skew=-1.0, kurtosis=6.0)
    assert skewed < sym


# ---- expected max Sharpe under N trials ------------------------------------------------------

def test_expected_max_sharpe_zero_for_single_trial():
    assert expected_max_sharpe(1, sr_std=1.0) == 0.0  # no multiple testing → no deflation


def test_expected_max_sharpe_increases_with_trials():
    e10 = expected_max_sharpe(10, sr_std=1.0)
    e100 = expected_max_sharpe(100, sr_std=1.0)
    assert 0.0 < e10 < e100  # more variants → higher bar expected from luck alone


def test_expected_max_sharpe_scales_with_dispersion():
    assert expected_max_sharpe(50, sr_std=2.0) > expected_max_sharpe(50, sr_std=1.0)


def test_expected_max_sharpe_reference_value():
    # N=10, σ=1 → (1-γ)·Φ⁻¹(0.9) + γ·Φ⁻¹(1-1/(10e)) ≈ 1.575
    assert math.isclose(expected_max_sharpe(10, sr_std=1.0), 1.575, abs_tol=0.02)


# ---- DSR -------------------------------------------------------------------------------------

def test_dsr_drops_as_more_variants_tried():
    common = dict(observed_sharpe=1.5, n_obs=200, sr_std=0.8)
    few = deflated_sharpe_ratio(n_trials=2, **common)
    many = deflated_sharpe_ratio(n_trials=200, **common)
    assert 0.0 <= many < few <= 1.0  # the more we tried, the less impressive the same SR


def test_dsr_single_trial_equals_psr_vs_zero():
    # one trial → no deflation → DSR == PSR(benchmark 0)
    dsr = deflated_sharpe_ratio(observed_sharpe=1.2, n_obs=200, sr_std=1.0, n_trials=1)
    assert math.isclose(dsr, prob_sharpe_ratio(1.2, n_obs=200), abs_tol=1e-9)


def test_dsr_rejects_bad_inputs():
    with pytest.raises(ValueError):
        deflated_sharpe_ratio(observed_sharpe=1.0, n_obs=1, sr_std=1.0, n_trials=5)
