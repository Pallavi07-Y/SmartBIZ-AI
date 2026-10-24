import html
import ipaddress
import json
import re
import secrets
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from sqlalchemy import or_

from app.agent.tools import AgentTools
from app.agent.ai_agent import AIAgent
from app.business.business_manager import BusinessController
from app.business.analytics import get_business_metrics
from app.business.catalog import BusinessCatalog
from app.business.register_business import BusinessManager
from app.business.search_business import BusinessSearch
from app.business.nearby import (
    LocationLookupError,
    geocode_place,
    get_customer_saved_location,
    reverse_geocode,
    save_shop_address,
    save_shop_location,
    save_customer_location,
    search_nearby_shops,
    validate_optional_coordinates,
    validate_coordinates,
)
from app.components.location_picker import location_picker
from app.database import (
    Business,
    CustomerAccount,
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
    authenticate_customer_account,
    clear_login_failures,
    generate_account_id,
    hash_password,
    login_lockout_remaining,
    normalize_email,
    normalize_phone,
    record_login_failure,
    register_customer_account,
    verify_password,
)
from app.utils.email_verification import (
    EmailVerificationError,
    send_email_verification_code,
    verify_email_verification_code,
)
from app.utils.logger import logger
from app.utils.time import utc_now_for_database


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
        conversation.updated_at = utc_now_for_database()

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


def _client_ip():
    client_ip = getattr(st.context, "ip_address", None)
    if not isinstance(client_ip, str):
        return None
    try:
        return ipaddress.ip_address(client_ip).compressed
    except ValueError:
        return None


def _rate_limit_device_id():
    return st.session_state.setdefault(
        "rate_limit_device_id",
        secrets.token_urlsafe(24),
    )


def _safe_html_text(value):
    return html.escape(str(value or ""), quote=True)


def _email_already_registered(email):
    session = get_session()
    try:
        return bool(
            session.query(Business.business_id)
            .filter(Business.owner_email == email)
            .first()
            or session.query(CustomerAccount.customer_id)
            .filter(CustomerAccount.email == email)
            .first()
        )
    finally:
        session.close()


