from collections import defaultdict
from datetime import date, datetime

from app.database import Conversation, ConversationStatus, Order, Product, get_session


def _as_int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _order_totals(order):
    total = 0
    quantity_total = 0
    item_rows = []
    for item in order.items if isinstance(order.items, list) else []:
        if not isinstance(item, dict):
            continue
        quantity = max(0, _as_int(item.get("quantity", 1), 1))
        price = max(0, _as_int(item.get("price")))
        item_total = price * quantity
        total += item_total
        quantity_total += quantity
        item_rows.append({
            "product_name": str(item.get("product_name") or "Unknown product"),
            "quantity": quantity,
            "price": price,
            "total": item_total,
            "customer_name": item.get("customer_name") or "Customer",
        })
    return total, quantity_total, item_rows


def get_business_metrics(business_id, *, today=None, date_from=None, date_to=None):
    """Calculate dashboard metrics using only records owned by the supplied business."""
    if not business_id:
        return {
            "total_orders": 0,
            "today_orders": 0,
            "month_orders": 0,
            "revenue": 0,
            "average_order_value": 0,
            "pending_orders": 0,
            "completed_orders": 0,
            "cancelled_orders": 0,
            "total_products": 0,
            "total_units": 0,
            "low_stock_products": 0,
            "out_of_stock_products": 0,
            "stock_value": 0,
            "customer_count": 0,
            "conversation_count": 0,
            "escalated_count": 0,
            "daily": [],
            "monthly": [],
            "orders_by_product": [],
            "orders_by_category": [],
            "recent_orders": [],
            "orders": [],
            "top_products": [],
            "products": [],
            "low_stock_items": [],
        }

    today = today or datetime.utcnow().date()
    if isinstance(today, datetime):
        today = today.date()
    session = get_session()
    try:
        orders = (
            session.query(Order)
            .filter(Order.business_id == business_id)
            .order_by(Order.created_at.desc())
            .all()
        )
        if isinstance(date_from, datetime):
            date_from = date_from.date()
        if isinstance(date_to, datetime):
            date_to = date_to.date()
        if date_from or date_to:
            orders = [
                order for order in orders
                if order.created_at is not None
                and (date_from is None or order.created_at.date() >= date_from)
                and (date_to is None or order.created_at.date() <= date_to)
            ]
        products = (
            session.query(Product)
            .filter(Product.business_id == business_id, Product.is_active.is_(True))
            .all()
        )
        conversations = (
            session.query(Conversation)
            .filter(Conversation.business_id == business_id)
            .all()
        )

        products_by_name = {product.product_name.casefold(): product for product in products}
        order_count_by_product = defaultdict(int)
        quantity_by_product = defaultdict(int)
        revenue_by_product = defaultdict(int)
        category_orders = defaultdict(int)
        category_revenue = defaultdict(int)
        order_rows = []
        daily_values = defaultdict(lambda: {"orders": 0, "revenue": 0})
        monthly_values = defaultdict(lambda: {"orders": 0, "revenue": 0})
        revenue = 0
        today_orders = 0
        month_orders = 0
        pending_orders = 0
        completed_orders = 0
        cancelled_orders = 0

        for order in orders:
            order_total, _, items = _order_totals(order)
            status = str(order.status or "NEW").upper()
            order_date = order.created_at.date() if order.created_at else None
            if status not in {"CANCELLED", "CANCELED"}:
                revenue += order_total
            if order_date == today:
                today_orders += 1
            if order_date and order_date.year == today.year and order_date.month == today.month:
                month_orders += 1

            if status in {"NEW", "CONFIRMED", "PROCESSING", "READY"}:
                pending_orders += 1
            elif status in {"COMPLETED", "DELIVERED", "RESOLVED"}:
                completed_orders += 1
            elif status in {"CANCELLED", "CANCELED"}:
                cancelled_orders += 1

            countable_total = order_total if status not in {"CANCELLED", "CANCELED"} else 0
            if order_date:
                daily_values[order_date]["orders"] += 1
                daily_values[order_date]["revenue"] += countable_total
                month_key = f"{order_date.year:04d}-{order_date.month:02d}"
                monthly_values[month_key]["orders"] += 1
                monthly_values[month_key]["revenue"] += countable_total

            for item in items:
                product_name = item["product_name"]
                key = product_name.casefold()
                if status not in {"CANCELLED", "CANCELED"}:
                    quantity_by_product[product_name] += item["quantity"]
                    order_count_by_product[product_name] += 1
                    revenue_by_product[product_name] += item["total"]
                    product = products_by_name.get(key)
                    category = product.category if product and product.category else "Uncategorized"
                    category_orders[category] += item["quantity"]
                    category_revenue[category] += item["total"]

            order_rows.append({
                "order_id": order.id,
                "customer_name": items[0]["customer_name"] if items else "Customer",
                "customer_phone": order.customer_phone,
                "items": items,
                "product_name": ", ".join(item["product_name"] for item in items) or "No items",
                "quantity": sum(item["quantity"] for item in items),
                "total": order_total,
                "status": status,
                "created_at": order.created_at,
            })

        low_stock_items = []
        inventory_rows = []
        total_units = 0
        stock_value = 0
        low_stock_count = 0
        out_of_stock_count = 0
        for product in products:
            stock = max(0, _as_int(product.stock_quantity))
            threshold = max(0, _as_int(getattr(product, "low_stock_threshold", 2), 2))
            inventory_rows.append({
                "id": product.id,
                "product_name": product.product_name,
                "category": product.category or "Uncategorized",
                "brand": product.brand or "",
                "price": max(0, _as_int(product.price)),
                "stock_quantity": stock,
                "low_stock_threshold": threshold,
                "availability": product.availability or ("Out of Stock" if stock == 0 else "In Stock"),
            })
            total_units += stock
            stock_value += stock * max(0, _as_int(product.price))
            if stock == 0:
                out_of_stock_count += 1
            elif stock <= threshold:
                low_stock_count += 1
                low_stock_items.append({
                    "product_name": product.product_name,
                    "stock_quantity": stock,
                    "threshold": threshold,
                    "category": product.category,
                })

        daily = []
        for offset in range(6, -1, -1):
            day = today.fromordinal(today.toordinal() - offset)
            values = daily_values[day]
            daily.append({"date": day.isoformat(), **values})

        monthly = []
        year, month = today.year, today.month
        for offset in range(5, -1, -1):
            month_index = year * 12 + month - 1 - offset
            target_year, month_zero = divmod(month_index, 12)
            key = f"{target_year:04d}-{month_zero + 1:02d}"
            monthly.append({"month": key, **monthly_values[key]})

        sales_rows = [
            {
                "product_name": name,
                "orders": order_count_by_product[name],
                "quantity": quantity_by_product[name],
                "revenue": revenue_by_product[name],
            }
            for name in order_count_by_product
        ]
        sales_rows.sort(key=lambda row: (-row["quantity"], -row["revenue"], row["product_name"].casefold()))
        category_rows = [
            {"category": name, "quantity": quantity, "revenue": category_revenue[name]}
            for name, quantity in category_orders.items()
        ]
        category_rows.sort(key=lambda row: (-row["quantity"], row["category"].casefold()))
        inventory_rows.sort(key=lambda row: row["product_name"].casefold())
        customers = {conversation.customer_phone for conversation in conversations if conversation.customer_phone}
        escalated_count = sum(conversation.status == ConversationStatus.ESCALATED for conversation in conversations)

        return {
            "total_orders": len(orders),
            "today_orders": today_orders,
            "month_orders": month_orders,
            "revenue": revenue,
            "average_order_value": round(revenue / max(1, len([order for order in orders if str(order.status or "").upper() not in {"CANCELLED", "CANCELED"}]))) if revenue else 0,
            "pending_orders": pending_orders,
            "completed_orders": completed_orders,
            "cancelled_orders": cancelled_orders,
            "total_products": len(products),
            "total_units": total_units,
            "low_stock_products": low_stock_count,
            "out_of_stock_products": out_of_stock_count,
            "stock_value": stock_value,
            "customer_count": len(customers),
            "conversation_count": len(conversations),
            "escalated_count": escalated_count,
            "daily": daily,
            "monthly": monthly,
            "orders_by_product": sales_rows,
            "orders_by_category": category_rows,
            "recent_orders": order_rows[:8],
            "orders": order_rows,
            "top_products": sales_rows[:5],
            "products": inventory_rows,
            "low_stock_items": low_stock_items,
        }
    finally:
        session.close()