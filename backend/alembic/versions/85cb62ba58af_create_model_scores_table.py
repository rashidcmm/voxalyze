"""create model_scores table

Revision ID: 85cb62ba58af
Revises: d3330c50a587
Create Date: 2026-08-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '85cb62ba58af'
down_revision: Union[str, None] = 'd3330c50a587'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('model_scores',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('pronunciation_status', sa.String(length=20), nullable=False),
    sa.Column('pronunciation_result', sa.JSON(), nullable=True),
    sa.Column('pronunciation_error', sa.Text(), nullable=True),
    sa.Column('relevance_status', sa.String(length=20), nullable=False),
    sa.Column('relevance_result', sa.JSON(), nullable=True),
    sa.Column('relevance_error', sa.Text(), nullable=True),
    sa.Column('argument_status', sa.String(length=20), nullable=False),
    sa.Column('argument_result', sa.JSON(), nullable=True),
    sa.Column('argument_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_model_scores_session_id'), 'model_scores', ['session_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_model_scores_session_id'), table_name='model_scores')
    op.drop_table('model_scores')
