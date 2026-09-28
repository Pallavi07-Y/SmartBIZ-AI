import enum
import os
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from dotenv import load_dotenv


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
	category: Mapped[str | None] = mapped_column(String)
	address: Mapped[str | None] = mapped_column(String)
	owner_phone: Mapped[str | None] = mapped_column(String)
	username: Mapped[str | None] = mapped_column(String, unique=True)
	password_hash: Mapped[str | None] = mapped_column(String)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Conversation(Base):
	__tablename__ = "conversations"

	id: Mapped[int] = mapped_column(Integer, primary_key=True)
	business_id: Mapped[str] = mapped_column(ForeignKey("businesses.business_id"))
	customer_phone: Mapped[str | None] = mapped_column(String)
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


class Order(Base):
	__tablename__ = "orders"

	id: Mapped[str] = mapped_column(
		String,
		primary_key=True,
		default=lambda: str(uuid.uuid4()),
	)
	business_id: Mapped[str] = mapped_column(ForeignKey("businesses.business_id"))
	customer_phone: Mapped[str] = mapped_column(String, nullable=False)
	items: Mapped[list] = mapped_column(JSON, nullable=False)
	status: Mapped[str] = mapped_column(String, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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

	return _engine


def get_session():
	configure_database()
	return _session_factory()
