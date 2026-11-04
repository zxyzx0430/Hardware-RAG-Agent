"""W3 single-source schema upgrade (mirrors app.db.schema_w3).

Revision ID: w3_0001_message_truth
Revises: a1b2c3d4e5f6

Runtime upgrades run through ``init_db`` and ``PRAGMA user_version`` because
the historical revision chain cannot reach head on a fresh database. This
revision exists so a rebuilt migration chain has the same shape.

Downgrade is intentionally partial: SQLite keeps added columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w3_0001_message_truth"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MESSAGE_COLUMNS = (
    sa.Column("seq", sa.Integer(), nullable=True),
    sa.Column("updated_at", sa.DateTime(), nullable=True),
    sa.Column("status", sa.String(), nullable=False, server_default="complete"),
    sa.Column("client_key", sa.String(), nullable=True),
    sa.Column("tool_call_id", sa.String(), nullable=True),
    sa.Column("tool_name", sa.String(), nullable=True),
)

_BACKFILL_SEQ = """
WITH ranked AS (
    SELECT rowid AS rid,
           ROW_NUMBER() OVER (
               PARTITION BY session_id ORDER BY created_at, rowid
           ) AS rn
    FROM messages
)
UPDATE messages
SET seq = (SELECT rn FROM ranked WHERE ranked.rid = messages.rowid)
"""


def upgrade() -> None:
    for column in _MESSAGE_COLUMNS:
        op.add_column("messages", column)
    op.add_column("sessions", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sessions", sa.Column("messages_imported", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "session_tombstones",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
        sa.Column("epoch", sa.String(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_table(
        "todos",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "seq"),
    )
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("env", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(_BACKFILL_SEQ)
    op.execute("UPDATE messages SET updated_at = created_at WHERE updated_at IS NULL")
    op.create_index("idx_messages_session_order", "messages", ["session_id", "created_at", "seq"])
    op.create_index("uq_messages_session_seq", "messages", ["session_id", "seq"], unique=True)
    op.create_index("uq_messages_session_client_key", "messages", ["session_id", "client_key"], unique=True)
    op.create_index("idx_todos_session", "todos", ["session_id"])
    op.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")


def downgrade() -> None:
    op.drop_index("idx_todos_session", table_name="todos")
    op.drop_index("uq_messages_session_client_key", table_name="messages")
    op.drop_index("uq_messages_session_seq", table_name="messages")
    op.drop_index("idx_messages_session_order", table_name="messages")
    op.drop_table("mcp_servers")
    op.drop_table("todos")
    op.drop_table("session_tombstones")
