"""Global live-trading kill switch (Issue #178)

Revision ID: 20260928_001
Revises: 20260927_001
Create Date: 2026-09-28

Seeds ``live_kill_switch`` in ``trading.app_settings`` - the global emergency
stop of the live contour (Epic #172, block F).

Semantics (decision D2):

* ``false`` (the seeded value) - new entries are allowed;
* ``true`` - the executor rejects every new entry with the skip reason
  ``kill_switch`` and alerts the operator. Existing positions keep their
  broker-side protection: the switch never flattens a position and never
  cancels a stop, so turning it on cannot itself create an unprotected
  position;
* a **missing** or unreadable row is treated as ``true`` (fail-safe). An
  executor that cannot read its own emergency stop must not keep entering.

The trailing contour keeps its independent ``trailing_kill_switch``
(20260916_001): the global switch stops *entries*, the trailing one stops stop
*ratcheting*. Both are published by ``GET /api/live-trading/metrics``.

Idempotent: ``ON CONFLICT DO NOTHING`` never overwrites an operator's ``true``.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '20260928_001'
down_revision: Union[str, None] = '20260927_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_LIVE_KILL_SWITCH_SQL = """
INSERT INTO trading.app_settings (key, value, updated_at)
VALUES ('live_kill_switch', 'false'::jsonb, CURRENT_TIMESTAMP)
ON CONFLICT (key) DO NOTHING
"""


def upgrade() -> None:
    op.execute(_SEED_LIVE_KILL_SWITCH_SQL)


def downgrade() -> None:
    # Documented rollback: removing the row does NOT re-enable trading. The
    # executor reads a missing key as ON (fail-safe), so a downgraded database
    # blocks entries instead of quietly running without an emergency stop.
    # To roll the whole contour back, redeploy the previous image and leave
    # ALLOW_REAL_TRADING unset.
    op.execute("DELETE FROM trading.app_settings WHERE key = 'live_kill_switch'")
