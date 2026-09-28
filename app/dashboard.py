import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from app.business.register_business import BusinessManager
from app.database import (
    Business,
    Conversation,
    ConversationStatus,
    Message,
    MessageSender,
    Order,
    configure_database,
    get_session,
)
from app.utils.auth import hash_password, verify_password


configure_database()


def _apply_dashboard_theme():
    st.markdown(
        """
        <style>
            :root {
                --bg: #f4f7ff;
                --panel: #ffffff;
                --panel-alt: #eef3ff;
                --primary: #4f46e5;
                --primary-strong: #312e81;
                --accent: #14b8a6;
                --accent-soft: #d8fff8;
                --amber: #f59e0b;
                --text: #172033;
                --muted: #5d6a85;
                --border: rgba(91, 105, 139, 0.18);
                --shadow: rgba(79, 70, 229, 0.12);
            }

            .stApp {
                background: linear-gradient(180deg, #f7f8ff 0%, #eef7ff 100%);
                color: var(--text);
            }

            .block-container {
                padding-top: 2rem;
                padding-bottom: 3rem;
            }

            [data-testid="stSidebar"] {
                background: linear-gradient(180deg, #1c163e 0%, #2a245d 100%);
                border-right: 1px solid rgba(255,255,255,0.1);
            }

            [data-testid="stSidebar"] .stMarkdown {
                color: white;
            }

            .brand-header {
                display: flex;
                align-items: center;
                gap: 0.8rem;
                padding: 0.6rem 0.75rem 1rem;
                margin-bottom: 0.8rem;
                border-bottom: 1px solid rgba(255,255,255,0.12);
            }

            .brand-icon {
                width: 42px;
                height: 42px;
                border-radius: 12px;
                display: flex;
                align-items: center;
                justify-content: center;
                background: linear-gradient(135deg, #7c3aed, #14b8a6);
                font-size: 1.4rem;
                box-shadow: 0 12px 30px rgba(20,184,166,0.28);
            }

            .brand-title {
                font-weight: 800;
                letter-spacing: 0.04em;
                font-size: 1.1rem;
                color: white;
            }

            .brand-subtitle {
                display: block;
                font-size: 0.72rem;
                letter-spacing: 0.12em;
                text-transform: uppercase;
                color: rgba(255,255,255,0.72);
            }

            .nav-pill {
                padding: 0.5rem 0.65rem;
                border-radius: 10px;
                margin: 0.25rem 0;
                color: rgba(255,255,255,0.9);
                font-weight: 600;
            }

            .section-heading {
                margin-top: 1rem;
                margin-bottom: 0.65rem;
                color: var(--primary-strong);
                font-size: 0.78rem;
                font-weight: 800;
                letter-spacing: 0.12em;
                text-transform: uppercase;
            }

            .hero-panel {
                padding: 1.6rem 1.4rem;
                border-radius: 20px;
                background: linear-gradient(135deg, rgba(79,70,229,0.12), rgba(20,184,166,0.08));
                border: 1px solid rgba(79,70,229,0.16);
                margin-bottom: 1rem;
            }

            .hero-title {
                font-size: clamp(2rem, 4vw, 3rem);
                font-weight: 900;
                background: linear-gradient(90deg, var(--primary), var(--accent));
                -webkit-background-clip: text;
                background-clip: text;
                color: transparent;
                margin: 0;
                line-height: 1.1;
            }

            .hero-subtitle {
                color: var(--muted);
                font-size: 1rem;
                margin-top: 0.45rem;
            }

            .card-shell {
                background: rgba(255,255,255,0.8);
                border: 1px solid var(--border);
                border-radius: 18px;
                padding: 1.2rem;
                box-shadow: 0 14px 36px var(--shadow);
            }

            .metric-card {
                background: linear-gradient(145deg, #ffffff, #f2f5ff);
                border: 1px solid rgba(79,70,229,0.12);
                border-radius: 18px;
                padding: 1rem 1.1rem;
                box-shadow: 0 12px 24px rgba(79,70,229,0.08);
                height: 100%;
            }

            .metric-label {
                color: var(--muted);
                font-size: 0.74rem;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
            }

            .metric-value {
                font-size: 2rem;
                font-weight: 900;
                color: var(--primary-strong);
                margin: 0.3rem 0 0;
            }

            .metric-accent {
                display: inline-block;
                margin-top: 0.45rem;
                padding: 0.3rem 0.5rem;
                border-radius: 999px;
                background: var(--accent-soft);
                color: #0f766e;
                font-size: 0.7rem;
                font-weight: 700;
            }

            .stButton > button {
                background: linear-gradient(135deg, var(--primary), #6d5ef5);
                color: white;
                border: none;
                border-radius: 10px;
                font-weight: 700;
                padding: 0.55rem 1rem;
            }

            .stButton > button:hover {
                filter: brightness(1.04);
            }

            div[data-testid="stForm"] {
                background: #ffffff;
                border: 1px solid rgba(91, 105, 139, 0.18);
                border-radius: 18px;
                padding: 1rem;
                box-shadow: 0 12px 30px rgba(79,70,229,0.10);
            }

            .stTextInput > div > div > input {
                background: #ffffff !important;
                color: #172033 !important;
                border: 1px solid rgba(91, 105, 139, 0.28) !important;
                border-radius: 12px !important;
            }

            .stTextInput label,
            .stCheckbox label {
                color: #172033 !important;
                font-weight: 600 !important;
            }

            div[data-testid="stFormSubmitButton"] button {
                background: linear-gradient(135deg, #4f46e5 0%, #6d5ef5 100%) !important;
                color: #ffffff !important;
                border: none !important;
                border-radius: 12px !important;
                font-weight: 800 !important;
                font-size: 0.95rem !important;
                padding: 0.7rem 1.4rem !important;
                width: 100% !important;
                box-shadow: 0 10px 24px rgba(79, 70, 229, 0.22) !important;
            }

            div[data-testid="stFormSubmitButton"] button:hover {
                filter: brightness(1.04) !important;
            }

            .stDataFrame {
                border-radius: 16px;
                overflow: hidden;
                box-shadow: 0 12px 24px rgba(15, 23, 42, 0.06);
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
    st.markdown("<div class='hero-panel'>", unsafe_allow_html=True)
    st.markdown("<div class='hero-title'>SMART BIZ AI</div>", unsafe_allow_html=True)
    st.markdown("<div class='hero-subtitle'>Create your shop account and manage orders with confidence.</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_center:
        st.markdown("<div class='section-heading'>Sign Up</div>", unsafe_allow_html=True)
        with st.form("signup_form"):
            name = st.text_input("Business name")
            category = st.text_input("Category")
            address = st.text_input("Address")
            owner_phone = st.text_input("Owner phone")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create account")

    if submitted:
        if not all([name, category, username, password]):
            st.error("Please fill in all required fields.")
            return

        if password != confirm_password:
            st.error("Passwords do not match.")
            return

        session = get_session()
        try:
            existing_business = (
                session.query(Business)
                .filter(Business.username == username)
                .first()
            )
            if existing_business:
                st.error("That username is already taken. Please choose a different one.")
                return

            manager = BusinessManager()
            business = manager.register_business(
                name=name,
                category=category,
                address=address or None,
                owner_phone=owner_phone or None,
                username=username,
                password_hash=hash_password(password),
            )
            st.session_state.business_id = business["business_id"]
            st.success("Account created successfully.")
            st.rerun()
        except Exception:
            st.error("Unable to create your account right now. Please try again.")
        finally:
            session.close()

    if st.button("Back to login"):
        st.session_state.auth_mode = "login"
        st.rerun()


def _load_saved_login():
    default = {"username": "", "password": "", "remember": False}
    saved = st.session_state.get("remembered_login", default)
    if not isinstance(saved, dict):
        return default
    return {
        "username": saved.get("username", ""),
        "password": saved.get("password", ""),
        "remember": bool(saved.get("remember", False)),
    }


def _show_login_page():
    saved_login = _load_saved_login()

    st.markdown("<div class='hero-panel'>", unsafe_allow_html=True)
    st.markdown("<div class='hero-title'>SMART BIZ AI</div>", unsafe_allow_html=True)
    st.markdown("<div class='hero-subtitle'>AI-powered business operations for smarter day-to-day shop management.</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_center:
        st.markdown("<div class='section-heading'>Shopkeeper Login</div>", unsafe_allow_html=True)
        with st.form("login_form"):
            username = st.text_input("Username", value=saved_login.get("username", ""), key="login_username")
            password = st.text_input("Password", type="password", value=saved_login.get("password", ""), key="login_password")
            remember_me = st.checkbox("Remember me", value=bool(saved_login.get("remember", False)))
            submitted = st.form_submit_button("Login")

    if submitted:
        if not username or not password:
            st.error("Please enter both username and password.")
            return

        session = get_session()
        try:
            business = (
                session.query(Business)
                .filter(Business.username == username)
                .first()
            )

            if business and business.password_hash and verify_password(password, business.password_hash):
                st.session_state.business_id = business.business_id
                st.session_state.auth_mode = "login"
                st.session_state.remembered_login = {
                    "username": username if remember_me else "",
                    "password": password if remember_me else "",
                    "remember": bool(remember_me),
                }
                st.rerun()
            else:
                st.error("Invalid username or password.")
                st.info("No account yet? Sign up to create one.")
        except Exception:
            st.error("Unable to log in right now. Please try again.")
        finally:
            session.close()

    if st.button("Create a new account"):
        st.session_state.auth_mode = "signup"
        st.rerun()


def _logout():
    st.session_state.clear()
    st.rerun()


def _render_orders_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view orders.")
        return

    session = get_session()
    try:
        orders = (
            session.query(Order)
            .filter(Order.business_id == business_id)
            .order_by(Order.created_at.desc())
            .all()
        )

        status_options = ["all"] + sorted({order.status for order in orders if order.status})
        selected_status = st.selectbox("Filter by status", status_options)

        filtered_orders = [
            order
            for order in orders
            if selected_status == "all" or order.status == selected_status
        ]

        if not filtered_orders:
            st.info("No orders yet")
            return

        pending_orders = sum(
            1
            for order in filtered_orders
            if order.status and order.status.lower() not in {"completed", "delivered", "resolved", "cancelled"}
        )

        table_data = [
            {
                "customer_phone": order.customer_phone,
                "items": _items_to_text(order.items),
                "status": order.status,
                "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else "",
            }
            for order in filtered_orders
        ]

        st.markdown("<div class='section-heading'>Overview</div>", unsafe_allow_html=True)
        col_1, col_2, col_3 = st.columns(3)
        with col_1:
            st.markdown("<div class='metric-card'><div class='metric-label'>Total Orders</div><div class='metric-value'>%s</div><div class='metric-accent'>Live</div></div>" % len(filtered_orders), unsafe_allow_html=True)
        with col_2:
            st.markdown("<div class='metric-card'><div class='metric-label'>Pending</div><div class='metric-value'>%s</div><div class='metric-accent'>Needs action</div></div>" % pending_orders, unsafe_allow_html=True)
        with col_3:
            st.markdown("<div class='metric-card'><div class='metric-label'>Current Filter</div><div class='metric-value'>%s</div><div class='metric-accent'>%s</div></div>" % (selected_status if selected_status != "all" else "All", "Showing" if selected_status == "all" else selected_status), unsafe_allow_html=True)

        st.markdown("<div class='section-heading'>Orders</div>", unsafe_allow_html=True)
        st.dataframe(table_data, use_container_width=True, hide_index=True)
    except Exception:
        st.error("Unable to load orders.")
    finally:
        session.close()


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

        st.markdown("<div class='section-heading'>Business Profile</div>", unsafe_allow_html=True)
        st.markdown("<div class='card-shell'>", unsafe_allow_html=True)
        st.text_input("Name", value=business.name or "", disabled=True)
        st.text_input("Address", value=business.address or "", disabled=True)
        st.text_input("Owner Phone", value=business.owner_phone or "", disabled=True)
        st.markdown("</div>", unsafe_allow_html=True)
    except Exception:
        st.error("Unable to load business profile.")
    finally:
        session.close()


def _render_escalated_chats_page():
    business_id = st.session_state.get("business_id")
    if not business_id:
        st.warning("Please log in to view escalated chats.")
        return

    session = get_session()
    try:
        conversations = (
            session.query(Conversation)
            .filter(
                Conversation.business_id == business_id,
                Conversation.status == ConversationStatus.ESCALATED,
            )
            .order_by(Conversation.updated_at.desc())
            .all()
        )

        options = []
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
        st.info("No escalated chats right now.")
        return

    labels = [
        f"{option['customer_phone']} • {option['status']} • {option['updated_at']} • {option['latest_message'][:80]}"
        for option in options
    ]

    selected_id = st.session_state.get("selected_escalated_chat_id")
    if selected_id is None or not any(option["id"] == selected_id for option in options):
        selected_id = options[0]["id"]
    st.session_state.selected_escalated_chat_id = selected_id

    selected = next(option for option in options if option["id"] == selected_id)
    selected_index = labels.index(
        f"{selected['customer_phone']} • {selected['status']} • {selected['updated_at']} • {selected['latest_message'][:80]}"
    )
    chosen_label = st.selectbox("Escalated conversations", options=labels, index=selected_index)
    selected = next(option for option in options if chosen_label.startswith(f"{option['customer_phone']} • "))

    st.subheader(f"Conversation with {selected['customer_phone']}")
    st.caption(f"Status: {selected['status']} • Last updated: {selected['updated_at']}")

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
            role = "user"
            sender_name = "Customer"
        elif message.sender == MessageSender.KEEPER:
            role = "assistant"
            sender_name = "Shopkeeper"
        else:
            role = "assistant"
            sender_name = "AI"

        with st.chat_message(role):
            st.markdown(f"**{sender_name}:** {message.text}")

    with st.form("keeper_reply_form"):
        reply = st.text_area("Reply to customer", key=f"keeper_reply_{selected['conversation'].id}")
        submitted = st.form_submit_button("Send reply")

    if submitted:
        if not reply or not reply.strip():
            st.warning("Please enter a reply before sending.")
        else:
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

                conversation.status = ConversationStatus.RESOLVED
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        sender=MessageSender.KEEPER,
                        text=reply.strip(),
                    )
                )
                session.commit()
                st.success("Reply saved and conversation marked as resolved.")
                st.session_state.selected_escalated_chat_id = None
                st.rerun()
            except Exception:
                session.rollback()
                st.error("Unable to save the reply right now.")
            finally:
                session.close()


def main():
    st.set_page_config(page_title="SMART BIZ AI", page_icon="🤖", layout="wide")
    _apply_dashboard_theme()

    if "auth_mode" not in st.session_state:
        st.session_state.auth_mode = "login"

    if "business_id" not in st.session_state:
        if st.session_state.auth_mode == "signup":
            _show_signup_page()
        else:
            _show_login_page()
        return

    st.sidebar.markdown(
        """
        <div class='brand-header'>
            <div class='brand-icon'>🤖</div>
            <div>
                <span class='brand-subtitle'>AI BUSINESS</span>
                <div class='brand-title'>SMART BIZ AI</div>
            </div>
        </div>
        <div class='nav-pill'>🛒 Orders</div>
        <div class='nav-pill'>👤 Business Profile</div>
        <div class='nav-pill'>🚨 Escalated Chats</div>
        """,
        unsafe_allow_html=True,
    )
    page = st.sidebar.radio("Navigation", ["🛒 Orders", "👤 Business Profile", "🚨 Escalated Chats"])

    if st.sidebar.button("Logout"):
        _logout()

    if page == "🛒 Orders":
        _render_orders_page()
    elif page == "👤 Business Profile":
        _render_profile_page()
    else:
        _render_escalated_chats_page()


if __name__ == "__main__":
    main()
