"""tests/risk/ — TDD home for the risk engine (the most-tested module, money code).

Per CLAUDE.md + §7: TDD is MANDATORY in src/risk/** — test-first, red-green-refactor. The
first real P0 risk slices land here as failing tests before any implementation:

  - limits:          per-trade cap, soft/hard daily, drawdown kill-switch, correlation cluster
  - sizing:          inverse-ATR, fractional-Kelly cap, minNotional/lot/tick → SKIP not round-up
  - exchange_assert: spot/leverage=1/margin-off/reduceOnly mismatch → STOP
  - depeg:           quote-stablecoin off $1 → halt + re-mark
  - killswitch:      manual kill overrides; process + human dead-man's switches

This placeholder only asserts the test tree is wired. Replace as real slices land.
"""


def test_risk_test_tree_is_wired():
    assert True
