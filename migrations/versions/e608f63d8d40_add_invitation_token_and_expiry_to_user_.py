"""Add invitation token and expiry to User model

Revision ID: e608f63d8d40
Revises: 
Create Date: 2025-05-04 19:13:29.607633

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e608f63d8d40'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('invitation_token', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('password',
                   existing_type=sa.VARCHAR(length=255),
                   nullable=True)
        batch_op.alter_column('full_name',
                   existing_type=sa.VARCHAR(length=100),
                   nullable=True)
        batch_op.create_unique_constraint('uq_users_invitation_token', ['invitation_token'])

    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_users_invitation_token', type_='unique')
        batch_op.alter_column('full_name',
                   existing_type=sa.VARCHAR(length=100),
                   nullable=False)
        batch_op.alter_column('password',
                   existing_type=sa.VARCHAR(length=255),
                   nullable=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('token_expires_at')
        batch_op.drop_column('invitation_token')

    # ### end Alembic commands ### 