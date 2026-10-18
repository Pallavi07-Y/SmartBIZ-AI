import enum
import os
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from dotenv import load_dotenv

from app.utils.auth import hash_password


load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/business.sqlite3")


class Base(DeclarativeBase):
	pass


class ConversationStatus(str, enum.Enum):
	AI_HANDLING = "AI_HANDLING"
	ESCALATED = "ESCALATED"
	KEEPER_HANDLING = "KEEPER_HANDLING"
	RESOLVED = "RESOLVED"


class MessageSender(str, enum.Enum):
	CUSTOMER = "customer"
	AI = "ai"
	KEEPER = "keeper"


class Business(Base):
	__tablename__ = "businesses"

	business_id: Mapped[str] = mapped_column(String, primary_key=True, unique=True)
	name: Mapped[str] = mapped_column(String, nullable=False)
	owner_name: Mapped[str | None] = mapped_column(String, nullable=True)
	category: Mapped[str | None] = mapped_column(String)
	address: Mapped[str | None] = mapped_column(String)
	latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
	longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
	location_precision: Mapped[str | None] = mapped_column(String, nullable=True)
	owner_phone: Mapped[str | None] = mapped_column(String, unique=True)
	username: Mapped[str | None] = mapped_column(String, unique=True)
	password_hash: Mapped[str | None] = mapped_column(String)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OtpVerification(Base):
	__tablename__ = "otp_verifications"

	id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
	phone_number: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
	otp_hash: Mapped[str] = mapped_column(String, nullable=False)
	expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
	attempts_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
	max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
	last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
	verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Conversation(Base):
	__tablename__ = "conversations"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	business_id: Mapped[str] = mapped_column(ForeignKey("businesses.business_id"))
	customer_session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
	customer_phone: Mapped[str | None] = mapped_column(String)
	order_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
	status: Mapped[ConversationStatus] = mapped_column(
		Enum(ConversationStatus),
		default=ConversationStatus.AI_HANDLING,
	)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
	updated_at: Mapped[datetime] = mapped_column(
		DateTime,
		default=datetime.utcnow,
		onupdate=datetime.utcnow,
	)


class Message(Base):
	__tablename__ = "messages"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
	sender: Mapped[MessageSender] = mapped_column(Enum(MessageSender))
	text: Mapped[str] = mapped_column(String, nullable=False)
	timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Notification(Base):
	__tablename__ = "notifications"

	notification_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: f"NTF-{uuid.uuid4().hex[:8].upper()}")
	business_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
	type: Mapped[str] = mapped_column(String, nullable=False)
	title: Mapped[str] = mapped_column(String, nullable=False)
	message: Mapped[str] = mapped_column(String, nullable=False)
	reference_type: Mapped[str | None] = mapped_column(String)
	reference_id: Mapped[str | None] = mapped_column(String)
	is_read: Mapped[bool] = mapped_column(default=False)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Order(Base):
	__tablename__ = "orders"

	id: Mapped[str] = mapped_column(
		String,
		primary_key=True,
		default=lambda: str(uuid.uuid4()),
	)
	business_id: Mapped[str] = mapped_column(ForeignKey("businesses.business_id"))
	customer_session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
	customer_phone: Mapped[str] = mapped_column(String, nullable=False)
	customer_address: Mapped[str | None] = mapped_column(String, nullable=True)
	items: Mapped[list] = mapped_column(JSON, nullable=False)
	status: Mapped[str] = mapped_column(String, nullable=False)
	payment_method: Mapped[str | None] = mapped_column(String, nullable=True)
	stock_deducted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Product(Base):
	__tablename__ = "products"

	id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
	business_id: Mapped[str] = mapped_column(ForeignKey("businesses.business_id"), index=True)
	product_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
	sku: Mapped[str | None] = mapped_column(String, nullable=True)
	category: Mapped[str | None] = mapped_column(String, nullable=True)
	brand: Mapped[str | None] = mapped_column(String, nullable=True)
	price: Mapped[int | None] = mapped_column(Integer, nullable=True)
	ram_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
	storage_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
	processor: Mapped[str | None] = mapped_column(String, nullable=True)
	description: Mapped[str | None] = mapped_column(String, nullable=True)
	availability: Mapped[str | None] = mapped_column(String, nullable=True)
	stock_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
	low_stock_threshold: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
	is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
	updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KnowledgeDocument(Base):
	__tablename__ = "knowledge_documents"

	document_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
	business_id: Mapped[str] = mapped_column(ForeignKey("businesses.business_id"), index=True)
	document_name: Mapped[str] = mapped_column(String, nullable=False)
	document_type: Mapped[str | None] = mapped_column(String, nullable=True)
	source: Mapped[str | None] = mapped_column(String, nullable=True)
	status: Mapped[str] = mapped_column(String, default="PROCESSING", nullable=False)
	chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
	updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CartItem(Base):
	__tablename__ = "cart_items"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	session_id: Mapped[str] = mapped_column(String, index=True)
	business_id: Mapped[str] = mapped_column(String, index=True)
	product_name: Mapped[str] = mapped_column(String)
	quantity: Mapped[int] = mapped_column(Integer)
	price: Mapped[int | None] = mapped_column(Integer)
	availability: Mapped[str | None] = mapped_column(String)


