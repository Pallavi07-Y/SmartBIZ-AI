import json
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from app.agent.tools import AgentTools
from app.agent.ai_agent import AIAgent
from app.business.business_manager import BusinessController
from app.business.analytics import get_business_metrics
from app.business.catalog import BusinessCatalog
from app.business.register_business import BusinessManager
from app.business.search_business import BusinessSearch
from app.database import (
    Business,
    OtpVerification,
    Conversation,
    ConversationStatus,
    KnowledgeDocument,
    Message,
    MessageSender,
    Notification,
    Order,
    Product,
    configure_database,
    get_session,
    seed_demo_data,
)
from app.config import settings
from app.notifications.notification_service import create_notification, get_notifications_for_business as _get_notifications_for_business, get_unread_count as _get_unread_count, mark_all_as_read as _mark_all_as_read, mark_as_read as _mark_as_read
from app.utils.auth import (
    hash_password,
    issue_mock_otp,
    normalize_phone,
    verify_password,
    verify_phone_otp,
)


configure_database()


def get_escalated_conversations_for_business(business_id):
    if not business_id:
        return []

    session = get_session()
    try:
        return (
            session.query(Conversation)
            .filter(
                Conversation.business_id == business_id,
                Conversation.status == ConversationStatus.ESCALATED,
            )
            .order_by(Conversation.updated_at.desc())
            .all()
        )
    finally:
        session.close()


def save_shopkeeper_reply(business_id, conversation_id, reply_text):
    reply = (reply_text or "").strip()
    if not reply:
        raise ValueError("Reply cannot be empty.")

    session = get_session()
    try:
        conversation = (
            session.query(Conversation)
            .filter(
                Conversation.id == conversation_id,
                Conversation.business_id == business_id,
            )
            .first()
        )

        if conversation is None:
            raise ValueError("Conversation not found for this business.")

        conversation.status = ConversationStatus.KEEPER_HANDLING
        conversation.updated_at = datetime.utcnow()

        message = Message(
            conversation_id=conversation.id,
            sender=MessageSender.KEEPER,
            text=reply,
        )
        session.add(message)
        session.commit()
        return message
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_orders_for_business(business_id):
    if not business_id:
        return []

    session = get_session()
    try:
        orders = (
            session.query(Order)
            .filter(Order.business_id == business_id)
            .order_by(Order.created_at.desc())
            .all()
        )
        return [
            {
                "order_id": order.id,
                "business_id": order.business_id,
                "customer_name": next((item.get("customer_name") for item in (order.items or []) if isinstance(item, dict)), None),
                "phone": order.customer_phone,
                "product_name": next((item.get("product_name") for item in (order.items or []) if isinstance(item, dict)), None),
                "quantity": next((item.get("quantity", 1) for item in (order.items or []) if isinstance(item, dict)), 1),
                "price": next((item.get("price", 0) for item in (order.items or []) if isinstance(item, dict)), 0),
                "total": next((item.get("price", 0) * item.get("quantity", 1) for item in (order.items or []) if isinstance(item, dict)), 0),
                "status": str(order.status or "NEW").upper(),
                "created_at": order.created_at,
            }
            for order in orders
        ]
    finally:
        session.close()


def get_customer_business(business_id):
    if not business_id:
        return None

    session = get_session()
    try:
        business = session.get(Business, business_id)
        if business is None:
            return None
        return {
            "business_id": business.business_id,
            "name": business.name,
            "category": business.category,
            "address": business.address,
            "owner_phone": business.owner_phone,
            "username": business.username,
            "created_at": business.created_at,
        }
    finally:
        session.close()


def get_customer_products(business_id, query=None, category=None, limit=None):
    if not business_id:
        return []

    session = get_session()
    try:
        query_filter = session.query(Product).filter(
            Product.business_id == business_id,
            Product.is_active.is_(True),
        )

        if category:
            query_filter = query_filter.filter(Product.category.ilike(f"%{category}%"))

        if query:
            search_term = (query or "").strip()
            if search_term:
                query_filter = query_filter.filter(
                    (Product.product_name.ilike(f"%{search_term}%"))
                    | (Product.brand.ilike(f"%{search_term}%"))
                    | (Product.category.ilike(f"%{search_term}%"))
                    | (Product.description.ilike(f"%{search_term}%"))
                )

        products = query_filter.order_by(Product.created_at.desc()).all()
        rows = []
        for product in products:
            price = product.price
            availability = product.availability or ("Out of Stock" if (product.stock_quantity or 0) <= 0 else "In Stock")
            rows.append({
                "id": product.id,
                "business_id": product.business_id,
                "product_name": product.product_name,
                "category": product.category,
                "brand": product.brand,
                "description": product.description,
                "price": price,
                "price_text": f"₹{price:,}" if price is not None else None,
                "stock_quantity": product.stock_quantity or 0,
                "availability": availability,
                "ram_gb": product.ram_gb,
                "storage_gb": product.storage_gb,
                "processor": product.processor,
                "is_active": product.is_active,
                "created_at": product.created_at,
            })

        if limit is not None:
            rows = rows[:limit]
        return rows
    finally:
        session.close()


def get_customer_orders(customer_session_id):
    if not customer_session_id:
        return []

    session = get_session()
    try:
        orders = (
            session.query(Order)
            .filter(Order.customer_session_id == customer_session_id)
            .order_by(Order.created_at.desc())
            .all()
        )
        business_ids = {order.business_id for order in orders}
        businesses = {
            business.business_id: business.name
            for business in session.query(Business).filter(Business.business_id.in_(business_ids)).all()
        } if business_ids else {}
        rows = []
        for order in orders:
            items = order.items if isinstance(order.items, list) else []
            total = sum(
                int(item.get("price", 0) or 0) * int(item.get("quantity", 1) or 1)
                for item in items if isinstance(item, dict)
            )
            rows.append({
                "order_id": order.id,
                "business_id": order.business_id,
                "shop_name": businesses.get(order.business_id, "Shop"),
                "items": items,
                "total": total,
                "status": str(order.status or "NEW").upper(),
                "customer_address": order.customer_address,
                "created_at": order.created_at,
            })
        return rows
    finally:
        session.close()


def get_customer_conversations(business_id, customer_session_id):
    if not business_id or not customer_session_id:
        return []

    session = get_session()
    try:
        conversations = (
            session.query(Conversation)
            .filter(
                Conversation.business_id == business_id,
                Conversation.customer_session_id == customer_session_id,
            )
            .order_by(Conversation.updated_at.desc())
            .all()
        )
        result = []
        for conversation in conversations:
            first_message = (
                session.query(Message)
                .filter(
                    Message.conversation_id == conversation.id,
                    Message.sender == MessageSender.CUSTOMER,
                )
                .order_by(Message.timestamp.asc())
                .first()
            )
            latest_message = (
                session.query(Message)
                .filter(Message.conversation_id == conversation.id)
                .order_by(Message.timestamp.desc())
                .first()
            )
            result.append({
                "id": conversation.id,
                "title": (first_message.text[:48] if first_message else "New conversation"),
                "latest_message": latest_message.text if latest_message else "No messages yet",
                "status": conversation.status.value if conversation.status else "AI_HANDLING",
                "updated_at": conversation.updated_at,
            })
        return result
    finally:
        session.close()


def create_customer_conversation(business_id, customer_session_id, customer_phone=None):
    if not business_id or not customer_session_id:
        raise ValueError("An active shop and customer session are required.")

    session = get_session()
    try:
        if session.get(Business, business_id) is None:
            raise ValueError("Shop not found.")
        conversation = Conversation(
            business_id=business_id,
            customer_session_id=customer_session_id,
            customer_phone=customer_phone,
            status=ConversationStatus.AI_HANDLING,
        )
        session.add(conversation)
        session.commit()
        return conversation.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_customer_conversation_messages(business_id, customer_session_id, conversation_id):
    if not business_id or not customer_session_id or conversation_id is None:
        return []

    session = get_session()
    try:
        conversation = (
            session.query(Conversation)
            .filter(
                Conversation.id == conversation_id,
                Conversation.business_id == business_id,
                Conversation.customer_session_id == customer_session_id,
            )
            .first()
        )
        if conversation is None:
            return []
        return [
            {
                "role": "user" if message.sender == MessageSender.CUSTOMER else "assistant" if message.sender == MessageSender.AI else "shopkeeper",
                "text": message.text,
                "timestamp": message.timestamp,
            }
            for message in session.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.timestamp.asc())
            .all()
        ]
    finally:
        session.close()


def get_notifications_for_business(business_id, unread_only=False, limit=None):
    return _get_notifications_for_business(
        business_id,
        unread_only=unread_only,
        limit=limit,
    )


def mark_notification_as_read(business_id, notification_id):
    return _mark_as_read(business_id, notification_id)


def mark_all_notifications_read(business_id):
    return _mark_all_as_read(business_id)


