from sqlalchemy import create_engine, inspect, text

from app.database.migrations import current_schema_version, upgrade_database


def test_legacy_schema_migration_is_additive_idempotent_and_preserves_rows(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE businesses "
                "(business_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO businesses (business_id, name) "
                "VALUES ('SB-LEGACY1', 'Legacy Shop')"
            )
            connection.exec_driver_sql(
                "CREATE TABLE products "
                "(id INTEGER PRIMARY KEY, business_id VARCHAR, product_name VARCHAR)"
            )
            connection.exec_driver_sql(
                "INSERT INTO products (id, business_id, product_name) "
                "VALUES (1, 'SB-LEGACY1', 'Legacy Product')"
            )
            connection.exec_driver_sql(
                "CREATE TABLE otp_verifications "
                "(id INTEGER PRIMARY KEY, phone_number VARCHAR NOT NULL, otp_hash VARCHAR NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO otp_verifications (id, phone_number, otp_hash) "
                "VALUES (1, 'legacy-phone-key', 'legacy-otp-hash')"
            )

        assert upgrade_database(engine) == 5
        assert upgrade_database(engine) == 5
        assert current_schema_version(engine) == 5

        inspector = inspect(engine)
        business_columns = {column["name"] for column in inspector.get_columns("businesses")}
        product_columns = {column["name"] for column in inspector.get_columns("products")}
        customer_columns = {
            column["name"] for column in inspector.get_columns("customer_accounts")
        }
        business_indexes = inspector.get_indexes("businesses")
        customer_indexes = inspector.get_indexes("customer_accounts")
        assert {
            "owner_name",
            "owner_phone",
            "owner_email",
            "username",
            "password_hash",
            "latitude",
            "longitude",
            "location_precision",
        }.issubset(business_columns)
        assert "low_stock_threshold" in product_columns
        assert {"customer_id", "email", "password_hash"}.issubset(customer_columns)
        assert {"delivery_address", "latitude", "longitude"}.issubset(customer_columns)
        assert "email_verified" in business_columns
        assert "email_verified" in customer_columns
        assert "email_verifications" in inspector.get_table_names()
        assert any(
            index["name"] == "uq_businesses_owner_email" and index["unique"]
            for index in business_indexes
        )
        assert any(index["unique"] for index in customer_indexes)

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT name FROM businesses WHERE business_id = 'SB-LEGACY1'")
            ).scalar_one() == "Legacy Shop"
            assert connection.execute(
                text("SELECT email_verified FROM businesses WHERE business_id = 'SB-LEGACY1'")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT product_name FROM products WHERE id = 1")
            ).scalar_one() == "Legacy Product"
            assert connection.execute(
                text("SELECT otp_hash FROM otp_verifications WHERE id = 1")
            ).scalar_one() == "legacy-otp-hash"
            assert connection.execute(
                text("SELECT COUNT(*) FROM schema_migrations WHERE version = 1")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM schema_migrations WHERE version = 2")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM schema_migrations WHERE version = 3")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM schema_migrations WHERE version = 4")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM schema_migrations WHERE version = 5")
            ).scalar_one() == 1
    finally:
        engine.dispose()
