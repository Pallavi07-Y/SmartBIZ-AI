import enum
import os
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from dotenv import load_dotenv

from app.config import PROJECT_ROOT
from app.utils.time import utc_now_for_database


load_dotenv(PROJECT_ROOT / ".env")
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
	owner_email: Mapped[str | None] = mapped_column(String, unique=True)
	email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))
	username: Mapped[str | None] = mapped_column(String, unique=True)
	password_hash: Mapped[str | None] = mapped_column(String)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database)


class CustomerAccount(Base):
	__tablename__ = "customer_accounts"

	customer_id: Mapped[str] = mapped_column(String, primary_key=True, unique=True)
	name: Mapped[str] = mapped_column(String, nullable=False)
	email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
	email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))
	password_hash: Mapped[str] = mapped_column(String, nullable=False)
	delivery_address: Mapped[str | None] = mapped_column(String, nullable=True)
	latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
	longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database)


class RateLimitEvent(Base):
	__tablename__ = "rate_limit_events"

	id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
	event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
	subject_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database, nullable=False, index=True)


class EmailVerification(Base):
	__tablename__ = "email_verifications"

	email: Mapped[str] = mapped_column(String, primary_key=True)
	code_hash: Mapped[str] = mapped_column(String, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
	expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
	last_sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
	attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


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
	created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database)
	updated_at: Mapped[datetime] = mapped_column(
		DateTime,
		default=utc_now_for_database,
		onupdate=utc_now_for_database,
	)


class Message(Base):
	__tablename__ = "messages"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
	sender: Mapped[MessageSender] = mapped_column(Enum(MessageSender))
	text: Mapped[str] = mapped_column(String, nullable=False)
	timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database)


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
	created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database)


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
	created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database)


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
	created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database)
	updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database, onupdate=utc_now_for_database)


class KnowledgeDocument(Base):
	__tablename__ = "knowledge_documents"

	document_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
	business_id: Mapped[str] = mapped_column(ForeignKey("businesses.business_id"), index=True)
	document_name: Mapped[str] = mapped_column(String, nullable=False)
	document_type: Mapped[str | None] = mapped_column(String, nullable=True)
	source: Mapped[str | None] = mapped_column(String, nullable=True)
	status: Mapped[str] = mapped_column(String, default="PROCESSING", nullable=False)
	chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database)
	updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_for_database, onupdate=utc_now_for_database)


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
		from app.database.migrations import upgrade_database

		upgrade_database(_engine)

	return _engine


def get_session():
	configure_database()
	return _session_factory()


def seed_demo_data():
	"""Compatibility wrapper that seeds the canonical 10-business SmartBiz demo dataset."""
	from app.seed_demo_data import seed_demo_shops
	return seed_demo_shops(index_knowledge=True)