def set_conversation_status(business_id, conversation_id, status):
    normalized_status = status.value if hasattr(status, "value") else (status or "").strip().upper()
    if not normalized_status:
        raise ValueError("Status is required.")

    session = get_session()
    try:
        conversation = (
            session.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.business_id == business_id)
            .first()
        )
        if conversation is None:
            raise ValueError("Conversation not found for this business.")
        conversation.status = normalized_status if isinstance(normalized_status, ConversationStatus) else ConversationStatus(normalized_status)
        session.commit()
        return conversation
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_order_status(business_id, order_id, status):
    normalized_status = (status or "").strip().upper()
    if not normalized_status:
        raise ValueError("Status is required.")
    allowed_statuses = {"NEW", "CONFIRMED", "PROCESSING", "READY", "COMPLETED", "CANCELLED"}
    if normalized_status not in allowed_statuses:
        raise ValueError("Unsupported order status.")

    session = get_session()
    low_stock_products = []
    try:
        order = (
            session.query(Order)
            .filter(Order.id == order_id, Order.business_id == business_id)
            .first()
        )
        if order is None:
            raise ValueError("Order not found for this business.")

        previous_status = order.status
        if normalized_status == "CONFIRMED" and not order.stock_deducted:
            requested = {}
            all_products_found = True
            for item in order.items if isinstance(order.items, list) else []:
                if not isinstance(item, dict):
                    continue
                product_name = str(item.get("product_name") or "").strip()
                quantity = max(0, int(item.get("quantity", 1) or 1))
                product = (
                    session.query(Product)
                    .filter(
                        Product.business_id == business_id,
                        Product.product_name.ilike(product_name),
                        Product.is_active.is_(True),
                    )
                    .with_for_update()
                    .first()
                ) if product_name else None
                if product is None:
                    all_products_found = False
                    break
                requested[product.id] = (product, requested.get(product.id, (product, 0))[1] + quantity)

            for product, quantity in requested.values():
                if product.stock_quantity < quantity:
                    raise ValueError(f"Insufficient stock for {product.product_name}.")

            if all_products_found and requested:
                for product, quantity in requested.values():
                    product.stock_quantity -= quantity
                    product.availability = "Out of Stock" if product.stock_quantity <= 0 else "In Stock"
                    if product.stock_quantity <= product.low_stock_threshold:
                        low_stock_products.append((product.id, product.product_name, product.stock_quantity))
                order.stock_deducted = True

        order.status = normalized_status
        session.commit()
        create_notification(
            business_id,
            "ORDER_STATUS",
            f"Order Updated: {normalized_status}",
            f"Order {order_id}\nStatus changed from {previous_status or 'NEW'} to {normalized_status}",
            reference_type="ORDER",
            reference_id=order_id,
        )
        for product_id, product_name, stock_quantity in low_stock_products:
            notification_type = "LOW_STOCK" if stock_quantity else "OUT_OF_STOCK"
            create_notification(
                business_id,
                notification_type,
                f"Low stock: {product_name}" if stock_quantity else f"Out of stock: {product_name}",
                f"{product_name} has {stock_quantity} unit(s) remaining.",
                reference_type="PRODUCT",
                reference_id=str(product_id),
            )
        return {
            "order_id": order.id,
            "business_id": order.business_id,
            "status": order.status,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _apply_dashboard_theme():
    st.markdown(
        """
        <style>
            @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');

            :root {
                --bg: #F1F5F2;
                --surface: #FFFFFF;
                --surface-soft: #E7EFEB;
                --sidebar-bg: #142D29;
                --card-bg: #FFFFFF;
                --primary: #176B5B;
                --primary-hover: #105448;
                --secondary: #B96F4C;
                --accent: #3C8A73;
                --accent-soft: #E4F1EC;
                --text-primary: #172522;
                --text-secondary: #52635D;
                --muted: #74827D;
                --border: #D8E3DD;
                --success: #26734D;
                --warning: #A56414;
                --error: #B54743;
                --escalation: #A63B35;
                --soft-peach: #F8EEE8;
                --soft-green: #E8F3EC;
                --soft-rose: #F9EAE8;
                --shadow: rgba(22, 52, 43, 0.08);
            }

            .stApp {
                background: var(--bg);
                color: var(--text-primary);
                font-family: "DM Sans", "Segoe UI", sans-serif;
            }

            .stApp h1,
            .stApp h2,
            .stApp h3,
            .stApp h4,
            .brand-title,
            .auth-headline,
            .hero-title,
            .step-title {
                font-family: "Manrope", "DM Sans", sans-serif;
                color: var(--text-primary);
                letter-spacing: 0;
            }

            [data-testid="stAppViewContainer"],
            [data-testid="stMain"] {
                background: var(--bg);
                color: var(--text-primary);
            }

            [data-testid="stMainBlockContainer"] {
                max-width: 1440px;
                padding: 2.25rem clamp(1.25rem, 4vw, 3.5rem) 3.5rem;
            }

            [data-testid="stHeader"] {
                background: rgba(241, 245, 242, 0.94);
            }

            [data-testid="stSidebar"] {
                background: var(--sidebar-bg);
                border-right: 1px solid #294740;
            }

            [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
            [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
            [data-testid="stSidebar"] label,
            [data-testid="stSidebar"] p {
                color: #E2ECE7;
            }

            .brand-header {
                display: flex;
                align-items: center;
                gap: 0.8rem;
                padding: 0.85rem 0.8rem 1rem;
                margin-bottom: 0.8rem;
                border-bottom: 1px solid #31534A;
            }

            .brand-icon {
                width: 42px;
                height: 42px;
                border-radius: 12px;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #C9825C;
                color: white;
                font-size: 1.3rem;
                box-shadow: 0 8px 18px rgba(7, 20, 17, 0.22);
            }

            .brand-title {
                font-weight: 700;
                font-size: 1.2rem;
                color: #F6FAF8;
            }

            .brand-subtitle {
                display: block;
                font-size: 0.72rem;
                color: #A9C1B8;
                letter-spacing: 0.08em;
                text-transform: uppercase;
            }

            .nav-pill {
                padding: 0.7rem 0.8rem;
                border-radius: 10px;
                margin: 0.25rem 0;
                color: #DFEAE5;
                font-weight: 600;
                background: transparent;
            }

            .nav-pill.active {
                background: #244940;
                color: #FFFFFF;
            }

            [data-testid="stSidebar"] [data-testid="stRadio"] > label {
                color: #A9C1B8;
                font-size: 0.72rem;
                font-weight: 700;
                letter-spacing: 0.09em;
                text-transform: uppercase;
            }

            [data-testid="stSidebar"] [role="radiogroup"] > label {
                padding: 0.58rem 0.72rem;
                margin: 0.16rem 0;
                border: 1px solid transparent;
                border-radius: 9px;
                color: #DFEAE5;
                transition: background 160ms ease, border-color 160ms ease;
            }

            [data-testid="stSidebar"] [role="radiogroup"] > label:hover {
                background: #1B3A33;
                border-color: #31534A;
            }

            [data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) {
                background: #244940;
                border-color: #39675A;
                box-shadow: inset 3px 0 #D08A61;
                color: #FFFFFF;
            }

            [data-testid="stSidebar"] [data-testid="stButton"] button {
                color: #F5FAF7;
                border-color: #41655B;
                background: #1B3A33;
                box-shadow: none;
            }

            [data-testid="stSidebar"] [data-testid="stButton"] button:hover {
                background: #285247;
                border-color: #719689;
                color: #FFFFFF;
            }

            .section-heading {
                margin-top: 1.35rem;
                margin-bottom: 0.8rem;
                color: var(--text-primary);
                font-family: "Manrope", "DM Sans", sans-serif;
                font-size: 1.02rem;
                font-weight: 800;
                letter-spacing: 0;
            }

            .hero-intro {
                margin-bottom: 2rem;
            }

            .auth-shell {
                min-height: auto;
                display: flex;
                align-items: flex-start;
                justify-content: center;
                padding: 0.75rem 0 2rem;
            }

            .auth-inner {
                width: min(100%, 460px);
                margin: 0 auto;
            }

            .auth-brand {
                text-align: center;
                margin-bottom: 1.5rem;
            }

            .auth-brand h1 {
                color: var(--primary);
                font-size: 2.1rem;
                font-weight: 800;
                margin: 0;
                letter-spacing: 0;
            }

            .auth-brand h2 {
                color: var(--text-secondary);
                font-size: 1rem;
                font-weight: 600;
                margin-top: 0.35rem;
            }

            .auth-card {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 16px;
                box-shadow: 0 18px 45px rgba(22, 52, 43, 0.1);
                padding: 2rem 1.65rem;
            }

            .auth-headline {
                font-size: 1.65rem;
                font-weight: 800;
                color: var(--text-primary);
                margin: 0 0 0.3rem;
            }

            .auth-subtitle {
                color: var(--text-secondary);
                font-size: 0.96rem;
                margin-bottom: 1.25rem;
            }

            .auth-card a {
                color: #8C4E31 !important;
            }

            .auth-form-label {
                display: block;
                font-size: 0.8rem;
                font-weight: 700;
                color: var(--text-primary);
                margin-bottom: 0.45rem;
            }

            .auth-input {
                width: 100%;
                padding: 0.8rem 0.9rem;
                border-radius: 12px;
                border: 1px solid var(--border);
                background: #FBFCFB;
                color: var(--text-primary);
                margin-bottom: 0.9rem;
            }

            .primary-btn {
                background: var(--primary);
                color: #FFFFFF;
                border: none;
                border-radius: 9px;
                padding: 0.8rem 1rem;
                font-weight: 700;
                cursor: pointer;
                transition: background 0.2s ease;
            }

            .primary-btn:hover {
                background: var(--primary-hover);
            }

            .secondary-btn {
                background: #E8F1EC;
                color: #1B4438;
                border: none;
                border-radius: 12px;
                padding: 0.8rem 1rem;
                font-weight: 700;
                cursor: pointer;
            }

            .secondary-btn:hover {
                background: #D6E5DD;
            }

            .ghost-btn {
                background: #FFFFFF;
                color: var(--primary);
                border: 1px solid var(--primary);
                border-radius: 12px;
                padding: 0.8rem 1rem;
                font-weight: 700;
                cursor: pointer;
            }

            .ghost-btn:hover {
                background: var(--accent-soft);
            }

            .danger-btn {
                background: var(--error);
                color: #FFFFFF;
                border: none;
                border-radius: 12px;
                padding: 0.7rem 1rem;
                font-weight: 700;
            }

            .micro-copy {
                color: var(--text-secondary);
                font-size: 0.8rem;
                margin-top: 0.5rem;
            }

            .hero-title {
                font-size: 2.5rem;
                font-weight: 800;
                color: var(--text-primary);
                margin: 0;
                line-height: 1.18;
            }

            .hero-subtitle {
                color: var(--text-secondary);
                font-size: 1rem;
                margin-top: 0.45rem;
                line-height: 1.55;
            }

            .metric-card {
                background: var(--card-bg);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 1.15rem 1.2rem;
                height: 100%;
                box-shadow: 0 5px 15px rgba(22, 52, 43, 0.045);
                border-top: 3px solid var(--accent);
            }

            .metric-label {
                color: var(--text-secondary);
                font-size: 0.75rem;
                font-weight: 700;
                letter-spacing: 0.06em;
                text-transform: uppercase;
            }

            .metric-value {
                font-size: 1.8rem;
                font-weight: 800;
                color: var(--text-primary);
                margin: 0.35rem 0 0;
            }

            .metric-accent {
                display: inline-block;
                margin-top: 0.45rem;
                padding: 0.3rem 0.5rem;
                border-radius: 999px;
                background: var(--accent-soft);
                color: #185D4E;
                font-size: 0.7rem;
                font-weight: 700;
            }

            .metric-card.warning {
                background: var(--soft-peach);
                border-color: #E8CFC1;
                border-top-color: var(--secondary);
            }

            .stButton > button {
                background: #FFFFFF;
                color: var(--primary);
                border: 1px solid #AFC8BC;
                border-radius: 9px;
                font-weight: 700;
                padding: 0.62rem 1.05rem;
                box-shadow: 0 2px 5px rgba(22, 52, 43, 0.05);
                transition: transform 150ms ease, box-shadow 150ms ease, background 150ms ease;
            }

            .stButton > button:hover {
                background: var(--accent-soft);
                color: #104D41;
                border-color: #7FA998;
                box-shadow: 0 5px 12px rgba(22, 52, 43, 0.1);
                transform: translateY(-1px);
            }

            .stButton > button[kind="primary"],
            [data-testid="stFormSubmitButton"] button,
            [data-testid="stForm"] button[kind="primary"] {
                background: var(--primary) !important;
                color: #FFFFFF !important;
                border: 1px solid var(--primary) !important;
                box-shadow: 0 5px 12px rgba(23, 107, 91, 0.16);
            }

            .stButton > button[kind="primary"]:hover,
            [data-testid="stFormSubmitButton"] button:hover {
                background: var(--primary-hover) !important;
                border-color: var(--primary-hover) !important;
                color: #FFFFFF !important;
            }

            .stButton > button:disabled,
            [data-testid="stFormSubmitButton"] button:disabled {
                background: #E8EEEA !important;
                border-color: #D8E3DD !important;
                color: #82918A !important;
                box-shadow: none;
                transform: none;
            }

            .step-card {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 1.35rem 1.2rem;
                box-shadow: 0 7px 20px var(--shadow);
                height: 100%;
            }

            .step-number {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 2.1rem;
                height: 2.1rem;
                border-radius: 50%;
                background: var(--primary);
                color: white;
                font-weight: 800;
                margin-bottom: 0.8rem;
            }

            .step-title {
                font-size: 1.08rem;
                font-weight: 800;
                color: var(--text-primary);
                margin: 0.2rem 0 0.4rem;
            }

            .step-body {
                font-size: 0.92rem;
                color: var(--text-secondary);
                line-height: 1.55;
            }

            div[data-testid="stForm"] {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 1.2rem;
                box-shadow: 0 5px 16px rgba(22, 52, 43, 0.045);
            }

            .stTextInput > div > div > input,
            .stTextArea > div > div > textarea {
                background: #FBFCFB !important;
                color: var(--text-primary) !important;
                border: 1px solid var(--border) !important;
                border-radius: 9px !important;
                min-height: 2.65rem;
                box-shadow: inset 0 1px 2px rgba(22, 52, 43, 0.025);
            }

            .stTextInput label,
            .stCheckbox label,
            .stSelectbox label,
            .stTextArea label {
                color: var(--text-primary) !important;
                font-weight: 600 !important;
            }

            [data-testid="stSelectbox"] [data-baseweb="select"] > div,
            [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
            [data-testid="stDateInput"] input,
            [data-testid="stTimeInput"] input,
            [data-testid="stNumberInput"] input {
                background: #FBFCFB !important;
                color: var(--text-primary) !important;
                border-color: var(--border) !important;
                border-radius: 9px !important;
            }

            [data-testid="stTextInput"] input:focus,
            [data-testid="stTextArea"] textarea:focus,
            [data-testid="stNumberInput"] input:focus,
            [data-testid="stDateInput"] input:focus,
            [data-testid="stTimeInput"] input:focus {
                border-color: var(--primary) !important;
                box-shadow: 0 0 0 3px rgba(23, 107, 91, 0.12) !important;
            }

            [data-testid="stFileUploaderDropzone"] {
                background: #F7FAF8;
                border: 1px dashed #91B3A4;
                border-radius: 10px;
            }

            [data-testid="stFileUploaderDropzone"]:hover {
                border-color: var(--primary);
                background: var(--accent-soft);
            }

            [data-testid="stCheckbox"] label,
            [data-testid="stRadio"] label {
                color: var(--text-primary);
            }

            [data-testid="stSlider"] [data-testid="stThumbValue"] {
                color: var(--primary);
            }

            div[data-testid="stFormSubmitButton"] button {
                background: var(--primary) !important;
                color: #ffffff !important;
                border: 1px solid var(--primary) !important;
                border-radius: 9px !important;
                font-weight: 700 !important;
                padding: 0.7rem 1.1rem !important;
            }

            .stDataFrame {
                border: 1px solid var(--border);
                border-radius: 10px;
                overflow: hidden;
                background: var(--surface);
                box-shadow: 0 4px 14px rgba(22, 52, 43, 0.045);
            }

            [data-testid="stDataEditor"] {
                border: 1px solid var(--border);
                border-radius: 10px;
                overflow: hidden;
                background: var(--surface);
            }

            [data-testid="stMetric"] {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 11px;
                padding: 1rem 1.1rem;
                box-shadow: 0 5px 15px rgba(22, 52, 43, 0.045);
            }

            [data-testid="stMetricLabel"] {
                color: var(--text-secondary);
                font-size: 0.76rem;
                font-weight: 700;
            }

            [data-testid="stMetricValue"] {
                color: var(--text-primary);
                font-family: "Manrope", "DM Sans", sans-serif;
                font-weight: 800;
            }

            [data-testid="stAlert"] {
                border-radius: 10px;
                border: 1px solid var(--border);
                box-shadow: 0 3px 10px rgba(22, 52, 43, 0.04);
            }

            [data-testid="stExpander"] {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 10px;
            }

            [class*="st-key-customer-shop-card-"],
            [class*="st-key-customer-product-card-"],
            [class*="st-key-customer-order-card-"] {
                background: var(--surface) !important;
                border: 1px solid var(--border) !important;
                border-radius: 12px !important;
                box-shadow: 0 7px 20px rgba(22, 52, 43, 0.07) !important;
            }

            [data-testid="stTabs"] [role="tablist"] {
                gap: 0.35rem;
                border-bottom: 1px solid var(--border);
            }

            [data-testid="stTabs"] button[role="tab"] {
                color: var(--text-secondary);
                font-weight: 700;
            }

            [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
                color: var(--primary);
                border-bottom-color: var(--primary);
            }

            .chat-bubble-user {
                background: #EEF2EF;
                color: var(--text-primary);
                border: 1px solid #DEE7E1;
                border-radius: 12px 12px 12px 4px;
                padding: 0.9rem 1rem;
                margin: 0.4rem 0;
            }

            .chat-bubble-ai {
                background: #E5F1EC;
                color: #173F35;
                border: 1px solid #CDE2D8;
                border-radius: 12px 12px 4px 12px;
                padding: 0.9rem 1rem;
                margin: 0.4rem 0;
            }

            .chat-bubble-keeper {
                background: #F7EEE8;
                color: #653F2F;
                border: 1px solid #ECD9CC;
                border-radius: 12px 12px 12px 4px;
                padding: 0.9rem 1rem;
                margin: 0.4rem 0;
            }

            a {
                color: var(--primary);
            }

            a:hover {
                color: var(--primary-hover);
            }

            ::selection {
                background: #CDE2D8;
                color: #142D29;
            }

            @media (max-width: 768px) {
                [data-testid="stMainBlockContainer"] {
                    padding: 1.2rem 1rem 2.25rem;
                }

                .hero-title {
                    font-size: 1.9rem;
                }

                .auth-shell {
                    min-height: auto;
                    padding: 1rem 0;
                }

                .auth-card {
                    padding: 1.4rem 1.1rem;
                }

                .metric-card {
                    padding: 0.95rem;
                }
            }

            @media (max-width: 520px) {
                [data-testid="stMainBlockContainer"] {
                    padding-left: 0.8rem;
                    padding-right: 0.8rem;
                }

                .hero-title {
                    font-size: 1.65rem;
                }

                .auth-inner {
                    width: 100%;
                }

                .metric-value {
                    font-size: 1.5rem;
                }

                .stDataFrame,
                [data-testid="stDataEditor"] {
                    max-width: 100%;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _items_to_text(items):
    if not items:
        return ""

    if isinstance(items, str):
        return items

    try:
        parsed_items = json.loads(items) if isinstance(items, str) else items
    except (TypeError, ValueError):
        parsed_items = items

    if not isinstance(parsed_items, list):
        return str(parsed_items)

    text_items = []
    for item in parsed_items:
        if isinstance(item, dict):
            name = item.get("product_name") or item.get("name") or "Item"
            quantity = item.get("quantity", 1)
            text_items.append(f"{name} x{quantity}")
        else:
            text_items.append(str(item))

    return ", ".join(text_items)


def _show_signup_page():
    st.markdown("<div class='auth-shell'><div class='auth-inner'>", unsafe_allow_html=True)
    st.markdown("<div class='auth-brand'><h1>WELCOME TO SMARTBIZ AI</h1><h2>Business Automation System</h2></div>", unsafe_allow_html=True)

    if "signup_step" not in st.session_state:
        st.session_state.signup_step = "phone"
    if "signup_phone" not in st.session_state:
        st.session_state.signup_phone = ""
    if "signup_otp_attempts" not in st.session_state:
        st.session_state.signup_otp_attempts = 0
    if "signup_otp_expires_at" not in st.session_state:
        st.session_state.signup_otp_expires_at = None
    if "signup_resend_cooldown_until" not in st.session_state:
        st.session_state.signup_resend_cooldown_until = None

    step = st.session_state.signup_step
    st.markdown("<div class='auth-card'>", unsafe_allow_html=True)

    if step == "phone":
        st.markdown("<div class='auth-headline'>Create Shopkeeper Account</div>", unsafe_allow_html=True)
        st.markdown("<div class='auth-subtitle'>Step 1 of 4</div>", unsafe_allow_html=True)
        with st.form("shop_phone_form"):
            phone = st.text_input("Phone Number", value=st.session_state.signup_phone, placeholder="+91 Enter phone number")
            submitted = st.form_submit_button("SEND OTP", use_container_width=True)
        if submitted:
            normalized_phone = normalize_phone(phone)
            if not normalized_phone:
                st.error("Please enter a valid phone number.")
            else:
                session = get_session()
                try:
                    existing = session.query(Business).filter(Business.owner_phone == normalized_phone).first()
                    if existing is not None:
                        st.error("This phone number is already registered. Please use another phone number or login with your existing business ID.")
                    else:
                        otp_result = issue_mock_otp(normalized_phone)
                        st.session_state.signup_phone = normalized_phone
                        st.session_state.signup_otp_attempts = 0
                        st.session_state.signup_otp_expires_at = otp_result["expires_at"]
                        st.session_state.signup_mock_otp = otp_result["otp_code"]
                        st.session_state.signup_step = "otp"
                        st.session_state.signup_resend_cooldown_until = datetime.utcnow() + timedelta(seconds=30)
                        st.success("OTP sent successfully.")
                        st.info(f"Mock OTP for development: {otp_result['otp_code']}")
                        st.rerun()
                except ValueError as exc:
                    st.warning(str(exc))
                finally:
                    session.close()

    elif step == "otp":
        st.markdown("<div class='auth-headline'>Verify OTP</div>", unsafe_allow_html=True)
        st.markdown("<div class='auth-subtitle'>Step 2 of 4</div>", unsafe_allow_html=True)
        expires_at = st.session_state.get("signup_otp_expires_at")
        if expires_at is not None:
            seconds_left = max(0, int((expires_at - datetime.utcnow()).total_seconds()))
            st.markdown(f"<div class='micro-copy'>OTP expires in {seconds_left} seconds</div>", unsafe_allow_html=True)
        st.info(f"Demo OTP: {st.session_state.get('signup_mock_otp', '****')}")
        with st.form("shop_otp_form"):
            otp = st.text_input("Enter OTP", max_chars=6, placeholder="_ _ _ _ _ _")
            submitted = st.form_submit_button("VERIFY OTP", use_container_width=True)

        if submitted:
            if not otp:
                st.error("Please enter the OTP.")
            else:
                if expires_at is not None and expires_at <= datetime.utcnow():
                    st.error("This OTP has expired. Please request a new one.")
                    st.session_state.signup_step = "phone"
                    st.rerun()
                else:
                    if verify_phone_otp(st.session_state.signup_phone, otp):
                        st.session_state.signup_step = "details"
                        st.session_state.signup_otp_attempts = 0
                        st.success("Phone verified successfully.")
                        st.rerun()
                    else:
                        st.session_state.signup_otp_attempts += 1
                        if st.session_state.signup_otp_attempts >= 5:
                            st.error("Too many failed attempts. Please request a new OTP.")
                            st.session_state.signup_step = "phone"
                            st.rerun()
                        else:
                            remaining = 5 - st.session_state.signup_otp_attempts
                            st.error(f"Invalid OTP. {remaining} attempt(s) remaining.")

        cooldown_until = st.session_state.get("signup_resend_cooldown_until")
        cooldown_active = bool(cooldown_until and datetime.utcnow() < cooldown_until)
        if cooldown_active:
            st.caption(f"You can request another OTP in {int((cooldown_until - datetime.utcnow()).total_seconds())} seconds.")
        if st.button("RESEND OTP", use_container_width=True, disabled=cooldown_active):
            try:
                otp_result = issue_mock_otp(st.session_state.signup_phone)
                st.session_state.signup_otp_expires_at = otp_result["expires_at"]
                st.session_state.signup_mock_otp = otp_result["otp_code"]
                st.session_state.signup_otp_attempts = 0
                st.session_state.signup_resend_cooldown_until = datetime.utcnow() + timedelta(seconds=30)
                st.success("A new OTP has been sent.")
                st.info(f"Mock OTP for development: {otp_result['otp_code']}")
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))

    elif step == "details":
        st.markdown("<div class='auth-headline'>Business Details</div>", unsafe_allow_html=True)
        st.markdown("<div class='auth-subtitle'>Step 3 of 4</div>", unsafe_allow_html=True)
        with st.form("shop_details_form"):
            owner_name = st.text_input("Owner Name")
            shop_name = st.text_input("Shop Name")
            category = st.text_input("Business Category")
            address = st.text_area("Shop Address")
            submitted = st.form_submit_button("CONTINUE", use_container_width=True)
        if submitted:
            if not all([owner_name.strip(), shop_name.strip(), category.strip(), address.strip()]):
                st.error("Please complete all business details.")
            else:
                st.session_state.signup_owner_name = owner_name.strip()
                st.session_state.signup_shop_name = shop_name.strip()
                st.session_state.signup_category = category.strip()
                st.session_state.signup_address = address.strip()
                st.session_state.signup_step = "password"
                st.rerun()

    elif step == "password":
        st.markdown("<div class='auth-headline'>Create Password</div>", unsafe_allow_html=True)
        st.markdown("<div class='auth-subtitle'>Step 4 of 4</div>", unsafe_allow_html=True)
        with st.form("shop_password_form"):
            password = st.text_input("Create Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            submitted = st.form_submit_button("CREATE BUSINESS ACCOUNT", use_container_width=True)
        if submitted:
            if not password or not confirm_password:
                st.error("Please enter and confirm your password.")
            elif len(password) < 8:
                st.error("Password must be at least 8 characters long.")
            elif password != confirm_password:
                st.error("Passwords do not match.")
            else:
                manager = BusinessManager()
                session = get_session()
                try:
                    verification = (
                        session.query(OtpVerification)
                        .filter(OtpVerification.phone_number == st.session_state.signup_phone)
                        .first()
                    )
                    if (
                        verification is None
                        or verification.verified_at is None
                        or verification.expires_at <= datetime.utcnow()
                    ):
                        st.error("Please verify your phone number again before creating the account.")
                        st.session_state.signup_step = "phone"
                        st.rerun()
                    business = manager.register_business(
                        name=st.session_state.signup_shop_name,
                        category=st.session_state.signup_category,
                        address=st.session_state.signup_address,
                        owner_phone=st.session_state.signup_phone,
                        username=f"shop-{normalize_phone(st.session_state.signup_phone)}",
                        password_hash=hash_password(password),
                        owner_name=st.session_state.signup_owner_name,
                    )
                    st.session_state.role = "shopkeeper"
                    st.session_state.auth_mode = "signup"
                    st.session_state.signup_success_business_id = business["business_id"]
                    st.session_state.signup_step = "success"
                    st.success("Business Account Created")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    st.error("Unable to create your business account right now.")
                finally:
                    session.close()

    elif step == "success":
        st.markdown("<div class='auth-card' style='text-align:center;'>", unsafe_allow_html=True)
        st.markdown("<div style='font-size:2rem; color:#2E7D32; margin-bottom:0.5rem;'>✓</div>", unsafe_allow_html=True)
        st.markdown("<div class='auth-headline'>Account Created</div>", unsafe_allow_html=True)
        business_id = st.session_state.get("signup_success_business_id") or "SB-XXXXXX"
        st.markdown(f"<div class='auth-subtitle'>Your Business ID:</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:1.8rem; font-weight:800; color:#2D2D2D; margin-bottom:0.5rem;'>{business_id}</div>", unsafe_allow_html=True)
        st.markdown("<div class='micro-copy'>Save this ID. You will use it to login.</div>", unsafe_allow_html=True)
        if st.button("GO TO LOGIN", use_container_width=True):
            st.session_state.signup_step = "phone"
            st.session_state.auth_mode = "login"
            st.session_state.remembered_login = {
                "business_id": business_id,
                "password": "",
                "remember": False,
            }
            st.session_state.pop("signup_success_business_id", None)
            st.session_state.pop("signup_mock_otp", None)
            st.session_state.pop("signup_otp_expires_at", None)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div></div>", unsafe_allow_html=True)

    if step != "success":
        if st.button("Back to login", use_container_width=True):
            st.session_state.auth_mode = "login"
            st.session_state.signup_step = "phone"
            st.session_state.pop("signup_mock_otp", None)
            st.session_state.pop("signup_success_business_id", None)
            st.rerun()


def _load_saved_login():
    default = {"business_id": "", "password": "", "remember": False}
    saved = st.session_state.get("remembered_login", default)
    if not isinstance(saved, dict):
        return default
    return {
        "business_id": saved.get("business_id", ""),
        "password": saved.get("password", ""),
        "remember": bool(saved.get("remember", False)),
    }


def _show_role_selection_page():
    st.markdown(
        "<div class='hero-intro'><div class='hero-title'>WELCOME TO SMARTBIZ AI</div>"
        "<div class='hero-subtitle'>Choose how you want to work with your business operations.</div></div>",
        unsafe_allow_html=True,
    )

    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_center:
        st.markdown("<div class='section-heading'>Select your role</div>", unsafe_allow_html=True)
        role_cols = st.columns(2)
        with role_cols[0]:
            if st.button("🏪 Shopkeeper", type="primary", use_container_width=True, key="role_shopkeeper"):
                st.session_state.role = "shopkeeper"
                st.session_state.auth_mode = "login"
                st.rerun()
        with role_cols[1]:
            if st.button("👤 Customer", type="primary", use_container_width=True, key="role_customer"):
                st.session_state.role = "customer"
                st.session_state.customer_business_id = None
                st.rerun()

def _show_login_page():
    saved_login = _load_saved_login()

    st.markdown("<div class='auth-shell'><div class='auth-inner'>", unsafe_allow_html=True)
    st.markdown("<div class='auth-brand'><h1>WELCOME TO SMARTBIZ AI</h1><h2>Business Automation System</h2></div>", unsafe_allow_html=True)
    st.markdown("<div class='auth-card'>", unsafe_allow_html=True)
    st.markdown("<div class='auth-headline'>Welcome Back!</div>", unsafe_allow_html=True)
    st.markdown("<div class='auth-subtitle'>Shopkeeper Login</div>", unsafe_allow_html=True)

    with st.form("login_form"):
        business_id = st.text_input("Business ID", value=saved_login.get("business_id", ""), key="login_business_id")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("LOGIN", use_container_width=True)

    if submitted:
        if not business_id or not password:
            st.error("Please enter both Business ID and password.")
        else:
            session = get_session()
            try:
                business = session.query(Business).filter(Business.business_id == business_id.strip()).first()
                if business and business.password_hash and verify_password(password, business.password_hash):
                    st.session_state.business_id = business.business_id
                    st.session_state.role = "shopkeeper"
                    st.session_state.auth_mode = "login"
                    st.session_state.remembered_login = {
                        "business_id": business.business_id,
                        "password": "",
                        "remember": False,
                    }
                    st.rerun()
                else:
                    st.error("Invalid Business ID or password.")
            finally:
                session.close()

    st.markdown("<div class='micro-copy' style='text-align: right; margin: 0.5rem 0 1rem;'><a href='#' style='color:#6B4F3A; text-decoration:none; font-weight:600;'>Forgot Password?</a></div>", unsafe_allow_html=True)
    st.markdown("<div class='micro-copy' style='text-align: center; margin-bottom: 0.8rem;'>New Shopkeeper?</div>", unsafe_allow_html=True)
    if st.button("CREATE ACCOUNT", use_container_width=True):
        st.session_state.auth_mode = "signup"
        st.rerun()

    st.markdown("<div style='display:flex;align-items:center;gap:0.75rem; margin: 1.25rem 0 1rem;'><div style='flex:1; height:1px; background:#E5E7EB;'></div><div style='color:#6B7280; font-weight:600;'>OR</div><div style='flex:1; height:1px; background:#E5E7EB;'></div></div>", unsafe_allow_html=True)
    st.markdown("<div class='micro-copy' style='text-align:center; margin-bottom: 0.8rem;'>Are you a customer?</div>", unsafe_allow_html=True)
    if st.button("VISIT A SHOP", use_container_width=True):
        st.session_state.role = "customer"
        st.session_state.customer_business_id = None
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div></div>", unsafe_allow_html=True)


def _logout():
    st.session_state.clear()
    st.rerun()


def _render_orders_page():
    business_id = st.session_state.get("business_id")
    metrics = get_business_metrics(business_id)
    st.markdown("<div class='section-heading'>Order Overview</div>", unsafe_allow_html=True)
    columns = st.columns(5)
    for column, label, value in zip(
        columns,
        ["Total Orders", "Today", "This Month", "Revenue", "Average Order"],
        [metrics["total_orders"], metrics["today_orders"], metrics["month_orders"], f"₹{metrics['revenue']:,}", f"₹{metrics['average_order_value']:,}"],
    ):
        column.metric(label, value)

    status_options = ["All", "NEW", "CONFIRMED", "PROCESSING", "READY", "COMPLETED", "CANCELLED"]
    selected_status = st.selectbox("Filter orders by status", status_options)
    recent_orders = metrics["orders"]
    filtered_orders = [
        order for order in recent_orders
        if selected_status == "All" or order["status"] == selected_status
    ]
    if not filtered_orders:
        st.info("No orders yet." if not recent_orders else "No orders match this status.")
        return

    table_data = []
    for order in filtered_orders:
        first_item = order["items"][0] if order["items"] else {}
        table_data.append({
            "Order ID": order["order_id"],
            "Customer": order["customer_name"],
            "Phone": order["customer_phone"],
            "Product": order["product_name"],
            "Quantity": order["quantity"],
            "Unit Price": f"₹{first_item.get('price', 0):,}" if first_item else "-",
            "Total": f"₹{order['total']:,}",
            "Requested Delivery": "Not specified",
            "Status": order["status"],
            "Order Time": order["created_at"].strftime("%d %b %Y, %I:%M %p") if order["created_at"] else "-",
        })
    st.dataframe(table_data, use_container_width=True, hide_index=True)

    st.markdown("<div class='section-heading'>Update Order Status</div>", unsafe_allow_html=True)
    for index, order in enumerate(filtered_orders):
        with st.form(f"order_status_form_{index}"):
            control_columns = st.columns([3, 2, 1])
            control_columns[0].write(f"**{order['order_id']}** · {order['customer_name']} · ₹{order['total']:,}")
            current_status = order["status"] if order["status"] in status_options else "NEW"
            new_status = control_columns[1].selectbox(
                "Status",
                status_options[1:],
                index=status_options[1:].index(current_status),
                key=f"order_status_{order['order_id']}",
            )
            submitted = control_columns[2].form_submit_button("Update")
        if submitted and new_status != order["status"]:
            try:
                update_order_status(business_id, order["order_id"], new_status)
                st.success("Order status updated.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _render_products_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to manage products.")
        return

    controller = BusinessController()
    catalog = BusinessCatalog(business_id)
    products = controller.list_products(business_id, include_inactive=True)

    st.markdown("<div class='section-heading'>Product Management</div>", unsafe_allow_html=True)
    col_1, col_2, col_3 = st.columns(3)
    with col_1:
        st.markdown(f"<div class='metric-card'><div class='metric-label'>Products</div><div class='metric-value'>{len(products)}</div><div class='metric-accent'>Catalog</div></div>", unsafe_allow_html=True)
    with col_2:
        low_stock = sum(1 for product in products if (product.get("stock_quantity") or 0) <= 2)
        st.markdown(f"<div class='metric-card warning'><div class='metric-label'>Low Stock</div><div class='metric-value'>{low_stock}</div><div class='metric-accent'>Needs restock</div></div>", unsafe_allow_html=True)
    with col_3:
        active_products = sum(1 for product in products if product.get("is_active") is not False)
        st.markdown(f"<div class='metric-card'><div class='metric-label'>Active</div><div class='metric-value'>{active_products}</div><div class='metric-accent'>Live</div></div>", unsafe_allow_html=True)

    search_text = st.text_input("Search products", placeholder="Name, brand, category, or SKU")
    category_options = ["All"] + sorted({product.get("category") for product in products if product.get("category")})
    selected_category = st.selectbox("Category", category_options)
    visible_products = [
        product for product in products
        if (not search_text or search_text.casefold() in " ".join(str(product.get(key) or "") for key in ("product_name", "brand", "category", "sku")).casefold())
        and (selected_category == "All" or product.get("category") == selected_category)
    ]

    st.markdown("<div class='section-heading'>Add Product</div>", unsafe_allow_html=True)
    with st.form("add_product_form"):
        c1, c2 = st.columns(2)
        with c1:
            product_name = st.text_input("Product name")
            category = st.text_input("Category")
            brand = st.text_input("Brand")
            sku = st.text_input("SKU")
        with c2:
            price = st.number_input("Price (₹)", min_value=0, value=0, step=100)
            stock_quantity = st.number_input("Stock quantity", min_value=0, value=0, step=1)
            low_stock_threshold = st.number_input("Low-stock threshold", min_value=0, value=2, step=1)
            availability = st.text_input("Availability", placeholder="In Stock")
            description = st.text_area("Description", height=80)
        submitted = st.form_submit_button("Save Product")

    if submitted:
        if not product_name.strip():
            st.warning("Product name is required.")
        else:
            controller.add_product(
                business_id=business_id,
                product_name=product_name.strip(),
                price=int(price) if price is not None else None,
                stock_quantity=int(stock_quantity),
                category=category.strip() or None,
                brand=brand.strip() or None,
                sku=sku.strip() or None,
                description=description.strip() or None,
                availability=availability.strip() or None,
                low_stock_threshold=int(low_stock_threshold),
            )
            catalog.sync_products_to_rag()
            st.success("Product saved and synced to the AI catalog.")
            st.rerun()

    st.markdown("<div class='section-heading'>Inventory</div>", unsafe_allow_html=True)
    for product in visible_products:
        with st.container():
            st.markdown(
                f"<div style='background:#ffffff;border:1px solid #E5E7EB;border-radius:12px;padding:1rem;margin-bottom:0.8rem;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;'>"
                f"<div><strong>{product.get('product_name') or 'Unnamed product'}</strong><br>"
                f"<span style='color:#6B7280;'>{product.get('brand') or 'No brand'} • {product.get('category') or 'General'} • ₹{product.get('price') or 0:,}</span></div>"
                f"<div style='text-align:right;'><div style='font-weight:700;'>Stock: {product.get('stock_quantity') or 0}</div><div style='color:#6B7280;'>{product.get('availability') or 'Availability unknown'}</div></div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
            with st.form(f"update_stock_{product.get('id')}"):
                c1, c2, c3 = st.columns([2, 2, 1])
                with c1:
                    updated_price = st.number_input("Price", min_value=0, value=int(product.get("price") or 0), key=f"price_{product.get('id')}")
                with c2:
                    updated_stock = st.number_input("Stock", min_value=0, value=int(product.get("stock_quantity") or 0), key=f"stock_{product.get('id')}")
                    updated_threshold = st.number_input("Low-stock threshold", min_value=0, value=int(product.get("low_stock_threshold") or 0), key=f"threshold_{product.get('id')}")
                with c3:
                    st.write("")
                    st.write("")
                    save_click = st.form_submit_button("Update")
                if save_click:
                    controller.update_product(
                        business_id,
                        product["id"],
                        price=int(updated_price),
                        stock_quantity=int(updated_stock),
                        low_stock_threshold=int(updated_threshold),
                    )
                    catalog.sync_products_to_rag()
                    st.success(f"Updated {product.get('product_name')}.")
                    st.rerun()
            if st.button(f"Deactivate product", key=f"deactivate_{product.get('id')}"):
                controller.deactivate_product(business_id, product["id"])
                catalog.sync_products_to_rag()
                st.success(f"Deactivated {product.get('product_name')}.")
                st.rerun()


def _render_knowledge_base_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to manage the knowledge base.")
        return

    controller = BusinessController()
    documents = controller.list_knowledge_documents(business_id)

    st.markdown("<div class='section-heading'>Knowledge Base</div>", unsafe_allow_html=True)
    st.markdown("<div class='hero-subtitle'>Upload business documents, product notes, or service FAQs to keep the AI context fresh.</div>", unsafe_allow_html=True)

    with st.form("knowledge_upload_form"):
        file = st.file_uploader("Upload document", type=["txt", "pdf", "csv", "docx"])
        submitted = st.form_submit_button("Upload and index")

    if submitted and file is not None:
        temp_dir = Path("data/temp_uploads")
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / file.name
        temp_path.write_bytes(file.getvalue())
        try:
            result = controller.add_document(business_id, str(temp_path))
            st.success(f"Indexed {file.name} successfully.")
            st.caption(f"Documents added: {result.get('documents_added')}")
            st.session_state.last_doc_result = result
            st.rerun()
        except Exception as exc:
            st.error(f"Unable to index the document: {exc}")
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    if not documents:
        st.info("No knowledge-base documents have been uploaded yet.")
        return

    st.markdown("<div class='section-heading'>Indexed Documents</div>", unsafe_allow_html=True)
    for doc in documents:
        with st.container():
            st.markdown(
                f"<div style='background:#ffffff;border:1px solid #E5E7EB;border-radius:12px;padding:1rem;margin-bottom:0.8rem;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;'>"
                f"<div><strong>{doc.get('document_name')}</strong><br><span style='color:#6B7280;'>{doc.get('document_type') or 'unknown'} • {doc.get('status') or 'Pending'}</span></div>"
                f"<div style='color:#6B7280;'>Chunks: {doc.get('chunk_count') or 0}</div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


def _render_profile_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view your business profile.")
        return

    session = get_session()
    try:
        business = session.get(Business, business_id)
        if business is None:
            st.error("Business profile not found.")
            return

        st.title("Settings")
        st.subheader("Shop Profile")
        profile_columns = st.columns(2)
        profile_columns[0].text_input("Business ID", value=business.business_id, disabled=True)
        profile_columns[1].text_input("Shop Name", value=business.name or "", disabled=True)
        profile_columns = st.columns(2)
        profile_columns[0].text_input("Owner Name", value=business.owner_name or "", disabled=True)
        profile_columns[1].text_input("Phone", value=business.owner_phone or "", disabled=True)
        profile_columns = st.columns(2)
        profile_columns[0].text_input("Category", value=business.category or "", disabled=True)
        profile_columns[1].text_input("Address", value=business.address or "", disabled=True)

        st.subheader("Account")
        with st.form("change_password_form"):
            current_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            update_password = st.form_submit_button("Change Password")
        if update_password:
            if not current_password or not new_password or not confirm_password:
                st.error("Complete all password fields.")
            elif not verify_password(current_password, business.password_hash or ""):
                st.error("Current password is incorrect.")
            elif len(new_password) < 8:
                st.error("New password must contain at least 8 characters.")
            elif new_password != confirm_password:
                st.error("New passwords do not match.")
            else:
                business.password_hash = hash_password(new_password)
                session.commit()
                st.success("Password changed.")

        if st.button("Log Out", type="secondary"):
            _logout()

        st.subheader("AI and Knowledge Base")
        document_count = session.query(KnowledgeDocument).filter(KnowledgeDocument.business_id == business_id).count()
        latest_document = (
            session.query(KnowledgeDocument)
            .filter(KnowledgeDocument.business_id == business_id)
            .order_by(KnowledgeDocument.updated_at.desc())
            .first()
        )
        st.write(f"AI Assistant: Configured model {settings.MODEL}")
        st.write(f"Knowledge documents: {document_count}")
        st.write(f"Last knowledge update: {latest_document.updated_at if latest_document else 'No documents indexed'}")
        st.caption("SmartBiz AI · Version 1.0")
    except Exception:
        st.error("Unable to load business profile.")
    finally:
        session.close()


def _render_analytics_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view analytics.")
        return

    today = datetime.utcnow().date()
    selected_period = st.selectbox("Reporting period", ["All time", "Today", "Last 7 Days", "This Month"])
    date_from = None
    date_to = None
    if selected_period == "Today":
        date_from = date_to = today
    elif selected_period == "Last 7 Days":
        date_from = today - timedelta(days=6)
        date_to = today
    elif selected_period == "This Month":
        date_from = today.replace(day=1)
        date_to = today
    metrics = get_business_metrics(business_id, date_from=date_from, date_to=date_to)
    st.title("Analytics & Reports")
    st.caption(f"Database-calculated performance for this shop · {selected_period}")
    columns = st.columns(4)
    columns[0].metric("Revenue", f"₹{metrics['revenue']:,}")
    columns[1].metric("Average order", f"₹{metrics['average_order_value']:,}")
    columns[2].metric("Orders", metrics["total_orders"])
    columns[3].metric("Customers", metrics["customer_count"])

    st.subheader("Orders and revenue · Last 7 days")
    daily = metrics["daily"]
    if any(item["orders"] or item["revenue"] for item in daily):
        st.line_chart({
            "Orders": [item["orders"] for item in daily],
            "Revenue (₹)": [item["revenue"] for item in daily],
        })
    else:
        st.info("No order activity in the last 7 days.")

    report_columns = st.columns(2)
    with report_columns[0]:
        st.subheader("Order status")
        st.dataframe([
            {"Status": "Pending", "Orders": metrics["pending_orders"]},
            {"Status": "Completed", "Orders": metrics["completed_orders"]},
            {"Status": "Cancelled", "Orders": metrics["cancelled_orders"]},
        ], use_container_width=True, hide_index=True)
        st.subheader("Monthly activity")
        st.bar_chart({
            "Orders": [item["orders"] for item in metrics["monthly"]],
            "Revenue (₹)": [item["revenue"] for item in metrics["monthly"]],
        })
    with report_columns[1]:
        st.subheader("Orders by product")
        if metrics["orders_by_product"]:
            st.dataframe(metrics["orders_by_product"], use_container_width=True, hide_index=True)
        else:
            st.info("No product orders to report yet.")
        st.subheader("Orders by category")
        if metrics["orders_by_category"]:
            st.dataframe(metrics["orders_by_category"], use_container_width=True, hide_index=True)
        else:
            st.info("No category activity to report yet.")
    st.caption(
        f"Inventory: {metrics['total_units']} units · {metrics['low_stock_products']} low stock · "
        f"{metrics['out_of_stock_products']} out of stock · {metrics['escalated_count']} escalated conversations"
    )


def _render_inventory_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view inventory.")
        return

    metrics = get_business_metrics(business_id)
    products = BusinessController().list_products(business_id, include_inactive=True)
    st.title("Inventory")
    columns = st.columns(5)
    columns[0].metric("Active products", metrics["total_products"])
    columns[1].metric("Units", metrics["total_units"])
    columns[2].metric("Low stock", metrics["low_stock_products"])
    columns[3].metric("Out of stock", metrics["out_of_stock_products"])
    columns[4].metric("Stock value", f"₹{metrics['stock_value']:,}")
    if not products:
        st.info("No products have been added yet.")
        return

    st.subheader("Stock by product")
    st.dataframe([
        {
            "Product": product.get("product_name"),
            "Category": product.get("category") or "Uncategorized",
            "Brand": product.get("brand") or "-",
            "Price": f"₹{product.get('price') or 0:,}",
            "Current stock": product.get("stock_quantity") or 0,
            "Threshold": product.get("low_stock_threshold", 2),
            "Status": (
                "INACTIVE" if not product.get("is_active") else
                "OUT OF STOCK" if (product.get("stock_quantity") or 0) <= 0 else
                "LOW STOCK" if (product.get("stock_quantity") or 0) <= (product.get("low_stock_threshold") or 0) else
                "IN STOCK"
            ),
            "Last updated": product.get("updated_at").strftime("%d %b %Y %I:%M %p") if product.get("updated_at") else "-",
        }
        for product in products
    ], use_container_width=True, hide_index=True)

    st.subheader("Adjust stock")
    controller = BusinessController()
    for product in products:
        if not product.get("is_active"):
            continue
        with st.form(f"inventory_adjust_{product['id']}"):
            columns = st.columns([3, 2, 1])
            columns[0].write(f"**{product['product_name']}** · currently {product.get('stock_quantity') or 0} units")
            new_stock = columns[1].number_input(
                "Set stock count",
                min_value=0,
                value=max(0, int(product.get("stock_quantity") or 0)),
                step=1,
                key=f"inventory_stock_{product['id']}",
            )
            submitted = columns[2].form_submit_button("Save")
        if submitted:
            controller.update_product(
                business_id,
                product["id"],
                stock_quantity=int(new_stock),
                availability="Out of Stock" if new_stock == 0 else "In Stock",
            )
            st.rerun()


def _render_notifications_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view notifications.")
        return

    notifications = get_notifications_for_business(business_id)
    st.title("Notifications")
    unread_count = sum(not notification.get("is_read") for notification in notifications)
    st.caption(f"{unread_count} unread")
    if unread_count and st.button("Mark all as read"):
        mark_all_notifications_read(business_id)
        st.rerun()
    if not notifications:
        st.info("You're all caught up.")
        return

    notification_types = ["All"] + sorted({notification.get("type") or "OTHER" for notification in notifications})
    selected_type = st.selectbox("Category", notification_types)
    if selected_type != "All":
        notifications = [notification for notification in notifications if notification.get("type") == selected_type]

    route_by_reference = {"ORDER": "🛒 Orders", "CONVERSATION": "💬 Customer Chats", "PRODUCT": "📦 Products"}
    for index, notification in enumerate(notifications):
        color = "#D7F4E6" if notification.get("is_read") else "#FDECEC"
        columns = st.columns([5, 1, 1])
        with columns[0]:
            st.markdown(
                f"<div style='background:{color};border:1px solid #E5E7EB;border-radius:10px;padding:0.9rem;margin-bottom:0.6rem;'>"
                f"<strong>{notification.get('title')}</strong><br><span style='color:#6B7280;'>{notification.get('message')}</span></div>",
                unsafe_allow_html=True,
            )
        if not notification.get("is_read") and columns[1].button("Mark read", key=f"notification_read_{index}"):
            mark_notification_as_read(business_id, notification["notification_id"])
            st.rerun()
        destination = route_by_reference.get(notification.get("reference_type"))
        if destination and columns[2].button("Open", key=f"notification_open_{index}"):
            st.session_state.requested_dashboard_page = destination
            if not notification.get("is_read"):
                mark_notification_as_read(business_id, notification["notification_id"])
            st.rerun()


def _render_ai_business_insights_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view AI insights.")
        return

    metrics = get_business_metrics(business_id)
    st.title("AI Business Insights")
    st.caption("Live figures below are calculated from SQLite. AI explanations are generated only when requested.")
    columns = st.columns(4)
    columns[0].metric("Revenue", f"₹{metrics['revenue']:,}")
    columns[1].metric("Orders", metrics["total_orders"])
    columns[2].metric("Products", metrics["total_products"])
    columns[3].metric("Low stock", metrics["low_stock_products"])

    insight_cache = st.session_state.get("business_insights_by_id", {})
    if st.button("Generate AI Insights", type="primary"):
        try:
            with st.spinner("Analyzing verified business data and relevant knowledge..."):
                insights = AIAgent().generate_business_insights(business_id, metrics)
            insight_cache[business_id] = insights
            st.session_state.business_insights_by_id = insight_cache
        except Exception:
            st.warning("AI insights are temporarily unavailable. Your live business metrics are still available above.")

    insights = st.session_state.get("business_insights_by_id", {}).get(business_id)
    if not insights:
        st.info("Generate insights when you need an AI summary of current business activity.")
        return

    st.subheader("Business Summary")
    st.write(insights["summary"])
    insight_sections = [
        ("Order Insights", "order_insights"),
        ("Inventory Insights", "inventory_insights"),
        ("Customer Insights", "customer_insights"),
        ("Operational Issues", "operational_issues"),
        ("Suggested Actions", "suggested_actions"),
    ]
    section_columns = st.columns(2)
    for index, (heading, key) in enumerate(insight_sections):
        with section_columns[index % 2]:
            st.markdown(f"**{heading}**")
            if insights[key]:
                for item in insights[key]:
                    st.write(f"- {item}")
            else:
                st.caption("Insufficient data to generate this insight.")


def _render_customer_business_selection(customer_session_id):
    st.title("Discover Shops")
    st.caption("Explore shops, products and get assistance from AI.")
    st.subheader("Available Shops")

    session = get_session()
    try:
        businesses = session.query(Business).order_by(Business.name.asc()).all()
        business_rows = [
            {"business_id": business.business_id, "name": business.name, "category": business.category, "address": business.address}
            for business in businesses
        ]
    finally:
        session.close()

    if not business_rows:
        st.info("No shops available.")
        return

    categories = ["All"] + sorted({row["category"] for row in business_rows if row["category"]})
    filters = st.columns([2, 1])
    search_text = filters[0].text_input("Search shops", placeholder="Shop name or category")
    selected_category = filters[1].selectbox("Category", categories)
    matching = [
        business for business in business_rows
        if (selected_category == "All" or business["category"] == selected_category)
        and (not search_text or search_text.casefold() in f"{business['name']} {business['category'] or ''}".casefold())
    ]
    if not matching:
        st.info("No shops available for this search.")
        return

    for start in range(0, len(matching), 2):
        columns = st.columns(2, gap="large")
        for column, business in zip(columns, matching[start:start + 2]):
            business_id = business["business_id"]
            products = get_customer_products(business_id, limit=100)
            metrics = get_business_metrics(business_id)
            ordered_names = {item["product_name"] for item in metrics["top_products"]}
            highlights = [
                f"{product['product_name']} · Frequently ordered"
                if product["product_name"] in ordered_names
                else f"{product['product_name']} · Currently available"
                for product in products[:2]
            ]
            with column.container(border=True, key=f"customer-shop-card-{business_id}"):
                st.subheader(business["name"])
                st.caption(f"{business['category'] or 'General'} · {business['address'] or 'Address not provided'}")
                st.caption(f"{len(products)} listed product(s) · Available shop")
                if highlights:
                    st.markdown("**AI Shop Highlights**")
                    for highlight in highlights:
                        st.write(f"- {highlight}")
                else:
                    st.caption("No products are currently listed.")
                if st.button("Visit Shop", key=f"visit_shop_{business_id}", use_container_width=True):
                    st.session_state.customer_business_id = business_id
                    st.session_state.customer_pending_question = None
                    st.session_state.requested_customer_page = "Dashboard"
                    st.rerun()


def _render_customer_shop_page(customer_session_id, business):
    business_id = business["business_id"]
    st.title(business["name"])
    st.caption(f"{business.get('category') or 'Shop'} · {business.get('address') or 'Address not provided'}")

    all_products = get_customer_products(business_id, limit=500)
    if not all_products:
        st.info("No products available in this shop.")
        return

    available_prices = [int(product["price"]) for product in all_products if product.get("price") is not None]
    filter_columns = st.columns([2, 1, 1, 2])
    search_term = filter_columns[0].text_input("Search products", key=f"shop_search_{business_id}")
    categories = ["All"] + sorted({product["category"] for product in all_products if product.get("category")})
    category = filter_columns[1].selectbox("Category", categories, key=f"shop_category_{business_id}")
    availability = filter_columns[2].selectbox("Availability", ["All", "In Stock", "Out of Stock"], key=f"shop_availability_{business_id}")
    price_range = None
    if available_prices and min(available_prices) < max(available_prices):
        price_range = filter_columns[3].slider(
            "Price range (₹)",
            min_value=min(available_prices),
            max_value=max(available_prices),
            value=(min(available_prices), max(available_prices)),
            key=f"shop_price_{business_id}",
        )

    products = [
        product for product in all_products
        if (not search_term or search_term.casefold() in " ".join(str(product.get(key) or "") for key in ("product_name", "brand", "category", "description")).casefold())
        and (category == "All" or product.get("category") == category)
        and (availability == "All" or ("In Stock" if product.get("stock_quantity", 0) > 0 else "Out of Stock") == availability)
        and (price_range is None or product.get("price") is None or price_range[0] <= product["price"] <= price_range[1])
    ]

    product_metrics = get_business_metrics(business_id)
    sales = {row["product_name"]: row for row in product_metrics["top_products"]}
    st.subheader("Products")
    if not products:
        st.info("No products available in this shop matching those filters.")
    tools = AgentTools.__new__(AgentTools)
    cart = tools.get_cart(customer_session_id, business_id)
    columns = st.columns(2, gap="large")
    for index, product in enumerate(products):
        with columns[index % 2].container(border=True, key=f"customer-product-card-{business_id}-{product['id']}"):
            st.markdown(f"### {product['product_name']}")
            st.caption(f"{product.get('brand') or 'Brand not listed'} · {product.get('category') or 'General'}")
            if product.get("price") is not None:
                st.markdown(f"**₹{product['price']:,}**")
            st.write(f"{'In Stock' if product.get('stock_quantity', 0) > 0 else 'Out of Stock'} · {product.get('stock_quantity', 0)} unit(s)")
            if product.get("description"):
                st.write(product["description"])
            order_data = sales.get(product["product_name"])
            if order_data and order_data["orders"] > 0:
                st.caption(f"Popular · {order_data['orders']} recorded order(s)")
            elif product.get("stock_quantity", 0) > 0:
                st.caption("AI Highlight · Currently available")
            with st.expander("View Details"):
                st.write(f"SKU: {product.get('sku') or 'Not specified'}")
                st.write(f"RAM: {product.get('ram_gb') or 'Not specified'}")
                st.write(f"Storage: {product.get('storage_gb') or 'Not specified'}")
                st.write(f"Processor: {product.get('processor') or 'Not specified'}")
            quantity = st.number_input(
                "Quantity",
                min_value=1,
                max_value=max(1, int(product.get("stock_quantity") or 0)),
                value=1,
                step=1,
                key=f"customer_product_quantity_{business_id}_{product['id']}",
                disabled=product.get("stock_quantity", 0) <= 0,
            )
            buttons = st.columns(2)
            if buttons[0].button("Ask AI", key=f"ask_product_{business_id}_{product['id']}"):
                st.session_state.customer_pending_question = f"Tell me about {product['product_name']}, including its specifications and any relevant shop policies."
                st.session_state.requested_customer_page = "AI Assistant"
                st.rerun()
            if buttons[1].button("Add to Cart", key=f"add_cart_{business_id}_{product['id']}", disabled=product.get("stock_quantity", 0) <= 0):
                result = tools.add_to_cart(customer_session_id, business_id, product["product_name"], int(quantity))
                if result.get("error"):
                    st.error(result["error"])
                else:
                    st.success("Added to cart.")
                    st.rerun()

    st.subheader("Cart")
    if not cart:
        st.info("Your cart is empty.")
    else:
        st.dataframe(cart, use_container_width=True, hide_index=True)
        with st.form(f"customer_checkout_{business_id}"):
            customer_name = st.text_input("Your name", value=st.session_state.get("customer_name", ""))
            customer_phone = st.text_input("Phone number", value=st.session_state.get("customer_phone", ""))
            customer_address = st.text_area("Delivery address", value=st.session_state.get("customer_address", ""))
            confirm_order = st.checkbox("I confirm these order details.")
            submit_order = st.form_submit_button("Place Order", type="primary")
        if submit_order:
            if not customer_name.strip() or not customer_phone.strip() or not customer_address.strip():
                st.warning("Enter your name, phone number, and delivery address to place an order.")
            elif not confirm_order:
                st.warning("Confirm your order details before placing the order.")
            else:
                st.session_state.customer_name = customer_name.strip()
                st.session_state.customer_phone = customer_phone.strip()
                st.session_state.customer_address = customer_address.strip()
                failures = []
                created_orders = []
                for item in cart:
                    order = tools.create_order(
                        business_id,
                        customer_name.strip(),
                        customer_phone.strip(),
                        item["product_name"],
                        quantity=item.get("quantity", 1),
                        customer_session_id=customer_session_id,
                        customer_address=customer_address.strip(),
                    )
                    if order.get("error"):
                        failures.append(f"{item['product_name']}: {order['error']}")
                    else:
                        created_orders.append(order)
                        tools.remove_from_cart(customer_session_id, item["product_name"], business_id)
                if failures:
                    for failure in failures:
                        st.error(failure)
                    if created_orders:
                        st.warning(f"{len(created_orders)} order(s) were placed; remove those items before retrying.")
                else:
                    tools.clear_cart(customer_session_id, business_id)
                    st.success(f"Order placed. {len(created_orders)} order(s) added to My Orders.")
                    st.rerun()


def _render_customer_orders_page(customer_session_id):
    st.title("My Orders")
    orders = get_customer_orders(customer_session_id)
    if not orders:
        st.info("No orders yet. Your orders will appear here after you place one.")
        return
    for order in orders:
        with st.container(border=True, key=f"customer-order-card-{order['order_id']}"):
            st.subheader(f"Order {order['order_id']}")
            st.caption(f"{order['shop_name']} · {order['created_at'].strftime('%d %b %Y, %I:%M %p') if order['created_at'] else 'Date unavailable'}")
            for item in order["items"]:
                if isinstance(item, dict):
                    st.write(f"{item.get('product_name', 'Product')} · Qty {item.get('quantity', 1)} · ₹{int(item.get('price', 0) or 0):,}")
            columns = st.columns(3)
            columns[0].metric("Total", f"₹{order['total']:,}")
            columns[1].metric("Status", order["status"])
            columns[2].write(f"Requested delivery: Not specified")
            if order.get("customer_address"):
                st.caption(f"Delivery address: {order['customer_address']}")


def _render_customer_settings(customer_session_id, business):
    st.title("Customer Settings")
    st.subheader("Current Session")
    st.code(customer_session_id[:8] + "..." if len(customer_session_id) > 12 else customer_session_id)
    st.write(f"Current shop: {business['name'] if business else 'None selected'}")
    if business and st.button("Leave Current Shop"):
        st.session_state.customer_business_id = None
        st.session_state.requested_customer_page = "Shops"
        st.rerun()
    st.subheader("Preferences")
    st.caption("Chat history is separated by shop and this browser session.")
    if st.button("Clear Customer Session", type="secondary"):
        for key in list(st.session_state.keys()):
            if (key.startswith("customer_") and key != "customer_page") or key.startswith("active_customer_"):
                st.session_state.pop(key, None)
        st.session_state.requested_customer_page = "Dashboard"
        st.rerun()
    st.caption("SmartBiz AI · Business Automation System")


def _render_customer_assistant(business, customer_session_id):
    st.title("AI Shopping Assistant")
    st.caption(f"Ask about products, current prices, availability, shop policies, and orders · {business['name']}")
    business_id = business["business_id"]
    session = get_session()
    try:
        active_products = session.query(Product).filter(
            Product.business_id == business_id,
            Product.is_active.is_(True),
        ).count()
    finally:
        session.close()
    st.info(
        f"Welcome to {business['name']}, specializing in {business.get('category') or 'local products'}. "
        f"There are {active_products} listed product(s). Ask about current prices, stock, specifications, policies, or orders."
    )

    conversations = get_customer_conversations(business_id, customer_session_id)
    active_by_business = st.session_state.get("active_customer_conversation_by_business", {})
    conversation_id = active_by_business.get(business_id)
    if not any(item["id"] == conversation_id for item in conversations):
        conversation_id = create_customer_conversation(
            business_id,
            customer_session_id,
            st.session_state.get("customer_phone"),
        )
        active_by_business[business_id] = conversation_id
        st.session_state.active_customer_conversation_by_business = active_by_business

    chat_column, history_column = st.columns([2, 1], gap="large")
    with history_column:
        st.subheader("Conversation History")
        if st.button("New Conversation", key=f"new_customer_conversation_{business_id}"):
            conversation_id = create_customer_conversation(
                business_id,
                customer_session_id,
                st.session_state.get("customer_phone"),
            )
            active_by_business[business_id] = conversation_id
            st.session_state.active_customer_conversation_by_business = active_by_business
            st.rerun()
        for item in conversations:
            label = item["title"]
            if st.button(label, key=f"select_customer_conversation_{item['id']}", use_container_width=True):
                active_by_business[business_id] = item["id"]
                st.session_state.active_customer_conversation_by_business = active_by_business
                st.rerun()

    with chat_column:
        for message in get_customer_conversation_messages(business_id, customer_session_id, conversation_id):
            role = "user" if message["role"] == "user" else "assistant"
            with st.chat_message(role):
                if message["role"] == "shopkeeper":
                    st.caption("Shopkeeper")
                st.write(message["text"])
                if message.get("timestamp"):
                    st.caption(message["timestamp"].strftime("%d %b %Y %I:%M %p"))

        pending_question = st.session_state.pop("customer_pending_question", None)
        input_key = f"customer_question_{business_id}_{conversation_id}_{st.session_state.get('customer_chat_turn', 0)}"
        if pending_question:
            st.session_state[input_key] = pending_question
        with st.form(f"customer_chat_form_{business_id}_{conversation_id}"):
            question = st.text_area("Message", key=input_key, placeholder="Ask about a product, price, availability, policy, or order...")
            send = st.form_submit_button("Send", type="primary")
        if send:
            if not question.strip():
                st.warning("Type a message first.")
            else:
                try:
                    agent_key = f"{customer_session_id}:{business_id}:{conversation_id}"
                    agents = st.session_state.get("customer_agents", {})
                    agent = agents.get(agent_key)
                    if agent is None:
                        agent = AIAgent()
                        agents[agent_key] = agent
                        st.session_state.customer_agents = agents
                    with st.spinner("Checking this shop's products and knowledge..."):
                        result = agent.chat(
                            business_id,
                            question.strip(),
                            customer_phone=st.session_state.get("customer_phone"),
                            customer_session_id=customer_session_id,
                            conversation_id=conversation_id,
                            customer_address=st.session_state.get("customer_address"),
                        )
                    if result.get("signal") == "awaiting_human":
                        st.info("Your message was sent to the shopkeeper. They can reply in this conversation.")
                    elif str(result.get("reply", "")).startswith(("Agent Error:", "Sorry, something went wrong")):
                        st.warning("AI Assistant is temporarily unavailable. Product browsing and orders are still available.")
                    st.session_state.customer_chat_turn = st.session_state.get("customer_chat_turn", 0) + 1
                    st.rerun()
                except Exception:
                    st.warning("AI Assistant is temporarily unavailable. Product browsing and orders are still available.")


def _render_customer_interface():
    customer_session_id = st.session_state.get("customer_session_id")
    if not customer_session_id:
        customer_session_id = secrets.token_urlsafe(24)
        st.session_state.customer_session_id = customer_session_id

    business_id = st.session_state.get("customer_business_id")
    business = get_customer_business(business_id) if business_id else None
    customer_pages = ["Dashboard", "AI Assistant", "Shops", "Orders", "Settings"]
    requested_page = st.session_state.pop("requested_customer_page", None)
    if requested_page in customer_pages:
        st.session_state.customer_page = requested_page
    if st.session_state.get("customer_page") not in customer_pages:
        st.session_state.customer_page = "Dashboard"

    st.sidebar.markdown("## SMARTBIZ AI")
    st.sidebar.caption("Business Automation System")
    page = st.sidebar.radio("Customer navigation", customer_pages, key="customer_page")
    if business:
        st.sidebar.divider()
        st.sidebar.markdown(f"**{business['name']}**")
        st.sidebar.caption(business.get("category") or "Shop")
        if st.sidebar.button("Switch Shop", key="customer_switch_shop"):
            st.session_state.customer_business_id = None
            st.session_state.requested_customer_page = "Shops"
            st.rerun()

    if page in {"Dashboard", "Shops"}:
        if page == "Dashboard" and business:
            _render_customer_shop_page(customer_session_id, business)
        else:
            _render_customer_business_selection(customer_session_id)
    elif page == "AI Assistant":
        if business:
            _render_customer_assistant(business, customer_session_id)
        else:
            st.title("AI Shopping Assistant")
            st.info("Choose a shop first to start a shop-specific conversation.")
            _render_customer_business_selection(customer_session_id)
    elif page == "Orders":
        _render_customer_orders_page(customer_session_id)
    elif page == "Settings":
        _render_customer_settings(customer_session_id, business)


def _get_recent_conversations(business_id, limit=10):
    session = get_session()
    try:
        conversations = (
            session.query(Conversation)
            .filter(Conversation.business_id == business_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .all()
        )
        result = []
        for conversation in conversations:
            latest_message = (
                session.query(Message)
                .filter(Message.conversation_id == conversation.id)
                .order_by(Message.timestamp.desc())
                .first()
            )
            result.append(
                {
                    "id": conversation.id,
                    "customer_phone": conversation.customer_phone or "Unknown customer",
                    "status": conversation.status.value if conversation.status else "AI_HANDLING",
                    "updated_at": conversation.updated_at,
                    "latest_message": latest_message.text if latest_message else "No messages yet",
                }
            )
        return result
    finally:
        session.close()


def _render_dashboard_home_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view the dashboard.")
        return

    metrics = get_business_metrics(business_id)
    session = get_session()
    try:
        business = session.query(Business).filter(Business.business_id == business_id).first()
        business_name = business.name if business else "Your shop"
    finally:
        session.close()

    st.title("Shopkeeper Dashboard")
    st.caption("Manage your store, orders, inventory and customers — all in one place.")
    st.caption(f"{business_name} · {datetime.now().strftime('%A, %d %B %Y')}")
    kpis = st.columns(4)
    kpis[0].metric("Total Orders", metrics["total_orders"])
    kpis[1].metric("Total Revenue", f"₹{metrics['revenue']:,}")
    kpis[2].metric("Total Products", metrics["total_products"])
    kpis[3].metric("Low Stock Items", metrics["low_stock_products"])

    left, right = st.columns([1.6, 1], gap="large")
    with left:
        st.subheader("Products")
        if metrics["top_products"]:
            st.dataframe([
                {
                    "Product": product["product_name"],
                    "Ordered": product["quantity"],
                    "Revenue": f"₹{product['revenue']:,}",
                }
                for product in metrics["top_products"]
            ], use_container_width=True, hide_index=True)
        elif metrics["products"]:
            st.caption("No sales history to rank products yet. Current catalog:")
            st.dataframe([
                {
                    "Product": product["product_name"],
                    "Price": f"₹{product['price']:,}",
                    "Stock": product["stock_quantity"],
                    "Availability": product["availability"],
                }
                for product in metrics["products"][:5]
            ], use_container_width=True, hide_index=True)
        else:
            st.info("No products added yet.")
        if st.button("Manage Products", key="home_products"):
            st.session_state.requested_dashboard_page = "📦 Products"
            st.rerun()

        st.subheader("Recent Orders")
        if metrics["recent_orders"]:
            st.dataframe([
                {
                    "Order": order["order_id"],
                    "Customer": order["customer_name"],
                    "Product": order["product_name"],
                    "Qty": order["quantity"],
                    "Total": f"₹{order['total']:,}",
                    "Status": order["status"],
                    "Time": order["created_at"].strftime("%I:%M %p") if order["created_at"] else "-",
                }
                for order in metrics["recent_orders"][:5]
            ], use_container_width=True, hide_index=True)
        else:
            st.info("No orders yet.")
        if st.button("View All Orders", key="home_orders"):
            st.session_state.requested_dashboard_page = "🛒 Orders"
            st.rerun()

        st.subheader("Order and Revenue Trend")
        st.line_chart({
            "Orders": [item["orders"] for item in metrics["daily"]],
            "Revenue (₹)": [item["revenue"] for item in metrics["daily"]],
        })

    with right:
        st.subheader("Notifications")
        notifications = get_notifications_for_business(business_id, limit=5)
        if notifications:
            for index, notification in enumerate(notifications):
                state = "Unread" if not notification["is_read"] else "Read"
                st.markdown(f"**{notification['title']}** · {state}")
                st.caption(notification["message"])
                if index < len(notifications) - 1:
                    st.divider()
        else:
            st.info("You're all caught up.")
        if st.button("View Notifications", key="home_notifications"):
            st.session_state.requested_dashboard_page = "🔔 Notifications"
            st.rerun()

        st.subheader("Customer Chats")
        conversation_rows = _get_recent_conversations(business_id, limit=5)
        if conversation_rows:
            for row in conversation_rows:
                st.markdown(f"**{row['customer_phone']}** · {row['status']}")
                st.caption(row["latest_message"])
        else:
            st.info("No customer conversations yet.")
        if st.button("View All Chats", key="home_chats"):
            st.session_state.requested_dashboard_page = "💬 Customer Chats"
            st.rerun()


def _render_customer_chats_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view customer chats.")
        return

    conversations = _get_recent_conversations(business_id, limit=50)
    if not conversations:
        st.info("No customer conversations yet.")
        return

    st.title("Customer Chats")
    status_filter = st.selectbox("Filter conversations", ["All", "AI_HANDLING", "ESCALATED", "KEEPER_HANDLING", "RESOLVED"])
    visible = [item for item in conversations if status_filter == "All" or item["status"] == status_filter]
    if not visible:
        st.info("No conversations match this status.")
        return
    selected_id = st.selectbox(
        "Conversation",
        [item["id"] for item in visible],
        index=next((i for i, item in enumerate(visible) if item["id"] == st.session_state.get("selected_customer_chat_id")), 0),
        format_func=lambda conversation_id: next(
            f"{item['customer_phone']} · {item['status']} · {item['latest_message'][:55]}"
            for item in visible if item["id"] == conversation_id
        ),
    )
    st.session_state.selected_customer_chat_id = selected_id

    session = get_session()
    try:
        conversation = (
            session.query(Conversation)
            .filter(Conversation.id == selected_id, Conversation.business_id == business_id)
            .first()
        )
        if conversation is None:
            st.error("This conversation is not available for your business.")
            return
        messages = (
            session.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.timestamp.asc())
            .all()
        )
        current_status = conversation.status.value if conversation.status else "AI_HANDLING"
    finally:
        session.close()

    status_columns = st.columns([3, 1])
    with status_columns[0]:
        st.markdown(f"**Customer:** {conversation.customer_phone or 'Unknown'}")
    with st.form(f"conversation_status_{selected_id}"):
        status = st.selectbox(
            "Conversation status",
            ["AI_HANDLING", "ESCALATED", "KEEPER_HANDLING", "RESOLVED"],
            index=["AI_HANDLING", "ESCALATED", "KEEPER_HANDLING", "RESOLVED"].index(current_status),
        )
        update_status = st.form_submit_button("Update Status")
    if update_status and status != current_status:
        set_conversation_status(business_id, selected_id, status)
        st.rerun()

    st.subheader("Conversation")
    if messages:
        for message in messages:
            sender = "Shopkeeper" if message.sender == MessageSender.KEEPER else "Customer" if message.sender == MessageSender.CUSTOMER else "AI"
            st.markdown(f"**{sender}** · {message.timestamp.strftime('%d %b %Y %I:%M %p') if message.timestamp else ''}")
            st.write(message.text)
    else:
        st.info("No messages in this conversation yet.")

    with st.form(f"reply_to_customer_{selected_id}"):
        reply = st.text_area("Reply to customer", placeholder="Write a response...")
        send_reply = st.form_submit_button("Send Reply")
    if send_reply:
        if not reply.strip():
            st.warning("Enter a reply before sending.")
        else:
            try:
                save_shopkeeper_reply(business_id, selected_id, reply)
                st.success("Reply saved and the conversation is now handled by the shopkeeper.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _render_escalated_chats_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view escalated chats.")
        return

    conversations = get_escalated_conversations_for_business(business_id)
    options = []
    session = get_session()
    try:
        for conversation in conversations:
            latest_message = (
                session.query(Message)
                .filter(Message.conversation_id == conversation.id)
                .order_by(Message.timestamp.desc())
                .first()
            )
            preview = latest_message.text if latest_message else "No message yet"
            options.append(
                {
                    "id": conversation.id,
                    "customer_phone": conversation.customer_phone or "Unknown customer",
                    "latest_message": preview,
                    "status": conversation.status.value if conversation.status else ConversationStatus.ESCALATED.value,
                    "updated_at": conversation.updated_at.strftime("%Y-%m-%d %H:%M:%S") if conversation.updated_at else "",
                    "conversation": conversation,
                }
            )
    finally:
        session.close()

    if not options:
        st.markdown("<div style='background:#ffffff;border:1px solid #E5E7EB;border-radius:12px;padding:1rem;'>No escalated conversations 🎉<br><span style='color:#6B7280;'>All customer conversations are currently handled by AI.</span></div>", unsafe_allow_html=True)
        return

    selected_id = st.session_state.get("selected_escalated_chat_id")
    if selected_id is None or not any(option["id"] == selected_id for option in options):
        selected_id = options[0]["id"]
    st.session_state.selected_escalated_chat_id = selected_id

    selected = next(option for option in options if option["id"] == selected_id)

    st.markdown("<div class='section-heading'>Escalated Chats</div>", unsafe_allow_html=True)
    st.markdown("<div style='color:#6B7280; margin-bottom:1rem;'>Customer conversations that require shopkeeper assistance.</div>", unsafe_allow_html=True)

    for option in options:
        st.markdown(
            "<div style='background:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;padding:1rem;margin-bottom:0.8rem;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;gap:1rem;'><div><strong>📱 {option['customer_phone']}</strong></div><div style='background:#FDECEC;color:#B42318;border-radius:999px;padding:0.25rem 0.6rem;font-size:0.72rem;font-weight:700;'>{option['status']}</div></div>"
            f"<div style='color:#6B7280;margin-top:0.6rem;'>{option['latest_message']}</div>"
            f"<div style='color:#6B7280;font-size:0.8rem;margin-top:0.5rem;'>Updated: {option['updated_at']}</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button(f"Open Chat → {option['customer_phone']}", key=f"chat_{option['id']}"):
            st.session_state.selected_escalated_chat_id = option["id"]
            st.rerun()

    st.markdown("<hr style='border-top:1px solid #E5E7EB; margin:1.5rem 0;'>", unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:1.3rem;font-weight:700;margin-bottom:0.5rem;'>Conversation with {selected['customer_phone']}</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='color:#6B7280;'>Status: <span style='color:#B42318;font-weight:700;'>{selected['status']}</span> • Last updated: {selected['updated_at']}</div>", unsafe_allow_html=True)

    session = get_session()
    try:
        conversation = (
            session.query(Conversation)
            .filter(
                Conversation.id == selected["conversation"].id,
                Conversation.business_id == business_id,
            )
            .first()
        )
        if conversation is None:
            st.error("This conversation is not available for your business.")
            return

        messages = (
            session.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.timestamp.asc())
            .all()
        )
    finally:
        session.close()

    for message in messages:
        if message.sender == MessageSender.CUSTOMER:
            sender_name = "Customer"
            bubble_class = "chat-bubble-user"
        elif message.sender == MessageSender.KEEPER:
            sender_name = "Shopkeeper"
            bubble_class = "chat-bubble-keeper"
        else:
            sender_name = "AI"
            bubble_class = "chat-bubble-ai"

        st.markdown(f"<div class='{bubble_class}'><strong>{sender_name}:</strong> {message.text}</div>", unsafe_allow_html=True)

    with st.form("keeper_reply_form"):
        reply = st.text_area("Reply to customer", key=f"keeper_reply_{selected['conversation'].id}", placeholder="Type your response...")
        submitted = st.form_submit_button("Send Reply")

    if submitted:
        if not reply or not reply.strip():
            st.warning("Please enter a reply before sending.")
        else:
            try:
                save_shopkeeper_reply(business_id, selected["conversation"].id, reply)
                st.success("Reply saved and conversation marked as being handled by the shopkeeper.")
                st.session_state.selected_escalated_chat_id = None
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                st.error("Unable to save the reply right now.")


def main():
    st.set_page_config(page_title="SMART BIZ AI", page_icon="🏪", layout="wide")
    _apply_dashboard_theme()

    if "role" not in st.session_state:
        _show_role_selection_page()
        return

    if st.session_state.role == "customer":
        _render_customer_interface()
        return

    if "auth_mode" not in st.session_state:
        st.session_state.auth_mode = "login"

    if "business_id" not in st.session_state:
        if st.session_state.auth_mode == "signup":
            _show_signup_page()
        else:
            _show_login_page()
        return

    nav_items = [
        "🏠 Dashboard",
        "🛒 Orders",
        "📦 Products",
        "📋 Inventory",
        "💬 Customer Chats",
        "🔔 Notifications",
        "📊 Analytics & Reports",
        "📚 Knowledge Base",
        "🤖 AI Business Insights",
        "⚙️ Settings",
        "🚪 Logout",
    ]
    if st.session_state.get("dashboard_page") not in nav_items:
        st.session_state.dashboard_page = nav_items[0]
    requested_page = st.session_state.pop("requested_dashboard_page", None)
    if requested_page in nav_items:
        st.session_state.dashboard_page = requested_page
    st.sidebar.markdown(
        """
        <div class='brand-header'>
            <div class='brand-icon'>🏪</div>
            <div>
                <span class='brand-subtitle'>Business Automation</span>
                <div class='brand-title'>SmartBiz AI</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    session = get_session()
    try:
        business = session.query(Business).filter(Business.business_id == st.session_state.business_id).first()
        if business:
            st.sidebar.markdown(f"**{business.name}**")
            st.sidebar.caption(business.category or "Shopkeeper")
    finally:
        session.close()
    unread_count = _get_unread_count(st.session_state.business_id)
    if unread_count:
        st.sidebar.caption(f"{unread_count} unread notification(s)")
    page = st.sidebar.radio("Navigation", nav_items, key="dashboard_page")

    if page == "🚪 Logout":
        _logout()
        return

    if page == "🏠 Dashboard":
        _render_dashboard_home_page()
    elif page == "📊 Analytics & Reports":
        _render_analytics_page()
    elif page == "📦 Products":
        _render_products_page()
    elif page == "📋 Inventory":
        _render_inventory_page()
    elif page == "🛒 Orders":
        _render_orders_page()
    elif page == "💬 Customer Chats":
        _render_customer_chats_page()
    elif page == "🔔 Notifications":
        _render_notifications_page()
    elif page == "📚 Knowledge Base":
        _render_knowledge_base_page()
    elif page == "🤖 AI Business Insights":
        _render_ai_business_insights_page()
    elif page == "⚙️ Settings":
        _render_profile_page()


if __name__ == "__main__":
    main()
