"""Alembic migration script template."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The tables are auto-created by Base.metadata.create_all in the lifespan.
    # Add manual migrations here as the schema evolves.
    pass


def downgrade() -> None:
    pass