def _render_email_verification(email, prefix, verified_state_key):
    expiry_minutes = max(1, settings.email_otp_ttl_seconds // 60)
    st.caption(
        f"Enter the six-digit code sent to {email}. "
        f"The code expires after {expiry_minutes} minutes."
    )
    with st.form(f"{prefix}_verification_form"):
        code = st.text_input("Email verification code", max_chars=6)
        submitted = st.form_submit_button("VERIFY EMAIL", use_container_width=True)
    if submitted:
        try:
            if verify_email_verification_code(email, code):
                st.session_state[verified_state_key] = email
                st.success("Email verified.")
                st.rerun()
            st.error("That code is invalid or expired. Request a new code and try again.")
        except EmailVerificationError as exc:
            st.error(str(exc))

    if st.button("Resend verification email", key=f"{prefix}_resend_code"):
        try:
            send_email_verification_code(email, _client_ip())
        except EmailVerificationError as exc:
            st.error(str(exc))
        else:
            st.success("A new verification email has been sent.")

    return st.button("Use a different email", key=f"{prefix}_change_email")


def _validate_upload_size(size_bytes):
    if not isinstance(size_bytes, int) or size_bytes < 0:
        raise ValueError("Unable to determine the uploaded file size.")
    if size_bytes > settings.max_upload_size_bytes:
        raise ValueError(
            f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB upload limit."
        )


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
                "payment_method": order.payment_method,
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
            "latitude": business.latitude,
            "longitude": business.longitude,
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


def search_customer_marketplace(query, *, product_limit=250):
    session = get_session()
    try:
        businesses = session.query(Business).order_by(Business.name.asc()).all()
        shops = [
            {
                "business_id": business.business_id,
                "name": business.name,
                "category": business.category,
                "address": business.address,
                "latitude": business.latitude,
                "longitude": business.longitude,
            }
            for business in businesses
        ]
    finally:
        session.close()

    products = AgentTools.__new__(AgentTools).get_marketplace_catalog(limit=product_limit)
    terms = [
        term for term in re.findall(r"[a-z0-9]+", (query or "").casefold())
        if len(term) > 1 and term not in {
            "a", "an", "and", "are", "best", "buy", "find", "for", "from", "i", "in",
            "is", "me", "my", "of", "on", "please", "product", "products", "shop",
            "shops", "the", "to", "want", "what", "which", "with", "you",
        }
    ]
    if not terms:
        return shops, products

    def match_score(text):
        words = set(re.findall(r"[a-z0-9]+", text.casefold()))
        return sum(
            any(word == term or (len(term) >= 4 and word.startswith(term)) for word in words)
            for term in terms
        )

    shops = [
        (match_score(" ".join(str(shop.get(key) or "") for key in ("name", "category", "address"))), shop)
        for shop in shops
    ]
    shops = [shop for score, shop in shops if score]
    shops.sort(key=lambda shop: shop["name"].casefold())

    products = [
        (
            match_score(" ".join(str(product.get(key) or "") for key in (
                "product_name", "brand", "category", "description",
                "shop_name", "shop_category", "shop_address",
            ))),
            product,
        )
        for product in products
    ]
    products = [product for score, product in products if score]
    products.sort(
        key=lambda product: (
            -match_score(" ".join(str(product.get(key) or "") for key in (
                "product_name", "brand", "category", "description",
                "shop_name", "shop_category", "shop_address",
            ))),
            product["stock_quantity"] <= 0,
            -product["recorded_orders"],
            product["product_name"].casefold(),
        )
    )
    matched_shop_ids = {product["business_id"] for product in products}
    for shop in shops:
        if shop["business_id"] in matched_shop_ids:
            matched_shop_ids.remove(shop["business_id"])
    if matched_shop_ids:
        session = get_session()
        try:
            related_shops = session.query(Business).filter(
                Business.business_id.in_(matched_shop_ids)
            ).all()
            shops.extend({
                "business_id": shop.business_id,
                "name": shop.name,
                "category": shop.category,
                "address": shop.address,
            } for shop in related_shops)
        finally:
            session.close()
        shops.sort(key=lambda shop: shop["name"].casefold())
    return shops, products


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
                "payment_method": order.payment_method,
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

            [data-testid="stSidebar"] h1,
            [data-testid="stSidebar"] h2,
            [data-testid="stSidebar"] h3,
            [data-testid="stSidebar"] h4,
            [data-testid="stSidebar"] h5,
            [data-testid="stSidebar"] h6,
            [data-testid="stSidebar"] .stMarkdown h1,
            [data-testid="stSidebar"] .stMarkdown h2,
            [data-testid="stSidebar"] .stMarkdown h3,
            [data-testid="stSidebar"] .stMarkdown h4,
            [data-testid="stSidebar"] .stMarkdown h5,
            [data-testid="stSidebar"] .stMarkdown h6 {
                color: #FFFFFF !important;
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

            .auth-card:empty {
                display: none;
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


def _show_customer_signup_page():
    st.markdown("<div class='auth-shell'><div class='auth-inner'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='auth-brand'><h1>WELCOME TO SMARTBIZ AI</h1>"
        "<h2>Business Automation System</h2></div>",
        unsafe_allow_html=True,
    )
    st.markdown("<div class='auth-card'>", unsafe_allow_html=True)
    st.markdown("<div class='auth-headline'>Create Customer Account</div>", unsafe_allow_html=True)

    customer_id = st.session_state.get("signup_success_customer_id")
    if customer_id:
        st.markdown("<div class='auth-subtitle'>Account Created</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='auth-subtitle'>Your Customer Account ID: "
            f"<strong>{customer_id}</strong></div>",
            unsafe_allow_html=True,
        )
        st.markdown("<div class='micro-copy'>Save this ID. You can also log in with your email.</div>", unsafe_allow_html=True)
        if st.button("GO TO LOGIN", use_container_width=True):
            st.session_state.auth_mode = "login"
            st.session_state.pop("signup_success_customer_id", None)
            st.rerun()
    else:
        pending_email = st.session_state.get("customer_signup_pending_email")
        verified_email = st.session_state.get("customer_signup_verified_email")
        signup_email = st.session_state.get("customer_signup_email", "")
        if pending_email and verified_email != pending_email:
            st.markdown("<div class='auth-subtitle'>Verify your email</div>", unsafe_allow_html=True)
            change_email = _render_email_verification(
                pending_email,
                "customer_signup",
                "customer_signup_verified_email",
            )
            if change_email:
                for key in (
                    "customer_signup_email",
                    "customer_signup_name",
                    "customer_signup_pending_email",
                    "customer_signup_verified_email",
                ):
                    st.session_state.pop(key, None)
                st.rerun()
        elif verified_email and verified_email == signup_email:
            st.markdown("<div class='auth-subtitle'>Email verified. Create your password.</div>", unsafe_allow_html=True)
            with st.form("customer_signup_password_form"):
                password = st.text_input("Create Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                submitted = st.form_submit_button("CREATE CUSTOMER ACCOUNT", use_container_width=True)
            if submitted:
                if len(password) < 8:
                    st.error("Password must be at least 8 characters long.")
                elif password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    try:
                        account = register_customer_account(
                            st.session_state.get("customer_signup_name", ""),
                            verified_email,
                            password,
                            email_verified=True,
                        )
                        st.session_state.signup_success_customer_id = account["customer_id"]
                        st.session_state.pop("customer_signup_pending_email", None)
                        st.session_state.pop("customer_signup_verified_email", None)
                        st.session_state.pop("customer_signup_email", None)
                        st.session_state.pop("customer_signup_name", None)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception:
                        st.error("Unable to create your account right now.")
        else:
            with st.form("customer_signup_form"):
                name = st.text_input(
                    "Full Name",
                    value=st.session_state.get("customer_signup_name", ""),
                )
                email = st.text_input(
                    "Email Address",
                    value=st.session_state.get("customer_signup_email", ""),
                )
                submitted = st.form_submit_button("VERIFY EMAIL", use_container_width=True)
            if submitted:
                normalized_email = normalize_email(email)
                if not normalized_email:
                    st.error("Please enter a valid email address.")
                elif not name.strip():
                    st.error("Please enter your name.")
                elif _email_already_registered(normalized_email):
                    st.error("An account already exists for this email address.")
                else:
                    try:
                        send_email_verification_code(normalized_email, _client_ip())
                    except EmailVerificationError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.customer_signup_name = name.strip()
                        st.session_state.customer_signup_email = normalized_email
                        st.session_state.customer_signup_pending_email = normalized_email
                        st.rerun()

    if not customer_id and st.button("Back to login", use_container_width=True):
        st.session_state.auth_mode = "login"
        for key in (
            "customer_signup_email",
            "customer_signup_name",
            "customer_signup_pending_email",
            "customer_signup_verified_email",
        ):
            st.session_state.pop(key, None)
        st.rerun()
    st.markdown("</div></div></div>", unsafe_allow_html=True)


def _show_signup_page():
    if st.session_state.get("role") == "customer":
        _show_customer_signup_page()
        return

    st.markdown("<div class='auth-shell'><div class='auth-inner'>", unsafe_allow_html=True)
    st.markdown("<div class='auth-brand'><h1>WELCOME TO SMARTBIZ AI</h1><h2>Business Automation System</h2></div>", unsafe_allow_html=True)

    if "signup_step" not in st.session_state:
        st.session_state.signup_step = "details"
    if st.session_state.signup_step in {"phone", "email", "otp"}:
        st.session_state.signup_step = "details"
    if "signup_email" not in st.session_state:
        st.session_state.signup_email = ""
    if "signup_latitude" not in st.session_state:
        st.session_state.signup_latitude = None
    if "signup_longitude" not in st.session_state:
        st.session_state.signup_longitude = None

    step = st.session_state.signup_step
    st.markdown("<div class='auth-card'>", unsafe_allow_html=True)
    if step == "details":
        st.markdown("<div class='auth-headline'>Business Details</div>", unsafe_allow_html=True)
        st.markdown("<div class='auth-subtitle'>Step 1 of 2</div>", unsafe_allow_html=True)
        st.caption("Shop coordinates are optional. You can use your device location or continue with the address only.")
        signup_location = location_picker(
            key="signup_shop_location_picker",
            button_label="Use My Current Location",
        )
        if isinstance(signup_location, dict):
            request_id = signup_location.get("request_id")
            if request_id and request_id != st.session_state.get("signup_location_request_id"):
                st.session_state.signup_location_request_id = request_id
                if signup_location.get("status") == "success":
                    try:
                        latitude, longitude = validate_coordinates(
                            signup_location.get("latitude"),
                            signup_location.get("longitude"),
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.signup_latitude = str(latitude)
                        st.session_state.signup_longitude = str(longitude)
                        st.success("Current location captured. Confirm the shop address before continuing, or leave it unused.")
                else:
                    st.warning(
                        signup_location.get("message")
                        or "Location permission was not available. You can continue using the shop address."
                    )
        with st.form("shop_details_form"):
            owner_name = st.text_input(
                "Owner Name",
                value=st.session_state.get("signup_owner_name", ""),
            )
            email = st.text_input(
                "Email Address",
                value=st.session_state.signup_email,
                placeholder="Enter your email address",
            )
            shop_name = st.text_input(
                "Shop Name",
                value=st.session_state.get("signup_shop_name", ""),
            )
            category = st.text_input(
                "Business Category",
                value=st.session_state.get("signup_category", ""),
            )
            address = st.text_area(
                "Shop Address",
                value=st.session_state.get("signup_address", ""),
            )
            submitted = st.form_submit_button("CONTINUE", use_container_width=True)
        if submitted:
            normalized_email = normalize_email(email)
            if not normalized_email:
                st.error("Please enter a valid email address.")
            elif not all([owner_name.strip(), shop_name.strip(), category.strip(), address.strip()]):
                st.error("Please complete all business details.")
            else:
                session = get_session()
                try:
                    existing_business = session.query(Business.business_id).filter(
                        Business.owner_email == normalized_email
                    ).first()
                    existing_customer = session.query(CustomerAccount.customer_id).filter(
                        CustomerAccount.email == normalized_email
                    ).first()
                    if existing_business or existing_customer:
                        st.error("An account already exists for this email address.")
                    else:
                        try:
                            send_email_verification_code(normalized_email, _client_ip())
                        except EmailVerificationError as exc:
                            st.error(str(exc))
                        else:
                            st.session_state.signup_email = normalized_email
                            st.session_state.signup_owner_name = owner_name.strip()
                            st.session_state.signup_shop_name = shop_name.strip()
                            st.session_state.signup_category = category.strip()
                            st.session_state.signup_address = address.strip()
                            st.session_state.signup_pending_email = normalized_email
                            st.session_state.signup_step = "verify_email"
                            st.rerun()
                finally:
                    session.close()

    elif step == "verify_email":
        if st.session_state.get("signup_pending_email") != st.session_state.signup_email:
            st.session_state.signup_step = "details"
            st.rerun()
        if st.session_state.get("signup_verified_email") == st.session_state.signup_email:
            st.session_state.signup_step = "password"
            st.rerun()
        st.markdown("<div class='auth-headline'>Verify your email</div>", unsafe_allow_html=True)
        change_email = _render_email_verification(
            st.session_state.signup_email,
            "shop_signup",
            "signup_verified_email",
        )
        if change_email:
            st.session_state.signup_step = "details"
            st.session_state.pop("signup_pending_email", None)
            st.session_state.pop("signup_verified_email", None)
            st.rerun()

    elif step == "password":
        if st.session_state.get("signup_verified_email") != st.session_state.signup_email:
            st.session_state.signup_step = "details"
            st.error("Verify your email before creating a business account.")
            st.rerun()
        st.markdown("<div class='auth-headline'>Create Password</div>", unsafe_allow_html=True)
        st.markdown("<div class='auth-subtitle'>Email verified · Step 2 of 2</div>", unsafe_allow_html=True)
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
                    account_id = generate_account_id("shopkeeper")
                    business = manager.register_business(
                        name=st.session_state.signup_shop_name,
                        category=st.session_state.signup_category,
                        address=st.session_state.signup_address,
                        owner_email=st.session_state.signup_email,
                        business_id=account_id,
                        password_hash=hash_password(password),
                        owner_name=st.session_state.signup_owner_name,
                        latitude=st.session_state.signup_latitude,
                        longitude=st.session_state.signup_longitude,
                        email_verified=True,
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
        business_id = st.session_state.get("signup_success_business_id") or "SBZ-S-XXXXXXXXXXXX"
        st.markdown(f"<div class='auth-subtitle'>Your Shopkeeper Account ID:</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:1.8rem; font-weight:800; color:#2D2D2D; margin-bottom:0.5rem;'>{business_id}</div>", unsafe_allow_html=True)
        st.markdown("<div class='micro-copy'>Save this ID. You will use it to login.</div>", unsafe_allow_html=True)
        if st.button("GO TO LOGIN", use_container_width=True):
            st.session_state.signup_step = "details"
            st.session_state.auth_mode = "login"
            st.session_state.remembered_login = {
                "identifier": business_id,
            }
            st.session_state.pop("signup_success_business_id", None)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div></div>", unsafe_allow_html=True)

    if step != "success":
        if st.button("Back to login", use_container_width=True):
            st.session_state.auth_mode = "login"
            st.session_state.signup_step = "details"
            st.session_state.pop("signup_success_business_id", None)
            st.rerun()


def _load_saved_login():
    default = {"identifier": ""}
    saved = st.session_state.get("remembered_login", default)
    if not isinstance(saved, dict):
        return default
    identifier = saved.get("identifier", saved.get("business_id", ""))
    st.session_state.remembered_login = {"identifier": identifier}
    return {"identifier": identifier}


def _show_role_selection_page():
    st.markdown(
        "<div class='hero-intro'><div class='hero-title'>WELCOME TO SMARTBIZ AI</div>"
        "<div class='hero-subtitle'>Choose how you want to work with your business operations.</div></div>",
        unsafe_allow_html=True,
    )

    st.markdown("<div class='section-heading'>Select your role</div>", unsafe_allow_html=True)
    _, center_column, _ = st.columns([1, 2, 1])
    with center_column:
        role_columns = st.columns(3, gap="small")
        if role_columns[0].button(
            "🏪 Shopkeeper Login",
            type="primary",
            use_container_width=True,
            key="role_shopkeeper",
        ):
            st.session_state.role = "shopkeeper"
            st.session_state.auth_mode = "login"
            st.rerun()
        if role_columns[1].button(
            "👤 Customer Login",
            type="primary",
            use_container_width=True,
            key="role_customer_login",
        ):
            st.session_state.role = "customer"
            st.session_state.auth_mode = "login"
            st.session_state.pop("customer_guest", None)
            st.rerun()
        if role_columns[2].button(
            "Browse as Guest",
            use_container_width=True,
            key="role_customer_guest",
        ):
            st.session_state.role = "customer"
            st.session_state.customer_guest = True
            st.session_state.customer_business_id = None
            st.rerun()

def _show_login_page():
    saved_login = _load_saved_login()
    is_customer = st.session_state.get("role") == "customer"
    role_title = "Customer" if is_customer else "Shopkeeper"

    st.markdown("<div class='auth-shell'><div class='auth-inner'>", unsafe_allow_html=True)
    st.markdown("<div class='auth-brand'><h1>WELCOME TO SMARTBIZ AI</h1><h2>Business Automation System</h2></div>", unsafe_allow_html=True)
    st.markdown("<div class='auth-card'>", unsafe_allow_html=True)
    st.markdown("<div class='auth-headline'>Welcome Back!</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='auth-subtitle'>{role_title} Login</div>", unsafe_allow_html=True)

    with st.form("login_form"):
        identifier = st.text_input(
            "Email Address or Account ID",
            value=saved_login.get("identifier", ""),
            key="login_identifier",
        )
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("LOGIN", use_container_width=True)

    if submitted:
        if not identifier.strip() or not password:
            st.error("Please enter your email address or account ID and password.")
        else:
            identifier = identifier.strip()
            client_ip = _client_ip()
            lockout_remaining = login_lockout_remaining(identifier, client_ip)
            if lockout_remaining:
                st.error(
                    f"Too many failed login attempts. Try again in {lockout_remaining} seconds."
                )
            else:
                session = get_session()
                try:
                    normalized_email = normalize_email(identifier)
                    if is_customer:
                        predicates = [CustomerAccount.customer_id == identifier]
                        if normalized_email:
                            predicates.append(CustomerAccount.email == normalized_email)
                        account = session.query(CustomerAccount).filter(or_(*predicates)).first()
                        canonical_id = account.customer_id if account else identifier
                    else:
                        predicates = [Business.business_id == identifier]
                        if normalized_email:
                            predicates.append(Business.owner_email == normalized_email)
                        account = session.query(Business).filter(or_(*predicates)).first()
                        canonical_id = account.business_id if account else identifier
                finally:
                    session.close()

                lockout_remaining = login_lockout_remaining(canonical_id, client_ip)
                if lockout_remaining:
                    st.error(
                        f"Too many failed login attempts. Try again in {lockout_remaining} seconds."
                    )
                elif (
                    account
                    and account.email_verified
                    and account.password_hash
                    and verify_password(password, account.password_hash)
                ):
                    clear_login_failures(canonical_id)
                    st.session_state.role = "customer" if is_customer else "shopkeeper"
                    st.session_state.auth_mode = "login"
                    st.session_state.remembered_login = {"identifier": identifier}
                    if is_customer:
                        st.session_state.customer_id = account.customer_id
                        st.session_state.customer_name = account.name
                        st.session_state.customer_session_id = account.customer_id
                        st.session_state.customer_guest = False
                    else:
                        st.session_state.business_id = account.business_id
                    st.rerun()
                else:
                    record_login_failure(canonical_id, client_ip)
                    st.error(f"Invalid {role_title.lower()} email/account ID or password.")

    st.caption("Password recovery is unavailable. Contact your administrator if you lose access.")
    if st.button("CREATE ACCOUNT", use_container_width=True):
        st.session_state.auth_mode = "signup"
        st.rerun()

    if is_customer and st.button("BROWSE AS GUEST", use_container_width=True):
        st.session_state.customer_guest = True
        st.session_state.customer_business_id = None
        st.rerun()
    if st.button("BACK TO ROLE SELECTION", use_container_width=True):
        _logout()

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div></div>", unsafe_allow_html=True)


def _logout():
    st.session_state.clear()
    st.rerun()


def _sync_dashboard_page(widget_key):
    st.session_state.dashboard_page = st.session_state[widget_key]


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
            "Payment": order.get("payment_method") or "Not recorded",
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

    st.markdown("<div class='section-heading'>Products &amp; Stock</div>", unsafe_allow_html=True)
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
            ram_gb = st.number_input("RAM (GB, if applicable)", min_value=0, value=0, step=1)
            storage_gb = st.number_input("Storage (GB, if applicable)", min_value=0, value=0, step=1)
            processor = st.text_input("Processor / key specification")
            description = st.text_area("Features / description", height=100)
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
                ram_gb=int(ram_gb) if ram_gb else None,
                storage_gb=int(storage_gb) if storage_gb else None,
                processor=processor.strip() or None,
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
            with st.expander("Edit product features and specifications"):
                with st.form(f"update_product_details_{product.get('id')}"):
                    detail_columns = st.columns(2)
                    with detail_columns[0]:
                        updated_brand = st.text_input(
                            "Brand",
                            value=product.get("brand") or "",
                            key=f"brand_{product.get('id')}",
                        )
                        updated_ram_gb = st.number_input(
                            "RAM (GB, if applicable)",
                            min_value=0,
                            value=int(product.get("ram_gb") or 0),
                            key=f"ram_{product.get('id')}",
                        )
                        updated_processor = st.text_input(
                            "Processor / key specification",
                            value=product.get("processor") or "",
                            key=f"processor_{product.get('id')}",
                        )
                    with detail_columns[1]:
                        updated_category = st.text_input(
                            "Category",
                            value=product.get("category") or "",
                            key=f"category_{product.get('id')}",
                        )
                        updated_storage_gb = st.number_input(
                            "Storage (GB, if applicable)",
                            min_value=0,
                            value=int(product.get("storage_gb") or 0),
                            key=f"storage_{product.get('id')}",
                        )
                    updated_description = st.text_area(
                        "Features / description",
                        value=product.get("description") or "",
                        key=f"description_{product.get('id')}",
                        height=100,
                    )
                    details_saved = st.form_submit_button("Save product details")
                if details_saved:
                    controller.update_product(
                        business_id,
                        product["id"],
                        brand=updated_brand.strip() or None,
                        category=updated_category.strip() or None,
                        ram_gb=int(updated_ram_gb) if updated_ram_gb else None,
                        storage_gb=int(updated_storage_gb) if updated_storage_gb else None,
                        processor=updated_processor.strip() or None,
                        description=updated_description.strip() or None,
                    )
                    catalog.sync_products_to_rag()
                    st.success(f"Features updated for {product.get('product_name')}.")
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

    st.markdown("<div class='section-heading'>Shop Knowledge</div>", unsafe_allow_html=True)
    st.markdown("<div class='hero-subtitle'>Upload this shop's documents so the AI can answer from your policies, product notes, and service information.</div>", unsafe_allow_html=True)

    with st.form("knowledge_upload_form"):
        file = st.file_uploader(
            "Upload shop document",
            type=["pdf", "docx", "txt", "jpg", "jpeg", "png", "csv"],
            help=(
                "Uploaded documents are processed for shop knowledge. Images are sent to the "
                "configured vision provider for text extraction."
            ),
        )
        image_processing_consent = st.checkbox(
            "I consent to sending uploaded images to the configured vision provider for text extraction.",
            value=False,
        )
        submitted = st.form_submit_button("Upload and index")

    if submitted and file is not None:
        safe_name = Path(file.name.replace("\\", "/")).name
        is_image = Path(safe_name).suffix.lower() in {".jpg", ".jpeg", ".png"}
        if not safe_name:
            st.error("The uploaded file must have a name.")
        elif is_image and not image_processing_consent:
            st.warning("Consent is required before an image can be sent to the configured vision provider.")
        else:
            try:
                _validate_upload_size(file.size)
            except ValueError as exc:
                st.error(str(exc))
            else:
                with tempfile.TemporaryDirectory(prefix="smartbiz-knowledge-") as temp_dir:
                    try:
                        temp_path = Path(temp_dir) / safe_name
                        temp_path.write_bytes(file.getvalue())
                        result = controller.add_document(business_id, str(temp_path))
                    except Exception as exc:
                        logger.error("Knowledge document processing failed (%s).", type(exc).__name__)
                        st.error("Unable to index the document. Check its format and try again.")
                    else:
                        st.success(f"Indexed {safe_name} successfully.")
                        st.caption(f"Documents added: {result.get('documents_added')}")
                        st.session_state.last_doc_result = result
                        st.rerun()

    if not documents:
        st.info("No knowledge-base documents have been uploaded yet.")
        return

    st.markdown("<div class='section-heading'>Indexed Documents</div>", unsafe_allow_html=True)
    for doc in documents:
        with st.container():
            st.markdown(
                f"<div style='background:#ffffff;border:1px solid #E5E7EB;border-radius:12px;padding:1rem;margin-bottom:0.8rem;'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap;'>"
                f"<div><strong>{_safe_html_text(doc.get('document_name'))}</strong><br><span style='color:#6B7280;'>{_safe_html_text(doc.get('document_type') or 'unknown')} • {_safe_html_text(doc.get('status') or 'Pending')}</span></div>"
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
        st.subheader("Shop location for nearby discovery")
        st.caption(
            "Enter and save the shop address below. Coordinates are optional; while on-site, "
            "you can use your device location without entering latitude or longitude."
        )
        shop_location_result = location_picker(
            key=f"shop_location_picker_{business_id}",
            button_label="Use My Current Location",
        )
        if isinstance(shop_location_result, dict):
            request_id = shop_location_result.get("request_id")
            if (
                request_id
                and request_id != st.session_state.get("shop_location_request_id")
            ):
                st.session_state.shop_location_request_id = request_id
                if shop_location_result.get("status") == "success":
                    try:
                        shop_latitude, shop_longitude = validate_coordinates(
                            shop_location_result.get("latitude"),
                            shop_location_result.get("longitude"),
                        )
                        address = business.address
                        if not address:
                            st.error("Enter and save the shop address before saving its location.")
                            continue_saving = False
                        else:
                            save_shop_location(
                                business_id,
                                address,
                                shop_latitude,
                                shop_longitude,
                            )
                            continue_saving = True
                    except ValueError as exc:
                        st.error(str(exc))
                        continue_saving = False
                    except Exception as exc:
                        logger.error(
                            "Unable to save shop coordinates (%s).",
                            type(exc).__name__,
                        )
                        st.error("Unable to save the shop location. Please try again.")
                        continue_saving = False
                    else:
                        if continue_saving:
                            st.success("Shop address and location saved.")
                            st.rerun()
                else:
                    st.warning(
                        shop_location_result.get("message")
                        or "Unable to get this device's location."
                    )
        with st.form("shop_location_form"):
            address = st.text_area(
                "Shop Address",
                value=business.address or "",
            )
            save_location = st.form_submit_button("Save shop address")
        if save_location:
            try:
                save_shop_address(business_id, address)
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:
                logger.error(
                    "Unable to save shop coordinates (%s).",
                    type(exc).__name__,
                )
                st.error("Unable to save the shop location. Please try again.")
            else:
                st.success("Shop address saved.")
                st.rerun()

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

        st.subheader("AI Business Hub")
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

    today = utc_now_for_database().date()
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

    notification_ids = [item["notification_id"] for item in notifications]
    selected_id = st.session_state.get("selected_notification_id")
    if selected_id not in notification_ids:
        selected_id = notification_ids[0]
        st.session_state.selected_notification_id = selected_id
    selected_notification = next(
        item for item in notifications if item["notification_id"] == selected_id
    )

    list_column, detail_column = st.columns([1, 1.7], gap="large")
    with list_column:
        st.subheader("Inbox")
        for notification in notifications:
            unread_marker = " • New" if not notification.get("is_read") else ""
            if st.button(
                f"{notification.get('title') or 'Notification'}{unread_marker}",
                key=f"notification_select_{notification['notification_id']}",
                type="primary" if notification["notification_id"] == selected_id else "secondary",
                width="stretch",
            ):
                st.session_state.selected_notification_id = notification["notification_id"]
                st.rerun()
            st.caption(notification.get("message") or "")

    with detail_column:
        st.subheader(selected_notification.get("title") or "Notification")
        st.write(selected_notification.get("message") or "")
        created_at = selected_notification.get("created_at")
        if created_at:
            st.caption(created_at.strftime("%d %b %Y, %I:%M %p"))
        reference_type = selected_notification.get("reference_type")
        reference_id = selected_notification.get("reference_id")
        if reference_type:
            st.caption(
                f"Related to {reference_type.lower()}"
                + (f" · {reference_id}" if reference_id else "")
            )
        if selected_notification.get("is_read"):
            st.caption("Read")
        elif st.button(
            "Mark as read",
            key=f"notification_read_{selected_notification['notification_id']}",
        ):
            mark_notification_as_read(business_id, selected_notification["notification_id"])
            st.rerun()


def _render_ai_business_insights_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view AI insights.")
        return

    metrics = get_business_metrics(business_id)
    st.subheader("AI Insights")
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


def _render_shop_ai_chat():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to use the shop AI assistant.")
        return

    st.subheader("AI Chat")
    st.caption("Ask about this shop's indexed documents, active products, orders, or inventory.")
    chats_by_business = st.session_state.setdefault("shop_ai_chats_by_business", {})
    history = chats_by_business.setdefault(business_id, [])
    for item in history:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])

    question = st.chat_input("Ask a question about your shop")
    if not question:
        return

    history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    try:
        with st.spinner("Checking this shop's knowledge and live records..."):
            tools = AgentTools()
            knowledge_context = tools.get_business_context(business_id, question)
            products = tools.get_live_products(business_id, limit=100)
            metrics = get_business_metrics(business_id)
            business_data = {
                key: metrics[key]
                for key in (
                    "total_orders",
                    "today_orders",
                    "month_orders",
                    "revenue",
                    "average_order_value",
                    "pending_orders",
                    "completed_orders",
                    "total_products",
                    "total_units",
                    "low_stock_products",
                    "out_of_stock_products",
                    "stock_value",
                    "customer_count",
                    "conversation_count",
                    "escalated_count",
                )
            }
            payload = json.dumps(
                {
                    "shop_knowledge": knowledge_context or "No relevant uploaded knowledge found.",
                    "active_products_from_this_shop_sqlite": products,
                    "verified_metrics_from_this_shop_sqlite": business_data,
                },
                default=str,
            )
            response = LLM().generate_response(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are the private SmartBiz assistant for the currently authenticated shopkeeper. "
                            "Answer using only the supplied shop knowledge, active product catalog, and verified "
                            "SQLite metrics. The supplied data belongs only to this shop; do not claim access to "
                            "other shops or invent facts. SQLite values are authoritative for current product "
                            "prices, stock, availability, order totals, order status, and metrics; ignore such "
                            "values in retrieved documents. If the supplied evidence does not answer the question, "
                            "say what is missing. Do not place orders, change records, or expose customer personal "
                            "information. Be concise and practical."
                        ),
                    },
                    *[
                        {"role": item["role"], "content": item["content"]}
                        for item in history[-11:-1]
                    ],
                    {
                        "role": "user",
                        "content": f"Question: {question}\n\nShop-only evidence:\n{payload}",
                    },
                ],
                temperature=0.2,
                max_tokens=900,
            )
        if not response or response.startswith("Sorry, something went wrong"):
            raise RuntimeError("The configured LLM could not generate a response.")
    except Exception as exc:
        logger.error(
            "Shopkeeper AI chat failed for business %s (%s).",
            business_id,
            type(exc).__name__,
        )
        history.pop()
        st.error("I couldn't get an AI response. Check the AI provider settings and try again.")
        return

    history.append({"role": "assistant", "content": response})
    chats_by_business[business_id] = history[-12:]
    st.session_state.shop_ai_chats_by_business = chats_by_business
    with st.chat_message("assistant"):
        st.markdown(response)


def _render_ai_business_hub_page():
    st.title("AI Business Hub")
    st.caption(
        "Shop chat prompts and retrieved shop knowledge are sent to the AI provider configured by the operator. "
        "Uploaded image content is sent to the configured vision provider for text extraction."
    )
    knowledge_tab, insights_tab, chat_tab = st.tabs(
        ["Shop Knowledge", "AI Insights", "AI Chat"]
    )
    with knowledge_tab:
        _render_knowledge_base_page()
    with insights_tab:
        _render_ai_business_insights_page()
    with chat_tab:
        _render_shop_ai_chat()


def _render_customer_dashboard():
    st.title("Find Shops & Products")
    st.caption("Search across all shops and active product listings.")
    _render_nearby_shop_discovery()
    search_text = st.text_input(
        "Search shops or products",
        placeholder="Try a product, brand, category, shop, or location",
        key="customer_marketplace_search",
    ).strip()
    shops, products = search_customer_marketplace(search_text)

    if search_text:
        st.subheader(f"Matching shops ({len(shops)})")
        if not shops:
            st.caption("No shop name, category, or location matched that search.")
        for start in range(0, len(shops), 2):
            columns = st.columns(2, gap="large")
            for column, shop in zip(columns, shops[start:start + 2]):
                with column.container(border=True):
                    st.markdown(f"### {shop['name']}")
                    st.caption(f"{shop.get('category') or 'General'} · {shop.get('address') or 'Address not listed'}")
                    if st.button("Open shop", key=f"marketplace_shop_{shop['business_id']}"):
                        st.session_state.customer_business_id = shop["business_id"]
                        st.session_state.customer_shop_detail = True
                        st.session_state.requested_customer_page = "Dashboard"
                        st.rerun()

        st.subheader(f"Matching products ({len(products)})")
        if not products:
            st.caption("No active product listing matched that search.")
        for start in range(0, len(products), 2):
            columns = st.columns(2, gap="large")
            for column, product in zip(columns, products[start:start + 2]):
                with column.container(border=True):
                    st.markdown(f"### {product['product_name']}")
                    st.caption(f"{product.get('brand') or 'Brand not listed'} · {product.get('category') or 'General'}")
                    st.write(f"**Shop:** {product['shop_name']}")
                    price = f"₹{product['price']:,}" if product.get("price") is not None else "Price not listed"
                    stock = (
                        f"In stock ({product['stock_quantity']})"
                        if product["stock_quantity"] > 0
                        else "Out of stock"
                    )
                    st.write(f"{price} · {stock}")
                    if st.button("Browse this shop", key=f"marketplace_product_{product['id']}"):
                        st.session_state.customer_business_id = product["business_id"]
                        st.session_state.customer_shop_detail = True
                        st.session_state.requested_customer_page = "Dashboard"
                        st.rerun()
    else:
        st.subheader("Explore shops")
        if not shops:
            st.info("No shops are listed yet.")
            return
        for start in range(0, len(shops), 2):
            columns = st.columns(2, gap="large")
            for column, shop in zip(columns, shops[start:start + 2]):
                with column.container(border=True):
                    st.markdown(f"### {shop['name']}")
                    st.caption(f"{shop.get('category') or 'General'} · {shop.get('address') or 'Address not listed'}")
                    if st.button("Open shop", key=f"marketplace_shop_{shop['business_id']}"):
                        st.session_state.customer_business_id = shop["business_id"]
                        st.session_state.customer_shop_detail = True
                        st.session_state.requested_customer_page = "Dashboard"
                        st.rerun()


def _render_nearby_shop_discovery():
    st.subheader("Find shops near you")
    st.caption("Choose live location or enter a town or postal code to find registered shops.")
    st.caption(
        "If you continue with either location option, the selected GPS coordinates or town "
        "will be sent to OpenStreetMap to identify the area. Shop coordinates are set by each "
        "shopkeeper; shops without them are never assigned guessed distances."
    )
    st.caption("Location data © OpenStreetMap contributors.")

    current_location = st.session_state.get("customer_nearby_location") or {}
    location_result = location_picker(
        key="customer_nearby_location_picker",
    )
    if isinstance(location_result, dict):
        request_id = location_result.get("request_id")
        if request_id and request_id != st.session_state.get("customer_nearby_request_id"):
            st.session_state.customer_nearby_request_id = request_id
            if location_result.get("status") == "success":
                try:
                    latitude, longitude = validate_coordinates(
                        location_result.get("latitude"),
                        location_result.get("longitude"),
                    )
                except ValueError as exc:
                    st.session_state.pop("customer_nearby_location", None)
                    st.session_state.customer_nearby_location_error = str(exc)
                else:
                    label = "Your location"
                    error = None
                    try:
                        label = reverse_geocode(latitude, longitude)
                    except LocationLookupError as exc:
                        error = str(exc)
                    st.session_state.customer_nearby_location = {
                        "latitude": latitude,
                        "longitude": longitude,
                        "label": label,
                    }
                    st.session_state.customer_nearby_location_error = error
            else:
                st.session_state.pop("customer_nearby_location", None)
                st.session_state.customer_nearby_location_error = (
                    location_result.get("message")
                    or "Unable to get your location. Enter a town to search manually."
                )
            st.rerun()

    with st.form("customer_manual_location_form"):
        manual_location = st.text_input(
            "Enter town, city, or postal code",
            placeholder="For example, Madanapalle",
            key="customer_manual_location_query",
        )
        search_location = st.form_submit_button("Search this location")

    if search_location:
        query = manual_location.strip()
        if not query:
            st.warning("Enter a town, city, or postal code.")
        else:
            try:
                resolved = geocode_place(query)
            except LocationLookupError as exc:
                st.session_state.customer_nearby_location = {
                    "latitude": None,
                    "longitude": None,
                    "label": query,
                }
                st.session_state.customer_nearby_location_error = (
                    f"{exc} Showing registered shops whose saved address matches your search; "
                    "distance cannot be calculated."
                )
            else:
                st.session_state.customer_nearby_location = resolved
                st.session_state.customer_nearby_location_error = None
            st.rerun()

    location = st.session_state.get("customer_nearby_location")
    location_error = st.session_state.get("customer_nearby_location_error")
    if location_error:
        st.warning(location_error)
    if not location:
        return

    label = location["label"]
    has_search_coordinates = (
        location["latitude"] is not None and location["longitude"] is not None
    )
    if has_search_coordinates:
        radius_km = 5
        st.markdown(f"**Nearby registered shops · {label} · within 5 km**")
        results = search_nearby_shops(
            location["latitude"],
            location["longitude"],
            radius_km=radius_km,
        )
    else:
        st.markdown(f"**Registered shops matching {label}**")
        results = search_nearby_shops(location_query=label)

    nearby_shops = results["nearby_shops"]
    outside_shops = results["outside_radius_shops"]
    missing_shops = results["missing_coordinate_shops"]

    def render_shop_cards(shops):
        for start in range(0, len(shops), 2):
            columns = st.columns(2, gap="large")
            for column, shop in zip(columns, shops[start:start + 2]):
                with column.container(border=True):
                    st.markdown(f"### {shop['name']}")
                    st.caption(
                        f"{shop.get('category') or 'General'} · "
                        f"{shop.get('address') or 'Address not listed'}"
                    )
                    if shop["location_status"] == "outside_radius":
                        st.warning(
                            f"Outside the {results['radius_km']} km search radius"
                            + (
                                f" · {shop['distance_km']:.1f} km away"
                                if shop["distance_km"] is not None
                                else ""
                            )
                        )
                    elif shop["location_status"] == "missing_coordinates":
                        st.info("Shop coordinates have not been added.")
                    elif shop["distance_km"] is not None:
                        if shop.get("location_precision") != "shop":
                            st.write(
                                f"**Approximate town-center distance:** "
                                f"{shop['distance_km']:.1f} km"
                            )
                        else:
                            st.write(f"**Distance:** {shop['distance_km']:.1f} km")
                    if shop["products"]:
                        st.write("**Products**")
                        for product in shop["products"]:
                            price = (
                                f"₹{product['price']:,}"
                                if product["price"] is not None
                                else "Price not listed"
                            )
                            stock = (
                                f"In stock ({product['stock_quantity']})"
                                if product["stock_quantity"] > 0
                                else "Out of stock"
                            )
                            st.write(f"- {product['product_name']} · {price} · {stock}")
                    else:
                        st.caption("No active products are listed for this shop.")
                    if st.button(
                        "Open shop",
                        key=f"nearby_shop_{shop['business_id']}",
                    ):
                        st.session_state.customer_business_id = shop["business_id"]
                        st.session_state.customer_shop_detail = True
                        st.session_state.requested_customer_page = "Dashboard"
                        st.rerun()

    if nearby_shops:
        st.subheader("Shops within 5 km" if has_search_coordinates else "Shops matching this town")
        render_shop_cards(nearby_shops)
    if outside_shops:
        with st.expander(
            f"Outside the 5 km radius ({len(outside_shops)})"
        ):
            st.caption("These registered shops match the town, but are outside the nearby radius.")
            render_shop_cards(outside_shops)
    if missing_shops:
        with st.expander(f"Missing shop coordinates ({len(missing_shops)})"):
            st.caption(
                "These registered shops match the town but cannot be distance-ranked yet. "
                "Shopkeepers can add real coordinates in Settings."
            )
            render_shop_cards(missing_shops)
    if not nearby_shops and not outside_shops and not missing_shops:
        st.info("No registered shops with matching location details were found.")


def _render_customer_shop_page(customer_session_id, business):
    business_id = business["business_id"]
    if st.button("Back to marketplace", key="customer_shop_back_to_marketplace"):
        st.session_state.customer_shop_detail = False
        st.session_state.requested_customer_page = "Dashboard"
        st.rerun()

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
        customer_id = st.session_state.get("customer_id")
        saved_location = get_customer_saved_location(customer_id) if customer_id else None
        if saved_location:
            st.session_state.setdefault(
                "customer_address", saved_location.get("address") or ""
            )
            st.session_state.setdefault(
                "customer_latitude", saved_location.get("latitude")
            )
            st.session_state.setdefault(
                "customer_longitude", saved_location.get("longitude")
            )
        location_result = location_picker(
            key=f"customer_checkout_location_{customer_session_id}",
            button_label="Use My Location",
        )
        if (
            isinstance(location_result, dict)
            and location_result.get("request_id")
            != st.session_state.get("customer_checkout_location_request_id")
        ):
            st.session_state.customer_checkout_location_request_id = location_result.get(
                "request_id"
            )
            if location_result.get("status") == "success":
                address = str(
                    st.session_state.get("customer_address")
                    or (saved_location or {}).get("address")
                    or ""
                ).strip()
                try:
                    latitude, longitude = validate_optional_coordinates(
                        location_result.get("latitude"),
                        location_result.get("longitude"),
                    )
                    st.session_state.customer_address = address
                    st.session_state.customer_latitude = latitude
                    st.session_state.customer_longitude = longitude
                    if customer_id and address:
                        save_customer_location(
                            customer_id, address, latitude, longitude
                        )
                    st.rerun()
                except ValueError as exc:
                    st.warning(str(exc))
                except Exception as exc:
                    logger.error(
                        "Unable to save customer location (%s).",
                        type(exc).__name__,
                    )
                    st.error("Unable to save the delivery location. Please try again.")
            elif location_result.get("status") == "error":
                st.warning(
                    location_result.get("message")
                    or "Unable to get your location. You can enter an address manually."
                )
        with st.form(f"customer_checkout_{business_id}"):
            customer_name = st.text_input("Your name", value=st.session_state.get("customer_name", ""))
            customer_phone = st.text_input("Phone number", value=st.session_state.get("customer_phone", ""))
            customer_address = st.text_area("Delivery address", value=st.session_state.get("customer_address", ""))
            payment_method = st.selectbox("Payment method", ["COD", "EMI"])
            st.caption("COD and EMI are recorded as order details only; no payment is processed.")
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
                        payment_method=payment_method,
                        confirmed=True,
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
            columns[2].write(f"Payment: {order.get('payment_method') or 'Not recorded'}")
            if order.get("customer_address"):
                st.caption(f"Delivery address: {order['customer_address']}")


def _render_customer_settings(customer_session_id, business):
    st.title("Customer Settings")
    st.subheader("Current Session")
    st.code(customer_session_id[:8] + "..." if len(customer_session_id) > 12 else customer_session_id)
    st.write(f"Current shop: {business['name'] if business else 'None selected'}")
    st.subheader("Contact details")
    customer_id = st.session_state.get("customer_id")
    saved_location = get_customer_saved_location(customer_id) if customer_id else None
    if saved_location:
        st.session_state.setdefault(
            "customer_address", saved_location.get("address") or ""
        )
        st.session_state.setdefault(
            "customer_latitude", saved_location.get("latitude")
        )
        st.session_state.setdefault(
            "customer_longitude", saved_location.get("longitude")
        )
    location_result = location_picker(
        key=f"customer_profile_location_{customer_session_id}",
        button_label="Use My Location",
    )
    if (
        isinstance(location_result, dict)
        and location_result.get("request_id")
        != st.session_state.get("customer_profile_location_request_id")
    ):
        st.session_state.customer_profile_location_request_id = location_result.get(
            "request_id"
        )
        if location_result.get("status") == "success":
            address = str(
                st.session_state.get("customer_address")
                or (saved_location or {}).get("address")
                or ""
            ).strip()
            try:
                latitude, longitude = validate_optional_coordinates(
                    location_result.get("latitude"),
                    location_result.get("longitude"),
                )
                st.session_state.customer_address = address
                st.session_state.customer_latitude = latitude
                st.session_state.customer_longitude = longitude
                st.session_state.customer_profile_address_input = address
                if customer_id and address:
                    save_customer_location(customer_id, address, latitude, longitude)
                st.success("Address and map location saved.")
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))
            except Exception as exc:
                logger.error(
                    "Unable to save customer location (%s).",
                    type(exc).__name__,
                )
                st.error("Unable to save the delivery location. Please try again.")
        elif location_result.get("status") == "error":
            st.warning(
                location_result.get("message")
                or "Unable to get your location. Enter an address manually."
            )
    with st.form("customer_contact_details_form"):
        customer_name = st.text_input(
            "Your name",
            value=st.session_state.get("customer_name", ""),
            key="customer_profile_name_input",
        )
        customer_phone = st.text_input(
            "Phone number",
            value=st.session_state.get("customer_phone", ""),
            key="customer_profile_phone_input",
        )
        customer_address = st.text_area(
            "Delivery address",
            value=st.session_state.get("customer_address", ""),
            key="customer_profile_address_input",
        )
        save_contact_details = st.form_submit_button("Save contact details")
    if save_contact_details:
        normalized_phone = normalize_phone(customer_phone)
        if normalized_phone and not 10 <= len(normalized_phone) <= 13:
            st.error("Enter a valid phone number with 10 to 13 digits.")
        else:
            st.session_state.customer_name = customer_name.strip()
            st.session_state.customer_phone = customer_phone.strip()
            st.session_state.customer_address = customer_address.strip()
            if customer_id and customer_address.strip():
                try:
                    save_customer_location(
                        customer_id,
                        customer_address,
                        st.session_state.get("customer_latitude"),
                        st.session_state.get("customer_longitude"),
                    )
                except ValueError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    logger.error(
                        "Unable to save customer address (%s).",
                        type(exc).__name__,
                    )
                    st.error("Unable to save the delivery address. Please try again.")
                else:
                    st.success("Contact details and address saved.")
            else:
                st.success("Contact details saved for this customer session.")
    if business and st.button("Leave Current Shop"):
        st.session_state.customer_business_id = None
        st.session_state.customer_shop_detail = False
        st.session_state.requested_customer_page = "Dashboard"
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


