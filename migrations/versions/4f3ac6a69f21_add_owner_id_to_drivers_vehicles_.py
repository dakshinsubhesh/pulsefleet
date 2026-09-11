
"""add owner_id to drivers vehicles shipments

Revision ID: 4f3ac6a69f21
Revises: 9a8c63d99c1c
Create Date: 2026-09-10 01:34:53.559615

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4f3ac6a69f21"
down_revision: Union[str, Sequence[str], None] = "9a8c63d99c1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add owner_id (FK -> users.id) to drivers, vehicles, and shipments.

    Migration strategy:
    1. Add owner_id as nullable so existing rows are allowed temporarily.
    2. Find the earliest registered user.
    3. Backfill all existing rows with that user's id.
    4. Verify that no NULL owner_id values remain.
    5. Make owner_id NOT NULL.
    6. Add foreign-key constraints to users.id.

    If existing fleet data is present but no user exists, the migration
    stops with a clear error instead of creating invalid NULL owner_id data.
    """

    bind = op.get_bind()

    # ---------------------------------------------------------
    # 1. Add owner_id columns as nullable
    # ---------------------------------------------------------

    op.add_column(
        "drivers",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )

    op.add_column(
        "vehicles",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )

    op.add_column(
        "shipments",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )

    # ---------------------------------------------------------
    # 2. Find the earliest registered user
    # ---------------------------------------------------------

    first_user_id = bind.execute(
        sa.text(
            """
            SELECT id
            FROM users
            ORDER BY id ASC
            LIMIT 1
            """
        )
    ).scalar()

    # ---------------------------------------------------------
    # 3. Check whether existing data needs backfilling
    # ---------------------------------------------------------

    driver_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM drivers")
    ).scalar_one()

    vehicle_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM vehicles")
    ).scalar_one()

    shipment_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM shipments")
    ).scalar_one()

    existing_data_count = (
        driver_count
        + vehicle_count
        + shipment_count
    )

    # ---------------------------------------------------------
    # 4. Backfill existing data
    # ---------------------------------------------------------

    if existing_data_count > 0:

        # Existing rows require an owner.
        if first_user_id is None:
            raise RuntimeError(
                "Day 8 migration cannot continue: existing drivers, "
                "vehicles, or shipments were found, but the users table "
                "contains no users. Create at least one user with "
                "POST /auth/register and then run 'alembic upgrade head' again."
            )

        # Drivers
        bind.execute(
            sa.text(
                """
                UPDATE drivers
                SET owner_id = :user_id
                WHERE owner_id IS NULL
                """
            ),
            {"user_id": first_user_id},
        )

        # Vehicles
        bind.execute(
            sa.text(
                """
                UPDATE vehicles
                SET owner_id = :user_id
                WHERE owner_id IS NULL
                """
            ),
            {"user_id": first_user_id},
        )

        # Shipments
        bind.execute(
            sa.text(
                """
                UPDATE shipments
                SET owner_id = :user_id
                WHERE owner_id IS NULL
                """
            ),
            {"user_id": first_user_id},
        )

    # ---------------------------------------------------------
    # 5. Verify that no NULL owner_id values remain
    # ---------------------------------------------------------

    null_drivers = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM drivers
            WHERE owner_id IS NULL
            """
        )
    ).scalar_one()

    null_vehicles = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM vehicles
            WHERE owner_id IS NULL
            """
        )
    ).scalar_one()

    null_shipments = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM shipments
            WHERE owner_id IS NULL
            """
        )
    ).scalar_one()

    if (
        null_drivers > 0
        or null_vehicles > 0
        or null_shipments > 0
    ):
        raise RuntimeError(
            "Day 8 migration aborted: NULL owner_id values remain. "
            f"drivers={null_drivers}, "
            f"vehicles={null_vehicles}, "
            f"shipments={null_shipments}"
        )

    # ---------------------------------------------------------
    # 6. Make owner_id NOT NULL
    # ---------------------------------------------------------

    op.alter_column(
        "drivers",
        "owner_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.alter_column(
        "vehicles",
        "owner_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.alter_column(
        "shipments",
        "owner_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    # ---------------------------------------------------------
    # 7. Add foreign-key constraints
    # ---------------------------------------------------------

    op.create_foreign_key(
        "fk_drivers_owner_id_users",
        "drivers",
        "users",
        ["owner_id"],
        ["id"],
    )

    op.create_foreign_key(
        "fk_vehicles_owner_id_users",
        "vehicles",
        "users",
        ["owner_id"],
        ["id"],
    )

    op.create_foreign_key(
        "fk_shipments_owner_id_users",
        "shipments",
        "users",
        ["owner_id"],
        ["id"],
    )


def downgrade() -> None:
    """
    Remove owner_id columns and their foreign-key constraints.
    """

    # Remove shipment FK + column
    op.drop_constraint(
        "fk_shipments_owner_id_users",
        "shipments",
        type_="foreignkey",
    )

    op.drop_column(
        "shipments",
        "owner_id",
    )

    # Remove vehicle FK + column
    op.drop_constraint(
        "fk_vehicles_owner_id_users",
        "vehicles",
        type_="foreignkey",
    )

    op.drop_column(
        "vehicles",
        "owner_id",
    )

    # Remove driver FK + column
    op.drop_constraint(
        "fk_drivers_owner_id_users",
        "drivers",
        type_="foreignkey",
    )

    op.drop_column(
        "drivers",
        "owner_id",
    )