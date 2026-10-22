"""Partial unique index on (owner_id, extraction_meta->>'client_message_id').

Revision ID: 0010_task_client_message_id_unique
Revises: 0009_task_hard_deadline
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_task_client_id_unique"
down_revision = "0009_task_hard_deadline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    is_offline = getattr(op.get_context(), "as_sql", False)

    if dialect_name == "postgresql":
        # 1. Inspect existing data for duplicate non-null keys before creating the index (online only)
        if not is_offline:
            dupes_query = sa.text("""
                SELECT owner_id, extraction_meta->>'client_message_id' AS client_msg_id, count(*)
                FROM tasks
                WHERE extraction_meta IS NOT NULL
                  AND extraction_meta->>'client_message_id' IS NOT NULL
                GROUP BY owner_id, extraction_meta->>'client_message_id'
                HAVING count(*) > 1;
            """)
            dupes = bind.execute(dupes_query).fetchall()
            if dupes:
                formatted_dupes = [
                    f"owner={r[0]}, client_message_id={r[1]}, count={r[2]}" for r in dupes
                ]
                raise RuntimeError(
                    "Cannot create partial unique index uq_tasks_owner_client_message_id: "
                    "found duplicate keys in existing tasks:\n" + "\n".join(formatted_dupes)
                )

        # 2. Create the partial unique index
        op.execute(
            sa.text("""
            CREATE UNIQUE INDEX uq_tasks_owner_client_message_id
            ON tasks (owner_id, (extraction_meta->>'client_message_id'))
            WHERE extraction_meta IS NOT NULL
              AND extraction_meta->>'client_message_id' IS NOT NULL;
        """)
        )

    elif dialect_name == "sqlite":
        if not is_offline:
            dupes_query = sa.text("""
                SELECT owner_id, json_extract(extraction_meta, '$.client_message_id') AS client_msg_id, count(*)
                FROM tasks
                WHERE extraction_meta IS NOT NULL
                  AND json_extract(extraction_meta, '$.client_message_id') IS NOT NULL
                GROUP BY owner_id, json_extract(extraction_meta, '$.client_message_id')
                HAVING count(*) > 1;
            """)
            dupes = bind.execute(dupes_query).fetchall()
            if dupes:
                formatted_dupes = [
                    f"owner={r[0]}, client_message_id={r[1]}, count={r[2]}" for r in dupes
                ]
                raise RuntimeError(
                    "Cannot create partial unique index uq_tasks_owner_client_message_id: "
                    "found duplicate keys in existing tasks:\n" + "\n".join(formatted_dupes)
                )

        op.execute(
            sa.text("""
            CREATE UNIQUE INDEX uq_tasks_owner_client_message_id
            ON tasks (owner_id, json_extract(extraction_meta, '$.client_message_id'))
            WHERE extraction_meta IS NOT NULL
              AND json_extract(extraction_meta, '$.client_message_id') IS NOT NULL;
        """)
        )


def downgrade() -> None:
    op.drop_index("uq_tasks_owner_client_message_id", table_name="tasks")