def _render_customer_marketplace_assistant(customer_session_id):
    st.title("AI Shopping Assistant")
    st.caption("Ask questions or compare current products and shops using live listings.")
    st.info(
        "I can compare listed products, prices, stock, locations, and recorded orders. "
        "Customer reviews and ratings are not collected, so I won't invent them."
    )
    history_key = f"marketplace_assistant_history_{customer_session_id}"
    history = st.session_state.get(history_key, [])
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    st.caption(
        "Messages and relevant shop information are sent to the AI provider configured by the operator. "
        "Avoid sharing sensitive personal information."
    )
    question = st.chat_input("Ask anything, or compare a product across shops...")
    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)
    try:
        with st.chat_message("assistant"):
            with st.spinner("Checking shop listings and preparing an answer..."):
                answer = AIAgent().chat_marketplace(question, history)
            st.markdown(answer)
    except Exception as exc:
        logger.error(
            "Marketplace assistant failed (%s).",
            type(exc).__name__,
        )
        answer = (
            "I couldn't prepare an answer right now. You can still use the dashboard search "
            "to check live shop and product listings."
        )
        with st.chat_message("assistant"):
            st.markdown(answer)

    st.session_state[history_key] = history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
    st.rerun()


def _render_customer_assistant(business, customer_session_id):
    scopes = ["All shops"]
    if business:
        scopes.append(f"This shop: {business['name']}")
    scope = st.radio(
        "Assistant scope",
        scopes,
        horizontal=True,
        key="customer_assistant_scope",
    )
    if scope == "All shops":
        _render_customer_marketplace_assistant(customer_session_id)
        return
    _render_customer_shop_assistant(business, customer_session_id)