def _create_engine(database_url):
	options = {}
	if database_url == "sqlite:///:memory:":
		options = {
			"connect_args": {"check_same_thread": False},
			"poolclass": StaticPool,
		}
	elif database_url.startswith("sqlite:///"):
		database_path = database_url.removeprefix("sqlite:///")
		parent = os.path.dirname(database_path)
		if parent:
			os.makedirs(parent, exist_ok=True)

	return create_engine(database_url, **options)


def _ensure_schema_compatibility():
	if _engine is None:
		return

	with _engine.begin() as connection:
		business_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(businesses)").fetchall()}
		if "owner_name" not in business_columns:
			connection.exec_driver_sql("ALTER TABLE businesses ADD COLUMN owner_name VARCHAR")
		if "owner_phone" not in business_columns:
			connection.exec_driver_sql("ALTER TABLE businesses ADD COLUMN owner_phone VARCHAR")
		if "username" not in business_columns:
			connection.exec_driver_sql("ALTER TABLE businesses ADD COLUMN username VARCHAR")
		if "password_hash" not in business_columns:
			connection.exec_driver_sql("ALTER TABLE businesses ADD COLUMN password_hash VARCHAR")
		if "latitude" not in business_columns:
			connection.exec_driver_sql("ALTER TABLE businesses ADD COLUMN latitude FLOAT")
		if "longitude" not in business_columns:
			connection.exec_driver_sql("ALTER TABLE businesses ADD COLUMN longitude FLOAT")
		if "location_precision" not in business_columns:
			connection.exec_driver_sql("ALTER TABLE businesses ADD COLUMN location_precision VARCHAR")
		order_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(orders)").fetchall()}
		if "stock_deducted" not in order_columns:
			connection.exec_driver_sql("ALTER TABLE orders ADD COLUMN stock_deducted BOOLEAN NOT NULL DEFAULT 0")
		if "customer_session_id" not in order_columns:
			connection.exec_driver_sql("ALTER TABLE orders ADD COLUMN customer_session_id VARCHAR")
		if "customer_address" not in order_columns:
			connection.exec_driver_sql("ALTER TABLE orders ADD COLUMN customer_address VARCHAR")
		if "payment_method" not in order_columns:
			connection.exec_driver_sql("ALTER TABLE orders ADD COLUMN payment_method VARCHAR")
		conversation_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(conversations)").fetchall()}
		if "customer_session_id" not in conversation_columns:
			connection.exec_driver_sql("ALTER TABLE conversations ADD COLUMN customer_session_id VARCHAR")
		if "order_state" not in conversation_columns:
			connection.exec_driver_sql("ALTER TABLE conversations ADD COLUMN order_state JSON")
		product_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(products)").fetchall()}
		if "low_stock_threshold" not in product_columns:
			connection.exec_driver_sql("ALTER TABLE products ADD COLUMN low_stock_threshold INTEGER NOT NULL DEFAULT 2")

		otp_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(otp_verifications)").fetchall()}
		if not otp_columns:
			connection.exec_driver_sql(
				"CREATE TABLE IF NOT EXISTS otp_verifications (id INTEGER PRIMARY KEY AUTOINCREMENT, phone_number VARCHAR UNIQUE NOT NULL, otp_hash VARCHAR NOT NULL, expires_at DATETIME NOT NULL, attempts_used INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5, last_sent_at DATETIME, created_at DATETIME, verified_at DATETIME)"
			)


_engine = None
_session_factory = None
_configured_url = None


def configure_database(database_url=None):
	global _engine, _session_factory, _configured_url

	if database_url is None:
		if _engine is not None:
			return _engine
		database_url = DATABASE_URL

	if _engine is None or _configured_url != database_url:
		_engine = _create_engine(database_url)
		_session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
		_configured_url = database_url
		Base.metadata.create_all(_engine)
		_ensure_schema_compatibility()

	return _engine


def get_session():
	configure_database()
	return _session_factory()


def seed_demo_data():
	"""Compatibility wrapper that seeds the canonical 10-business SmartBiz demo dataset."""
	from app.seed_demo_data import seed_demo_shops
	return seed_demo_shops(index_knowledge=True)
