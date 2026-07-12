"""tool_audit add call_id / success / error_type columns.

Revision ID: f4a1b2c3d4e5
Revises: 9467f02f4f43
Create Date: 2026-07-01 00:00:00

Adds the industrial-tool-runtime Task 6 extended audit fields to the
tool_audit table so AuditRecorder can persist call_id / success / error_type.
The columns are nullable / defaulted so legacy callers keep working.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4a1b2c3d4e5"
down_revision: Union[str, None] = "9467f02f4f43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE: str = "tool_audit"
_CALL_ID: str = "call_id"
_SUCCESS: str = "success"
_ERROR_TYPE: str = "error_type"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_CALL_ID, sa.String(length=64), nullable=True))
    op.add_column(_TABLE, sa.Column(_SUCCESS, sa.Boolean(), nullable=True, server_default=sa.true()))
    op.add_column(_TABLE, sa.Column(_ERROR_TYPE, sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _ERROR_TYPE)
    op.drop_column(_TABLE, _SUCCESS)
    op.drop_column(_TABLE, _CALL_ID)