def _render_customer_shop_assistant(business, customer_session_id):
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
                            customer_name=st.session_state.get("customer_name"),
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
    customer_pages = ["Dashboard", "AI Assistant", "Orders", "Settings"]
    requested_page = st.session_state.pop("requested_customer_page", None)
    if requested_page in customer_pages:
        st.session_state.customer_page = requested_page
    if st.session_state.get("customer_page") not in customer_pages:
        st.session_state.customer_page = "Dashboard"

    st.sidebar.markdown("## SMARTBIZ AI")
    st.sidebar.caption("Business Automation System")
    if st.session_state.get("customer_id"):
        st.sidebar.caption(
            f"{st.session_state.get('customer_name', 'Customer')} "
            f"· {st.session_state.customer_id}"
        )
    else:
        st.sidebar.caption("Guest customer")
    page = st.sidebar.radio("Customer navigation", customer_pages, key="customer_page")
    if business:
        st.sidebar.divider()
        st.sidebar.markdown(f"**{business['name']}**")
        st.sidebar.caption(business.get("category") or "Shop")
        if st.sidebar.button("Switch Shop", key="customer_switch_shop"):
            st.session_state.customer_business_id = None
            st.session_state.customer_shop_detail = False
            st.session_state.requested_customer_page = "Dashboard"
            st.rerun()
    if st.sidebar.button("LOG OUT", key="customer_logout"):
        _logout()

    if page == "Dashboard":
        if business and st.session_state.get("customer_shop_detail"):
            _render_customer_shop_page(customer_session_id, business)
        else:
            _render_customer_dashboard()
    elif page == "AI Assistant":
        _render_customer_assistant(business, customer_session_id)
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
            st.session_state.requested_dashboard_page = "📦 Products & Stock"
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
    st.title("Customer Chats")
    st.caption("Select a conversation to view and reply without leaving this page.")
    if not conversations:
        st.info("No customer conversations yet.")
        return

    status_filter = st.selectbox("Filter conversations", ["All", "AI_HANDLING", "ESCALATED", "KEEPER_HANDLING", "RESOLVED"])
    visible = [item for item in conversations if status_filter == "All" or item["status"] == status_filter]
    if not visible:
        st.info("No conversations match this status.")
        return

    visible_ids = [item["id"] for item in visible]
    selected_id = st.session_state.get("selected_customer_chat_id")
    if selected_id not in visible_ids:
        selected_id = visible_ids[0]
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

    list_column, chat_column = st.columns([1, 1.8], gap="large")
    with list_column:
        st.subheader("Conversations")
        for item in visible:
            if st.button(
                f"{item['customer_phone']} · {item['status']}",
                key=f"customer_chat_select_{item['id']}",
                type="primary" if item["id"] == selected_id else "secondary",
                width="stretch",
            ):
                st.session_state.selected_customer_chat_id = item["id"]
                st.rerun()
            st.caption(item["latest_message"] or "No messages yet")

    with chat_column:
        st.subheader(conversation.customer_phone or "Unknown customer")
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

        with st.container(height=400, border=True):
            if messages:
                for message in messages:
                    sender = (
                        "Shopkeeper"
                        if message.sender == MessageSender.KEEPER
                        else "Customer"
                        if message.sender == MessageSender.CUSTOMER
                        else "AI"
                    )
                    role = "user" if message.sender == MessageSender.CUSTOMER else "assistant"
                    with st.chat_message(role):
                        st.caption(
                            f"{sender} · {message.timestamp.strftime('%d %b %Y %I:%M %p') if message.timestamp else ''}"
                        )
                        st.write(message.text)
            else:
                st.info("No messages in this conversation yet.")

        with st.form(f"reply_to_customer_{selected_id}", clear_on_submit=True):
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
            f"<div style='display:flex;justify-content:space-between;align-items:center;gap:1rem;'><div><strong>📱 {_safe_html_text(option['customer_phone'])}</strong></div><div style='background:#FDECEC;color:#B42318;border-radius:999px;padding:0.25rem 0.6rem;font-size:0.72rem;font-weight:700;'>{_safe_html_text(option['status'])}</div></div>"
            f"<div style='color:#6B7280;margin-top:0.6rem;'>{html.escape(str(option['latest_message']))}</div>"
            f"<div style='color:#6B7280;font-size:0.8rem;margin-top:0.5rem;'>Updated: {html.escape(str(option['updated_at']))}</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button(f"Open Chat → {option['customer_phone']}", key=f"chat_{option['id']}"):
            st.session_state.selected_escalated_chat_id = option["id"]
            st.rerun()

    st.markdown("<hr style='border-top:1px solid #E5E7EB; margin:1.5rem 0;'>", unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:1.3rem;font-weight:700;margin-bottom:0.5rem;'>Conversation with {_safe_html_text(selected['customer_phone'])}</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='color:#6B7280;'>Status: <span style='color:#B42318;font-weight:700;'>{_safe_html_text(selected['status'])}</span> • Last updated: {_safe_html_text(selected['updated_at'])}</div>", unsafe_allow_html=True)

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

        st.markdown(
            f"<div class='{bubble_class}'><strong>{sender_name}:</strong> "
            f"{html.escape(str(message.text))}</div>",
            unsafe_allow_html=True,
        )

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
    missing_configuration = settings.missing_required_configuration()
    if missing_configuration:
        st.warning(
            "Some features are unavailable until deployment settings are configured: "
            + ", ".join(missing_configuration)
        )

    if "role" not in st.session_state:
        _show_role_selection_page()
        return

    if st.session_state.role == "customer":
        if (
            st.session_state.get("customer_id")
            or st.session_state.get("customer_guest")
        ):
            _render_customer_interface()
        else:
            if st.session_state.get("auth_mode") == "signup":
                _show_signup_page()
            else:
                _show_login_page()
        return

    if "auth_mode" not in st.session_state:
        st.session_state.auth_mode = "login"

    if "business_id" not in st.session_state:
        if st.session_state.auth_mode == "signup":
            _show_signup_page()
        else:
            _show_login_page()
        return

    operations_items = ["🛒 Orders", "📦 Products & Stock"]
    workspace_items = [
        "🏠 Dashboard",
        "💬 Customer Chats",
        "🔔 Notifications",
        "🤖 AI Business Hub",
        "📊 Analytics & Reports",
        "⚙️ Settings",
    ]
    nav_items = workspace_items[:1] + operations_items + workspace_items[1:]
    legacy_pages = {
        "📦 Products": "📦 Products & Stock",
        "📋 Inventory": "📦 Products & Stock",
        "📚 Knowledge Base": "🤖 AI Business Hub",
        "🤖 AI Business Insights": "🤖 AI Business Hub",
        "🚪 Logout": "⚙️ Settings",
    }
    legacy_page = st.session_state.get("dashboard_page")
    if legacy_page in legacy_pages:
        st.session_state.dashboard_page = legacy_pages[legacy_page]
        if legacy_page == "📚 Knowledge Base":
            st.session_state.business_hub_default_tab = "Shop Knowledge"
        elif legacy_page == "🤖 AI Business Insights":
            st.session_state.business_hub_default_tab = "AI Insights"
    if st.session_state.get("dashboard_page") not in nav_items:
        st.session_state.dashboard_page = nav_items[0]
    requested_page = st.session_state.pop("requested_dashboard_page", None)
    if requested_page in legacy_pages:
        st.session_state.dashboard_page = legacy_pages[requested_page]
        if requested_page == "📚 Knowledge Base":
            st.session_state.business_hub_default_tab = "Shop Knowledge"
        elif requested_page == "🤖 AI Business Insights":
            st.session_state.business_hub_default_tab = "AI Insights"
    elif requested_page in nav_items:
        st.session_state.dashboard_page = requested_page
    current_page = st.session_state.dashboard_page
    st.session_state.operations_page = (
        current_page if current_page in operations_items else None
    )
    st.session_state.workspace_page = (
        current_page if current_page in workspace_items else None
    )
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
    st.sidebar.markdown("**Operations**")
    st.sidebar.radio(
        "Operations",
        operations_items,
        key="operations_page",
        index=None,
        label_visibility="collapsed",
        on_change=_sync_dashboard_page,
        args=("operations_page",),
    )
    st.sidebar.markdown("**Workspace**")
    st.sidebar.radio(
        "Workspace",
        workspace_items,
        key="workspace_page",
        index=None,
        label_visibility="collapsed",
        on_change=_sync_dashboard_page,
        args=("workspace_page",),
    )
    page = st.session_state.dashboard_page

    if page == "🏠 Dashboard":
        _render_dashboard_home_page()
    elif page == "📊 Analytics & Reports":
        _render_analytics_page()
    elif page == "📦 Products & Stock":
        _render_products_page()
    elif page == "🛒 Orders":
        _render_orders_page()
    elif page == "💬 Customer Chats":
        _render_customer_chats_page()
    elif page == "🔔 Notifications":
        _render_notifications_page()
    elif page == "🤖 AI Business Hub":
        _render_ai_business_hub_page()
    elif page == "⚙️ Settings":
        _render_profile_page()


if __name__ == "__main__":
    main()
