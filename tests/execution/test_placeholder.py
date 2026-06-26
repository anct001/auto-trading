"""tests/execution/ — TDD home for execution (money code, §9).

Per CLAUDE.md + §7: TDD is MANDATORY in src/execution/**. Real P0+ slices land here as failing
tests first:

  - broker:    idempotency (client order ids), precision rounding, minNotional pre-check, TIF
  - stops:     exchange-side protective stop attaches at fill; survives killed process
  - reconcile: restart-safe; recover open positions, never double-trade

This placeholder only asserts the test tree is wired. Replace as real slices land.
"""


def test_execution_test_tree_is_wired():
    assert True
