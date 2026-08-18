"""create rooms tables

Revision ID: f1a9c3d7e2b4
Revises: a1e5f0c9b3d7
Create Date: 2026-08-11 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a9c3d7e2b4'
down_revision: Union[str, None] = 'a1e5f0c9b3d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rooms',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('host_user_id', sa.UUID(), nullable=False),
        sa.Column('mode', sa.Enum('IDENTIFIED', 'ANONYMOUS', name='room_mode', native_enum=False, length=20), nullable=False),
        sa.Column('status', sa.Enum('WAITING', 'LIVE', 'ENDED', 'ANALYZED', name='room_status', native_enum=False, length=20), nullable=False),
        sa.Column('join_code', sa.String(length=8), nullable=False),
        sa.Column('max_participants', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['host_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_rooms_host_user_id'), 'rooms', ['host_user_id'], unique=False)
    op.create_index(op.f('ix_rooms_status'), 'rooms', ['status'], unique=False)
    op.create_index(op.f('ix_rooms_join_code'), 'rooms', ['join_code'], unique=True)

    op.create_table(
        'room_participants',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('room_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('alias_name', sa.String(length=50), nullable=False),
        sa.Column('livekit_identity', sa.String(length=200), nullable=False),
        sa.Column('audio_path', sa.String(length=1024), nullable=True),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_room_participants_room_id'), 'room_participants', ['room_id'], unique=False)
    op.create_index(op.f('ix_room_participants_user_id'), 'room_participants', ['user_id'], unique=False)

    op.create_table(
        'room_transcript_segments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('room_id', sa.UUID(), nullable=False),
        sa.Column('participant_id', sa.UUID(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('start_s', sa.Float(), nullable=False),
        sa.Column('end_s', sa.Float(), nullable=False),
        sa.Column('is_final', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['participant_id'], ['room_participants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_room_transcript_segments_room_id'), 'room_transcript_segments', ['room_id'], unique=False)
    op.create_index(op.f('ix_room_transcript_segments_participant_id'), 'room_transcript_segments', ['participant_id'], unique=False)

    op.create_table(
        'room_reports',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('room_id', sa.UUID(), nullable=False),
        sa.Column('participant_stats', sa.JSON(), nullable=False),
        sa.Column('dominance_index', sa.Float(), nullable=False),
        sa.Column('qualitative_status', sa.String(length=20), nullable=False),
        sa.Column('qualitative_result', sa.JSON(), nullable=True),
        sa.Column('qualitative_error', sa.Text(), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_room_reports_room_id'), 'room_reports', ['room_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_room_reports_room_id'), table_name='room_reports')
    op.drop_table('room_reports')
    op.drop_index(op.f('ix_room_transcript_segments_participant_id'), table_name='room_transcript_segments')
    op.drop_index(op.f('ix_room_transcript_segments_room_id'), table_name='room_transcript_segments')
    op.drop_table('room_transcript_segments')
    op.drop_index(op.f('ix_room_participants_user_id'), table_name='room_participants')
    op.drop_index(op.f('ix_room_participants_room_id'), table_name='room_participants')
    op.drop_table('room_participants')
    op.drop_index(op.f('ix_rooms_join_code'), table_name='rooms')
    op.drop_index(op.f('ix_rooms_status'), table_name='rooms')
    op.drop_index(op.f('ix_rooms_host_user_id'), table_name='rooms')
    op.drop_table('rooms')
