from sqlalchemy import inspect, text

from app.utils.time import utc_now_for_database


_MIGRATIONS = (
    (1, "add_legacy_compatibility_columns",),
    (2, "add_business_owner_email",),
    (3, "add_customer_accounts",),
    (4, "add_customer_saved_locations",),
    (5, "add_email_verification",),
)

_LEGACY_COLUMNS = {
    "businesses": {
        "owner_name": "VARCHAR",
        "owner_phone": "VARCHAR",
        "username": "VARCHAR",
        "password_hash": "VARCHAR",
        "latitude": "FLOAT",
        "longitude": "FLOAT",
        "location_precision": "VARCHAR",
    },
    "orders": {
        "stock_deducted": "BOOLEAN NOT NULL DEFAULT 0",
        "customer_session_id": "VARCHAR",
        "customer_address": "VARCHAR",
        "payment_method": "VARCHAR",
    },
    "conversations": {
        "customer_session_id": "VARCHAR",
        "order_state": "JSON",
    },
    "products": {
        "low_stock_threshold": "INTEGER NOT NULL DEFAULT 2",
    },
}


def _add_business_owner_email(connection):
    inspector = inspect(connection)
    if "businesses" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("businesses")}
    if "owner_email" not in columns:
        connection.exec_driver_sql(
            'ALTER TABLE "businesses" ADD COLUMN "owner_email" VARCHAR'
        )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_businesses_owner_email "
        "ON businesses (owner_email)"
    )


def _create_customer_accounts(connection):
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS customer_accounts ("
        "customer_id VARCHAR PRIMARY KEY NOT NULL, "
        "name VARCHAR NOT NULL, "
        "email VARCHAR NOT NULL UNIQUE, "
        "password_hash VARCHAR NOT NULL, "
        "created_at DATETIME NOT NULL)"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_customer_accounts_email "
        "ON customer_accounts (email)"
    )


def _add_customer_saved_locations(connection):
    inspector = inspect(connection)
    if "customer_accounts" not in inspector.get_table_names():
        return
    columns = {
        column["name"] for column in inspector.get_columns("customer_accounts")
    }
    for column_name, column_type in (
        ("delivery_address", "VARCHAR"),
        ("latitude", "FLOAT"),
        ("longitude", "FLOAT"),
    ):
        if column_name not in columns:
            connection.exec_driver_sql(
                f'ALTER TABLE "customer_accounts" ADD COLUMN "{column_name}" {column_type}'
            )


def _add_email_verification(connection):
    existing_tables = set(inspect(connection).get_table_names())
    for table_name in ("businesses", "customer_accounts"):
        if table_name not in existing_tables:
            continue
        columns = {
            column["name"] for column in inspect(connection).get_columns(table_name)
        }
        if "email_verified" not in columns:
            connection.exec_driver_sql(
                f'ALTER TABLE "{table_name}" ADD COLUMN "email_verified" '
                "BOOLEAN NOT NULL DEFAULT 1"
            )
        connection.exec_driver_sql(
            f'UPDATE "{table_name}" SET "email_verified" = 1 '
            'WHERE "email_verified" IS NULL'
        )

    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS email_verifications ("
        "email VARCHAR PRIMARY KEY NOT NULL, "
        "code_hash VARCHAR NOT NULL, "
        "created_at DATETIME NOT NULL, "
        "expires_at DATETIME NOT NULL, "
        "last_sent_at DATETIME NOT NULL, "
        "attempt_count INTEGER NOT NULL DEFAULT 0)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_email_verifications_expires_at "
        "ON email_verifications (expires_at)"
    )


def _ensure_migration_table(connection):
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, applied_at DATETIME NOT NULL)"
    )


def _add_legacy_compatibility_columns(connection):
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table_name, required_columns in _LEGACY_COLUMNS.items():
        if table_name not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'
                )
                existing_columns.add(column_name)


def current_schema_version(engine):
    inspector = inspect(engine)
    if "schema_migrations" not in inspector.get_table_names():
        return 0
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
        ).scalar_one()


def upgrade_database(engine):
    with engine.begin() as connection:
        _ensure_migration_table(connection)

    version = current_schema_version(engine)
    for migration_version, migration_name in _MIGRATIONS:
        if migration_version <= version:
            continue
        with engine.begin() as connection:
            if migration_version == 1:
                _add_legacy_compatibility_columns(connection)
            elif migration_version == 2:
                _add_business_owner_email(connection)
            elif migration_version == 3:
                _create_customer_accounts(connection)
            elif migration_version == 4:
                _add_customer_saved_locations(connection)
            elif migration_version == 5:
                _add_email_verification(connection)
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (:version, :name, :applied_at)"
                ),
                {
                    "version": migration_version,
                    "name": migration_name,
                    "applied_at": utc_now_for_database(),
                },
            )
    return current_schema_version(engine)
