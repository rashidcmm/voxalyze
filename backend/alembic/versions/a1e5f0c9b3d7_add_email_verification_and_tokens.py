"""add email verification and tokens

Revision ID: a1e5f0c9b3d7
Revises: 85cb62ba58af
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1e5f0c9b3d7'
down_revision: Union[str, None] = '85cb62ba58af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'users',
        sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        'email_tokens',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('purpose', sa.String(length=10), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_email_tokens_user_id'), 'email_tokens', ['user_id'], unique=False)
    op.create_index(op.f('ix_email_tokens_token_hash'), 'email_tokens', ['token_hash'], unique=True)
    op.create_index(op.f('ix_email_tokens_purpose'), 'email_tokens', ['purpose'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_tokens_purpose'), table_name='email_tokens')
    op.drop_index(op.f('ix_email_tokens_token_hash'), table_name='email_tokens')
    op.drop_index(op.f('ix_email_tokens_user_id'), table_name='email_tokens')
    op.drop_table('email_tokens')
    op.drop_column('users', 'password_changed_at')
    op.drop_column('users', 'is_verified')
