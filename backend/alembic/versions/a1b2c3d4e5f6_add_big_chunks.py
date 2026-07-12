"""add big_chunks table and knowledge_bases.big_chunk_max_chars.

Revision ID: a1b2c3d4e5f6
Revises: f4a1b2c3d4e5
Create Date: 2026-07-11 00:00:00

Creates the big_chunks table for ParentDocument retrieval mode and adds
big_chunk_max_chars column to knowledge_bases for configurable big chunk size.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f4a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE_BIG_CHUNKS: str = "big_chunks"
_TABLE_KB: str = "knowledge_bases"
_COL_BIG_CHUNK_MAX_CHARS: str = "big_chunk_max_chars"


def upgrade() -> None:
    op.create_table(
        _TABLE_BIG_CHUNKS,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("big_chunk_id", sa.String(), nullable=False),
        sa.Column("doc_id", sa.String(), nullable=False),
        sa.Column("kb_id", sa.String(), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_big_chunks_big_chunk_id"),
        _TABLE_BIG_CHUNKS,
        ["big_chunk_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_big_chunks_doc_id"), _TABLE_BIG_CHUNKS, ["doc_id"], unique=False
    )
    op.create_index(
        op.f("ix_big_chunks_kb_id"), _TABLE_BIG_CHUNKS, ["kb_id"], unique=False
    )

    op.add_column(
        _TABLE_KB,
        sa.Column(
            _COL_BIG_CHUNK_MAX_CHARS,
            sa.Integer(),
            nullable=True,
            server_default="4000",
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE_KB, _COL_BIG_CHUNK_MAX_CHARS)
    op.drop_index(op.f("ix_big_chunks_kb_id"), table_name=_TABLE_BIG_CHUNKS)
    op.drop_index(op.f("ix_big_chunks_doc_id"), table_name=_TABLE_BIG_CHUNKS)
    op.drop_index(op.f("ix_big_chunks_big_chunk_id"), table_name=_TABLE_BIG_CHUNKS)
    op.drop_table(_TABLE_BIG_CHUNKS)
