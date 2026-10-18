import json
import re
from datetime import datetime

from app.agent.memory import ConversationMemory
from app.agent.tools import AgentTools
from app.database import (
    Conversation,
    ConversationStatus,
    Message,
    MessageSender,
    Order,
    get_session,
)
from app.llm.llm import LLM
from app.notifications.notification_service import create_notification
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.utils.logger import logger


class AIAgent:


    def __init__(self):

        self.llm = LLM()

        self.memory = ConversationMemory()

        self.tools = AgentTools()

    def chat_marketplace(self, user_query, history=None):
        """Answer cross-shop customer questions from current marketplace records."""
        query = (user_query or "").strip()
        if not query:
            raise ValueError("A customer question is required.")

        catalog = self.tools.get_marketplace_catalog()
        stop_words = {
            "a", "an", "and", "are", "according", "best", "buy", "can", "do", "for",
            "find", "from", "give", "good", "help", "i", "is", "me", "my", "of", "on",
            "please", "recommend", "reviews", "review", "shop", "shops", "should", "the",
            "to", "want", "which", "why", "with", "you", "your", "user", "users",
        }
        terms = [
            term for term in re.findall(r"[a-z0-9]+", query.casefold())
            if len(term) > 1 and term not in stop_words
        ]
        shopping_intent = bool(
            re.search(r"\b(?:buy|purchase|shop|shops|best|recommend|reviews?|ratings?|compare)\b", query, re.I)
            or re.search(r"\b(?:price|cost|stock|available|availability|product|products)\b", query, re.I)
        )

        matches = []
        if terms:
            for product in catalog:
                name_category = " ".join(str(product.get(key) or "") for key in (
                    "product_name", "category", "brand", "shop_name", "shop_category",
                )).casefold()
                full_text = " ".join((
                    name_category,
                    str(product.get("description") or "").casefold(),
                ))
                name_words = set(re.findall(r"[a-z0-9]+", name_category))
                all_words = set(re.findall(r"[a-z0-9]+", full_text))
                name_hits = sum(
                    any(word == term or (len(term) >= 4 and word.startswith(term)) for word in name_words)
                    for term in terms
                )
                all_hits = sum(
                    any(word == term or (len(term) >= 4 and word.startswith(term)) for word in all_words)
                    for term in terms
                )
                if all_hits:
                    matches.append((name_hits, all_hits, product))

        if shopping_intent:
            if not matches:
                searched_for = " ".join(terms) or "that item"
                return (
                    f"I couldn't find a current shop listing matching {searched_for!r}. "
                    "I don't have verified customer reviews or ratings to compare. "
                    "Try a different product name or category and I can check the closest listed options."
                )

            matches.sort(
                key=lambda item: (
                    item[0],
                    item[1],
                    item[2]["stock_quantity"] > 0,
                    item[2]["recorded_orders"],
                    -(item[2]["price"] if item[2]["price"] is not None else 10**12),
                ),
                reverse=True,
            )
            selected = [item[2] for item in matches[:3]]
            lines = []
            for index, product in enumerate(selected):
                price = f"₹{product['price']:,}" if product.get("price") is not None else "price not listed"
                stock = (
                    f"In stock ({product['stock_quantity']})"
                    if product["stock_quantity"] > 0
                    else "currently out of stock"
                )
                line = (
                    f"{index + 1}. {product['product_name']} at {product['shop_name']} "
                    f"({product.get('shop_address') or 'location not listed'}) — {price}; {stock}."
                )
                if product["recorded_orders"]:
                    line += f" {product['recorded_orders']} recorded order(s)."
                lines.append(line)

            leading = selected[0]
            leading_stock = (
                f"currently in stock ({leading['stock_quantity']} unit(s))"
                if leading["stock_quantity"] > 0
                else "currently out of stock"
            )
            leading_orders = leading["recorded_orders"]
            answer = (
                "I don't have verified customer reviews or star ratings to rank these shops. "
                f"{leading['shop_name']} is the strongest current match because its listing matches "
                f"your request and is {leading_stock}"
            )
            if leading_orders:
                answer += f", with {leading_orders} recorded order(s)"
            answer += (
                ". Recorded orders indicate purchases, not customer satisfaction. "
                "This recommendation compares available listing data, not customer reviews."
            )
            if len(lines) > 1:
                answer += "\n\nOther matching listings:\n" + "\n".join(lines[1:])
            answer += "\n\n" + lines[0]
            return answer

        payload = {
            "question": query,
            "current_marketplace_catalog": catalog,
            "evidence_limits": {
                "customer_reviews_or_ratings": "not collected",
                "popularity": "recorded non-cancelled order counts only; not a satisfaction score",
                "price_and_stock": "current product fields from the application catalog",
            },
        }
        messages = [{
            "role": "system",
            "content": (
                "You are SmartBiz's helpful customer assistant. Answer the user's question directly. "
                "Use current_marketplace_catalog as the only source for claims about shops, products, "
                "prices, stock, shop locations, popularity, and policies. Never invent listings, reviews, "
                "ratings, sales, product attributes, or shop facts. The app does not collect customer "
                "reviews or ratings; when asked for them, say so plainly and offer comparison using the "
                "listed products, current stock/prices, and recorded orders, explicitly noting that orders "
                "are not reviews. If no relevant listing is supplied, do not recommend an unrelated item; "
                "say you could not find a current matching listing and ask what alternative category or "
                "location the customer wants. For general questions unrelated to marketplace facts, answer "
                "using general knowledge and distinguish it from verified SmartBiz listing information. "
                "Be concise, accurate, and clear about uncertainty."
            ),
        }]
        for item in (history or [])[-8:]:
            if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": json.dumps(payload, default=str)})
        response = self.llm.generate_response(messages, temperature=0.2, max_tokens=800)
        if not response or response.startswith(("Sorry, something went wrong", "Agent Error:")):
            return (
                "I can't generate a general answer right now. I can still compare live shop listings "
                "when you ask about a specific product, category, price, or availability."
            )
        return response

    def generate_business_insights(self, business_id, metrics):
        if not business_id:
            raise ValueError("business_id is required.")

        try:
            knowledge_context = self.tools.get_business_context(
                business_id,
                "business policies, product descriptions, and customer service knowledge",
            )
        except Exception as exc:
            logger.warning("Business insight RAG context unavailable for %s: %s", business_id, exc)
            knowledge_context = None
        user_payload = {
            "verified_metrics": metrics,
            "business_knowledge_context": knowledge_context or "No relevant business knowledge found.",
        }
        response = self.llm.generate_response(
            [
                {
                    "role": "system",
                    "content": (
                        "Analyze only the supplied verified metrics and business knowledge. "
                        "Never invent or alter counts, revenue, stock, prices, customer facts, or dates. "
                        "Knowledge context may explain policies or product context, but is not a source "
                        "for live numeric values. Return only a JSON object with keys summary, "
                        "order_insights, inventory_insights, customer_insights, operational_issues, "
                        "suggested_actions. Use a string for summary and arrays of concise strings for "
                        "all other keys. State insufficient data when evidence is absent."
                    ),
                },
                {"role": "user", "content": json.dumps(user_payload, default=str)},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        if not response or response.startswith("Sorry, something went wrong"):
            raise RuntimeError("The configured LLM could not generate business insights.")

        content = response.strip()
        if content.startswith("```"):
            content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        insights = json.loads(content)
        required = {
            "summary": str,
            "order_insights": list,
            "inventory_insights": list,
            "customer_insights": list,
            "operational_issues": list,
            "suggested_actions": list,
        }
        for key, expected_type in required.items():
            if not isinstance(insights.get(key), expected_type):
                raise ValueError(f"AI insight output has an invalid {key} field.")
            if key != "summary" and any(not isinstance(item, str) for item in insights[key]):
                raise ValueError(f"AI insight output has invalid items in {key}.")
        return insights

    @staticmethod
    def _is_order_intent(message, has_product_reference=False):
        text = re.sub(r"\s+", " ", (message or "").casefold()).strip()
        if not text or re.search(r"\b(?:order status|track(?:ing)? (?:an? )?order|order history)\b", text):
            return False

        explicit_patterns = (
            r"\b(?:i want to|i would like to|i'd like to)\s+(?:buy|purchase|order)\b",
            r"\b(?:place|put in)\s+(?:an?\s+)?order\b",
            r"\b(?:buy|purchase|order)\s+(?:it|this|that|these|those|[\w])",
            r"\b(?:i'll|i will)\s+take\s+(?:it|this|that|these|those|one)\b",
            r"\b(?:i want|i would like|i'd like)\s+(?:it|this|that|these|those)\b",
        )
        if any(re.search(pattern, text) for pattern in explicit_patterns):
            return True

        if re.search(
            r"\b(?:know|learn|compare|details?|information|specs?|specifications?|"
            r"price|cost|whether|if)\b",
            text,
        ):
            return False
        return bool(has_product_reference and re.search(r"\b(?:i want|i would like|i'd like)\b", text))

    @staticmethod
    def _normalize_payment_method(message):
        text = re.sub(r"[^a-z0-9\s-]", " ", (message or "").casefold())
        text = re.sub(r"\s+", " ", text).strip()
        if re.search(r"\b(?:emi|installments?|instalments?)\b", text):
            return "EMI"
        if re.search(r"\b(?:cod|cash|cash on delivery|pay cash on delivery)\b", text):
            return "COD"
        return None

    @staticmethod
    def _extract_order_quantity(message):
        text = (message or "").casefold()
        quantity_words = {
            "one": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        }
        number = r"(?:\d+|" + "|".join(quantity_words) + r")"
        match = re.search(rf"^\s*(?:order|buy|purchase)\s+({number})\b", text)
        if not match:
            match = re.search(rf"\b({number})\s+(?:units?|pieces?|pcs?|items?|phones?)\b", text)
        if not match:
            match = re.search(rf"\b(?:i want|i would like|i'd like)\s+({number})\s+(?:of\s+)?(?:these|those|them|it|this)\b", text)
        if not match:
            return None
        value = match.group(1)
        return int(value) if value.isdigit() else quantity_words[value]

    @staticmethod
    def _is_order_confirmation(message):
        text = re.sub(r"[^a-z\s']", " ", (message or "").casefold())
        text = re.sub(r"\s+", " ", text).strip()
        return text in {
            "yes", "yes order it", "yes place it", "yes place the order",
            "yes order the product", "yes confirm", "yes go ahead", "yes proceed",
            "yes do it", "yes please",
            "confirm", "confirm order", "place it", "place the order",
            "go ahead", "proceed", "okay", "ok", "sure", "do it",
        }

    @staticmethod
    def _is_order_cancellation(message):
        text = (message or "").casefold().replace("’", "'")
        text = re.sub(r"[^a-z\s']", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text in {
            "no", "no thanks", "cancel", "cancel order", "don't order",
            "no don't order", "no do not order", "do not order", "not now", "forget it", "i don't want it",
            "i do not want it",
        } or text.startswith(("no ", "cancel ", "not now"))

    @staticmethod
    def _is_order_status_query(message):
        text = re.sub(r"\s+", " ", (message or "").casefold()).strip()
        return bool(re.search(
            r"\b(?:order|purchase|parcel|package|shipment|delivery)\b.{0,50}"
            r"\b(?:status|track(?:ing)?|update|progress|shipped|dispatched|delivered|"
            r"arrived|reached|come|on the way|out for delivery|late|delayed|delay|"
            r"taking too long|not arrived|where|when)\b"
            r"|\b(?:where|when)\b.{0,24}\b(?:my|the)\s+"
            r"(?:order|purchase|parcel|package|shipment|delivery)\b"
            r"|\btrack\b.{0,24}\b(?:my|the)\s+"
            r"(?:order|parcel|package|shipment|delivery)\b"
            r"|\b(?:has|did|is|was)\b.{0,20}\b(?:my|the)\s+"
            r"(?:order|purchase|parcel|package|shipment|delivery)\b.{0,30}"
            r"\b(?:arrive|reach|come|deliver|ship)\w*\b",
            text,
        ))

    @staticmethod
    def _is_live_price_query(message):
        text = re.sub(r"\s+", " ", (message or "").casefold()).strip()
        return bool(
            re.search(
                r"\b(?:price|cost|rate|pricing|worth|expensive|cheap|cheapest|"
                r"affordable|priced|costlier)\b",
                text,
            )
            or re.search(r"\b(?:least|most) expensive\b|\b(?:lowest|highest) priced\b", text)
            or re.search(r"\bhow much\s+(?:is|for)\b", text)
            or re.search(r"\bhow much\s+(?:would|will|do|did)\s+i\s+pay\b", text)
            or re.search(r"\bwhat (?:would|will) i pay\b", text)
            or re.search(r"\bwhat does .{1,60} run\b", text)
            or re.search(r"\bgo(?:es)? for\b", text)
            or AIAgent._extract_live_price_limit(text) is not None
        )

    @staticmethod
    def _extract_live_price_limit(message):
        text = re.sub(r"\s+", " ", (message or "").casefold()).strip()
        match = re.search(
            r"\b(?:under|below|less than|up to|at most|no more than)\s*"
            r"(?:₹|rs\.?\s*)?(\d[\d,]*)\b",
            text,
        )
        return int(match.group(1).replace(",", "")) if match else None

    @staticmethod
    def _is_live_stock_query(message):
        text = re.sub(r"\s+", " ", (message or "").casefold()).strip()
        explicit_inventory_query = bool(re.search(
            r"\b(?:stock|availability|available|unavailable|"
            r"in stock|out of stock|sold out|left|remaining|carry|"
            r"keep in stock|in store|on hand)\b"
            r"|\bhow many\b.{0,40}\b(?:units?|pieces?|items?)\b",
            text,
        ))
        generic_inventory_query = bool(re.search(r"\b(?:do you have|have you got)\b", text))
        static_business_topic = bool(re.search(
            r"\b(?:delivery|shipping|warranty|return|refund|exchange|policy|"
            r"support|service|opening hours|business hours|contact|address)\b",
            text,
        ))
        return explicit_inventory_query or (generic_inventory_query and not static_business_topic)

    @staticmethod
    def _default_order_state():
        return {
            "selected_product": None,
            "selected_product_id": None,
            "quantity": 1,
            "payment_method": None,
            "order_intent": False,
            "awaiting_payment_method": False,
            "awaiting_order_confirmation": False,
            "awaiting_quantity_confirmation": False,
            "quoted_price": None,
        }

    @staticmethod
    def _resolve_product_mentions(message, products):
        normalized = re.sub(r"[^a-z0-9]+", " ", (message or "").casefold()).strip()
        if not normalized:
            return []
        query_tokens = set(normalized.split())
        exact_matches = []
        for product in products:
            name_normalized = re.sub(
                r"[^a-z0-9]+",
                " ",
                (product.get("product_name") or "").casefold(),
            ).strip()
            if name_normalized and name_normalized in normalized:
                exact_matches.append(product)
        if exact_matches:
            return exact_matches

        matches = []
        for product in products:
            name = product.get("product_name") or ""
            name_normalized = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
            if not name_normalized:
                continue
            name_tokens = set(name_normalized.split())
            matched_tokens = name_tokens & query_tokens
            if matched_tokens:
                matches.append((len(matched_tokens), product))
        if not matches:
            return []
        best_score = max(score for score, _ in matches)
        best = [product for score, product in matches if score == best_score]
        if len(best) == 1:
            return best
        # A model suffix such as "Ultra" must not make a shorter name match ambiguous
        # when the customer's query includes the distinguishing model token.
        shortest = min(len(re.sub(r"[^a-z0-9]+", " ", p["product_name"].casefold()).split()) for p in best)
        shortest_matches = [
            product for product in best
            if len(re.sub(r"[^a-z0-9]+", " ", product["product_name"].casefold()).split()) == shortest
        ]
        return shortest_matches if len(shortest_matches) == 1 else best

    def _save_order_state(self, business_id, conversation_id, state):
        self.memory.set_state("order_state", dict(state))
        if conversation_id is None:
            return
        session = get_session()
        try:
            conversation = session.query(Conversation).filter(
                Conversation.id == conversation_id,
                Conversation.business_id == business_id,
            ).first()
            if conversation is not None:
                conversation.order_state = dict(state)
                conversation.updated_at = datetime.utcnow()
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _handle_order_conversation(
        self,
        business_id,
        user_query,
        customer_phone,
        customer_name,
        customer_session_id,
        customer_address,
        conversation_id,
    ):
        if not hasattr(self.tools, "get_live_products"):
            return None

        state = self.memory.get_state("order_state") or self._default_order_state()
        if state.get("business_id") not in (None, business_id):
            state = self._default_order_state()
        state["business_id"] = business_id
        products = self.tools.get_live_products(business_id, limit=None)
        mentions = self._resolve_product_mentions(user_query, products)
        selected = mentions[0] if len(mentions) == 1 else None

        if selected:
            state["selected_product"] = selected["product_name"]
            state["selected_product_id"] = selected.get("id")
            self.memory.set_last_entity(selected["product_name"])
        elif not state.get("selected_product_id"):
            for role, content in reversed(self.memory.get_history()):
                if role != "user":
                    continue
                historical_mentions = self._resolve_product_mentions(content, products)
                if len(historical_mentions) == 1:
                    state["selected_product"] = historical_mentions[0]["product_name"]
                    state["selected_product_id"] = historical_mentions[0].get("id")
                    break

        product = next(
            (
                item for item in products
                if item.get("id") == state.get("selected_product_id")
                and item.get("product_name") == state.get("selected_product")
            ),
            None,
        )
        was_awaiting_payment = bool(state.get("awaiting_payment_method"))
        was_awaiting_order_confirmation = bool(state.get("awaiting_order_confirmation"))
        was_awaiting_quantity_confirmation = bool(state.get("awaiting_quantity_confirmation"))
        confirming_order = (
            was_awaiting_order_confirmation
            and self._is_order_confirmation(user_query)
        )
        order_intent = self._is_order_intent(
            user_query,
            has_product_reference=selected is not None,
        ) or bool(state.get("order_intent"))
        payment_method = self._normalize_payment_method(user_query)

        if (was_awaiting_payment or was_awaiting_order_confirmation or was_awaiting_quantity_confirmation) and self._is_order_cancellation(user_query):
            cleared = self._default_order_state()
            self._save_order_state(business_id, conversation_id, cleared)
            return "No problem. I haven't placed the order."

        if was_awaiting_quantity_confirmation and self._is_order_confirmation(user_query):
            state["awaiting_quantity_confirmation"] = False
            order_intent = True
        elif was_awaiting_quantity_confirmation:
            if payment_method:
                state["payment_method"] = payment_method
                self._save_order_state(business_id, conversation_id, state)
            quantity = max(1, int(state.get("quantity") or 1))
            unit_label = "unit" if quantity == 1 else "units"
            return f"Only {quantity} {unit_label} {'is' if quantity == 1 else 'are'} available. Would you like to order {quantity}?"

        elif confirming_order:
            order_intent = True

        elif was_awaiting_payment and payment_method:
            state["payment_method"] = payment_method
            state["awaiting_payment_method"] = False
            state["awaiting_order_confirmation"] = True
            if product is None:
                self._save_order_state(business_id, conversation_id, state)
                return "I can no longer find that product in this shop's active catalog."
            price = product.get("price")
            state["quoted_price"] = price
            self._save_order_state(business_id, conversation_id, state)
            price_text = f" is ₹{price:,}" if price is not None else " has no price listed"
            return f"{payment_method} selected. {product['product_name']}{price_text}. Shall I place the order?"

        pending = was_awaiting_payment or was_awaiting_order_confirmation or was_awaiting_quantity_confirmation
        preference = any(term in user_query.casefold() for term in (
            "sounds better", "seems better", " is better", "prefer", "choose", "pick", "go with",
            "like the", "better one", "rather have",
        ))
        if not (pending or order_intent or (selected is not None and preference)):
            self._save_order_state(business_id, conversation_id, state)
            return None

        if product is None:
            if order_intent or pending:
                if state.get("selected_product_id") is not None:
                    product_name = state.get("selected_product") or "That product"
                    self._save_order_state(business_id, conversation_id, self._default_order_state())
                    return f"Sorry, {product_name} is no longer available in this shop's active catalog."
                state.update({
                    "order_intent": True,
                    "awaiting_payment_method": False,
                    "awaiting_order_confirmation": False,
                    "awaiting_quantity_confirmation": False,
                })
                self._save_order_state(business_id, conversation_id, state)
                return "Which product would you like to order? Please tell me its name."
            self._save_order_state(business_id, conversation_id, state)
            return None

        if preference and not pending and not order_intent:
            state.update({
                "quantity": 1,
                "payment_method": None,
                "order_intent": False,
                "awaiting_payment_method": False,
                "awaiting_order_confirmation": False,
                "awaiting_quantity_confirmation": False,
                "quoted_price": product.get("price"),
            })
            self._save_order_state(business_id, conversation_id, state)
            price_text = f" is ₹{product['price']:,}" if product.get("price") is not None else " has no price listed"
            return f"{product['product_name']}{price_text}. Would you like to order it?"

        if not order_intent and not pending:
            self._save_order_state(business_id, conversation_id, state)
            return None

        if order_intent:
            state["order_intent"] = True
            requested_quantity = self._extract_order_quantity(user_query)
            if requested_quantity is not None:
                state["quantity"] = requested_quantity
            if payment_method:
                state["payment_method"] = payment_method

        if was_awaiting_payment and not payment_method and not order_intent:
            self._save_order_state(business_id, conversation_id, state)
            return "Please choose COD or EMI as the payment method."

        if was_awaiting_order_confirmation and payment_method and not confirming_order:
            state["payment_method"] = payment_method
            state["quoted_price"] = product.get("price") if product else state.get("quoted_price")
            self._save_order_state(business_id, conversation_id, state)
            if product is None:
                return "I can no longer find that product in this shop's active catalog."
            if product.get("price") is None:
                return f"{payment_method} selected, but {product['product_name']} has no price listed."
            return f"{payment_method} selected. {product['product_name']} is ₹{product['price']:,}. Shall I place the order?"

        if was_awaiting_order_confirmation and not self._is_order_confirmation(user_query) and not order_intent:
            self._save_order_state(business_id, conversation_id, state)
            return None

        if product.get("price") is None:
            state["awaiting_payment_method"] = False
            state["awaiting_order_confirmation"] = False
            state["awaiting_quantity_confirmation"] = False
            self._save_order_state(business_id, conversation_id, state)
            return f"{product['product_name']} has no price listed, so I can't place the order yet."

        quantity = max(1, int(state.get("quantity") or 1))
        stock = max(0, int(product.get("stock_quantity") or 0))
        if stock == 0:
            self._save_order_state(business_id, conversation_id, self._default_order_state())
            return f"Sorry, {product['product_name']} is currently out of stock."
        if quantity > stock:
            state["quantity"] = stock
            state["awaiting_payment_method"] = False
            state["awaiting_order_confirmation"] = False
            state["awaiting_quantity_confirmation"] = True
            self._save_order_state(business_id, conversation_id, state)
            unit_label = "unit" if stock == 1 else "units"
            return f"Only {stock} {unit_label} {'is' if stock == 1 else 'are'} currently available. Would you like to order {stock}?"

        if not state.get("payment_method"):
            state["awaiting_payment_method"] = True
            state["awaiting_order_confirmation"] = False
            state["awaiting_quantity_confirmation"] = False
            state["quoted_price"] = product.get("price")
            self._save_order_state(business_id, conversation_id, state)
            return f"Sure. {product['product_name']} is ₹{product['price']:,}. Which payment method would you prefer: COD or EMI?"

        state["awaiting_payment_method"] = False
        state["awaiting_quantity_confirmation"] = False
        if not was_awaiting_order_confirmation or (order_intent and not confirming_order):
            state["awaiting_order_confirmation"] = True
            state["quoted_price"] = product.get("price")
            self._save_order_state(business_id, conversation_id, state)
            if was_awaiting_order_confirmation and order_intent:
                # An explicit new purchase request still requires confirmation.
                pass
            price_suffix = f" is ₹{product['price']:,}"
            return f"{product['product_name']}{price_suffix} and {state['payment_method']} is selected. Shall I place the order?"

        if was_awaiting_order_confirmation and state.get("quoted_price") != product.get("price"):
            state["quoted_price"] = product.get("price")
            self._save_order_state(business_id, conversation_id, state)
            return f"The current price for {product['product_name']} is ₹{product['price']:,}. {state['payment_method']} is selected. Shall I place the order?"

        if customer_session_id and not customer_phone:
            self._save_order_state(business_id, conversation_id, state)
            return "Please add your phone number in Settings before placing this order, then confirm again."

        order = self.tools.create_order(
            business_id,
            customer_name=customer_name or "Customer",
            phone=customer_phone or "0000000000",
            product_name=product["product_name"],
            product_id=product.get("id"),
            quantity=quantity,
            customer_session_id=customer_session_id,
            customer_address=customer_address,
            payment_method=state["payment_method"],
            confirmed=True,
        )
        if order.get("error"):
            error = order["error"]
            if error.startswith("Only ") or "not enough stock" in error.casefold():
                refreshed = self.tools.get_live_products(business_id, limit=None)
                refreshed_product = next((item for item in refreshed if item.get("id") == state.get("selected_product_id")), None)
                if refreshed_product and refreshed_product.get("stock_quantity", 0) > 0:
                    state["quantity"] = refreshed_product["stock_quantity"]
                    state["awaiting_order_confirmation"] = False
                    state["awaiting_quantity_confirmation"] = True
                    self._save_order_state(business_id, conversation_id, state)
                    stock = state["quantity"]
                    return f"Only {stock} unit{'s' if stock != 1 else ''} {'are' if stock != 1 else 'is'} currently available. Would you like to order {stock}?"
            if "out of stock" in error.casefold() or "no longer available" in error.casefold():
                self._save_order_state(business_id, conversation_id, self._default_order_state())
            return error

        self._save_order_state(business_id, conversation_id, self._default_order_state())
        return (
            f"Order placed successfully for {order['product_name']} x{order['quantity']} "
            f"({order['status']}). Payment: {order['payment_method']}. Total: ₹{order['total']:,}"
        )

    @staticmethod
    def _is_explicit_human_request(message):
        text = (message or "").lower()
        triggers = [
            "talk to owner",
            "talk to the owner",
            "speak to owner",
            "talk to a person",
            "speak to a person",
            "talk to a human",
            "speak to a human",
            "talk to someone",
            "speak to someone",
            "human support",
            "customer service",
            "escalate",
            "need a human",
            "need the owner",
            "i need to speak to the owner",
            "please connect me to a person",
            "please connect me to the owner",
        ]
        return any(trigger in text for trigger in triggers)

    @staticmethod
    def _is_complaint_or_frustration(message):
        text = (message or "").lower()
        complaint_words = [
            "complaint",
            "complain",
            "frustrated",
            "angry",
            "annoyed",
            "upset",
            "disappointed",
            "very unhappy",
            "not happy",
            "terrible",
            "awful",
            "hate this",
            "bad experience",
            "this is ridiculous",
            "worst",
            "really upset",
        ]
        return any(word in text for word in complaint_words)

    @staticmethod
    def _should_escalate(user_query, context=None, generated_response=None):
        if not user_query:
            return False

        explicit_human_request = AIAgent._is_explicit_human_request(user_query)
        complaint = AIAgent._is_complaint_or_frustration(user_query)

        if context is None or context == "No business information found.":
            context = None

        if generated_response is not None and generated_response.strip() == "I don't have that information in the business knowledge base.":
            generated_response = generated_response.strip()

        return explicit_human_request or complaint

    def _handle_human_escalation(
        self,
        business_id,
        user_query,
        customer_phone=None,
        customer_session_id=None,
        conversation_id=None,
    ):
        session = get_session()
        try:
            query = session.query(Conversation).filter(Conversation.business_id == business_id)
            if conversation_id is not None:
                query = query.filter(Conversation.id == conversation_id)
            elif customer_session_id:
                query = query.filter(Conversation.customer_session_id == customer_session_id)
            else:
                query = query.filter(Conversation.customer_phone == customer_phone)
            conversation = query.order_by(Conversation.created_at.desc()).first()

            if conversation is None:
                conversation = Conversation(
                    business_id=business_id,
                    customer_session_id=customer_session_id,
                    customer_phone=customer_phone,
                    status=ConversationStatus.AI_HANDLING,
                )
                session.add(conversation)
                session.flush()

            if conversation.status not in {ConversationStatus.ESCALATED, ConversationStatus.KEEPER_HANDLING}:
                conversation.status = ConversationStatus.ESCALATED

            session.add(
                Message(
                    conversation_id=conversation.id,
                    sender=MessageSender.CUSTOMER,
                    text=user_query,
                )
            )
            session.commit()

            if conversation.status == ConversationStatus.ESCALATED:
                create_notification(
                    business_id,
                    "ESCALATED_CHAT",
                    "Customer Needs Assistance",
                    f"Customer: {customer_phone or 'Unknown'}\n\"{user_query}\"",
                    reference_type="CONVERSATION",
                    reference_id=str(conversation.id),
                )

            return {"signal": "awaiting_human", "reply": None}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _load_customer_conversation(self, business_id, customer_session_id, customer_phone, conversation_id):
        session = get_session()
        try:
            query = session.query(Conversation).filter(
                Conversation.business_id == business_id,
                Conversation.customer_session_id == customer_session_id,
            )
            if conversation_id is not None:
                query = query.filter(Conversation.id == conversation_id)
            conversation = query.order_by(Conversation.updated_at.desc()).first()
            if conversation is None:
                conversation = Conversation(
                    business_id=business_id,
                    customer_session_id=customer_session_id,
                    customer_phone=customer_phone,
                    status=ConversationStatus.AI_HANDLING,
                )
                session.add(conversation)
                session.commit()
                session.refresh(conversation)
            elif customer_phone and not conversation.customer_phone:
                conversation.customer_phone = customer_phone
                session.commit()

            messages = (
                session.query(Message)
                .filter(Message.conversation_id == conversation.id)
                .order_by(Message.timestamp.asc())
                .all()
            )
            return conversation.id, conversation.status, [
                ("user" if message.sender == MessageSender.CUSTOMER else "assistant", message.text)
                for message in messages
            ], conversation.order_state
        finally:
            session.close()

    def _persist_customer_exchange(self, business_id, conversation_id, user_query, response):
        session = get_session()
        try:
            conversation = (
                session.query(Conversation)
                .filter(Conversation.id == conversation_id, Conversation.business_id == business_id)
                .first()
            )
            if conversation is None:
                return
            session.add(Message(
                conversation_id=conversation.id,
                sender=MessageSender.CUSTOMER,
                text=user_query,
            ))
            if response:
                session.add(Message(
                    conversation_id=conversation.id,
                    sender=MessageSender.AI,
                    text=response,
                ))
            conversation.updated_at = datetime.utcnow()
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def chat(
        self,
        business_id,
        user_query,
        customer_phone=None,
        customer_session_id=None,
        conversation_id=None,
        customer_address=None,
        customer_name=None,
    ):

        try:
            customer_conversation_id = None
            if customer_session_id:
                customer_conversation_id, _, stored_history, stored_order_state = self._load_customer_conversation(
                    business_id,
                    customer_session_id,
                    customer_phone,
                    conversation_id,
                )
                self.memory.clear()
                self.memory.set_state(
                    "order_state",
                    stored_order_state if isinstance(stored_order_state, dict) else self._default_order_state(),
                )
                for role, content in stored_history:
                    self.memory.add_message(role, content)

            def customer_reply(reply_text):
                if customer_session_id and customer_conversation_id is not None:
                    self._persist_customer_exchange(
                        business_id,
                        customer_conversation_id,
                        user_query,
                        reply_text,
                    )
                return {"signal": "reply", "reply": reply_text}

            session = get_session()
            try:
                query = session.query(Conversation).filter(Conversation.business_id == business_id)
                if customer_conversation_id is not None:
                    query = query.filter(Conversation.id == customer_conversation_id)
                elif customer_session_id:
                    query = query.filter(Conversation.customer_session_id == customer_session_id)
                else:
                    query = query.filter(Conversation.customer_phone == customer_phone)
                conversation = query.order_by(Conversation.created_at.desc()).first()
                if conversation is not None and conversation.status in {ConversationStatus.ESCALATED, ConversationStatus.KEEPER_HANDLING}:
                    session.add(
                        Message(
                            conversation_id=conversation.id,
                            sender=MessageSender.CUSTOMER,
                            text=user_query,
                        )
                    )
                    session.commit()
                    return {"signal": "awaiting_human", "reply": None}
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

            if self._is_explicit_human_request(user_query) or self._is_complaint_or_frustration(user_query):
                return self._handle_human_escalation(
                    business_id,
                    user_query,
                    customer_phone,
                    customer_session_id,
                    customer_conversation_id,
                )

            if self._is_order_status_query(user_query):
                order_id_match = re.search(r"\b(?:order\s*(?:id|number)?\s*#?)([A-Za-z0-9-]{5,})\b", user_query, re.I)
                session = get_session()
                try:
                    query = session.query(Order).filter(Order.business_id == business_id)
                    if customer_session_id:
                        query = query.filter(Order.customer_session_id == customer_session_id)
                    else:
                        query = query.filter(Order.customer_phone == (customer_phone or "0000000000"))
                    if order_id_match:
                        query = query.filter(Order.id == order_id_match.group(1))
                    order = query.order_by(Order.created_at.desc()).first()
                finally:
                    session.close()
                if order is None:
                    return customer_reply("I couldn't find an order for this customer session.")
                return customer_reply(f"Your order {order.id} is currently {order.status}.")

            order_reply = self._handle_order_conversation(
                business_id,
                user_query,
                customer_phone,
                customer_name,
                customer_session_id,
                customer_address,
                customer_conversation_id,
            )
            if order_reply is not None:
                return customer_reply(order_reply)

            # Get previous conversation
            history = self.memory.get_history()

            # Resolve follow-up pronouns against the last referenced product entity
            def extract_entities_from_text(text):
                candidates = []

                # Match explicit product name lines
                for m in re.finditer(r"Product Name:\s*([A-Za-z0-9 \-\(\)\+\&']{2,80})", text, re.I):
                    candidates.append(m.group(1).strip())

                # Match TitleCase or ALLCAPS product names of 2-5 words
                for m in re.finditer(r"([A-Z0-9][A-Za-z0-9&\-\.]{1,}(?:\s+[A-Z0-9][A-Za-z0-9&\-\.]*){1,4})", text):
                    candidates.append(m.group(1).strip())

                blacklist = {"yes", "no", "the", "i", "we", "it", "this", "that", "they", "business", "contact", "address"}
                cleaned = [c for c in candidates if len(c) > 3 and c.split()[0].lower() not in blacklist]
                return cleaned

            def choose_best_entity(candidates):
                if not candidates:
                    return None
                candidates.sort(key=lambda s: len(s), reverse=True)
                return candidates[0]

            last_entity = self.memory.get_last_entity()
            if not last_entity:
                for msg in reversed(history):
                    ents = extract_entities_from_text(msg.get("content", ""))
                    ent = choose_best_entity(ents)
                    if ent:
                        last_entity = ent
                        break

            # If the current user query mentions a product explicitly, save that as the last entity
            user_entities = extract_entities_from_text(user_query)
            explicit_entity = choose_best_entity(user_entities)
            if explicit_entity:
                last_entity = explicit_entity
                self.memory.set_last_entity(last_entity)

            pronoun_pattern = re.compile(r"\b(it|this|that|they|its|this product|the product)\b", re.I)
            resolved_query = user_query
            if last_entity and pronoun_pattern.search(user_query):
                lower = user_query.strip().lower()
                if lower.startswith("why"):
                    resolved_query = f"Why is {last_entity} the best product?"
                elif "price" in lower:
                    resolved_query = f"What is the price of {last_entity}?"
                elif "ram" in lower:
                    resolved_query = f"How much RAM does {last_entity} have?"
                elif "good for coding" in lower or "good for programming" in lower or "is it good" in lower or "is this good" in lower or "good for" in lower:
                    resolved_query = f"Is {last_entity} good for coding and programming?"
                elif "available" in lower or lower.startswith("is it") or lower.startswith("is this"):
                    resolved_query = f"Is {last_entity} available and in stock?"
                elif lower.startswith("tell me more") or lower.startswith("what about") or lower.startswith("describe"):
                    resolved_query = f"Tell me more about {last_entity}."
                else:
                    resolved_query = f"{user_query.strip()} about {last_entity}"

            lowered_resolved = resolved_query.casefold()
            product_query = self._is_live_price_query(resolved_query) or self._is_live_stock_query(
                resolved_query
            ) or any(term in lowered_resolved for term in (
                "product", "products", "price", "how much", "stock", "in stock", "available",
                "availability", "phones", "phone", "mobile", "smartphone", "laptop", "headphone", "better",
                "recommend", "recommendation", "best", "compare", "comparison", "feature",
                "features", "specification", "specifications", "specs", "what do you have",
                "sell", "charger", "chargers", "accessory", "accessories", "usb-c",
                "camera", "photo", "picture", "screen", "display", "battery",
                "processor", "storage", "ram", "suitable", "good for",
                "does it have", "does this have", "does the", "will it",
            ))
            if product_query and hasattr(self.tools, "get_live_products"):
                comparison_query = any(term in lowered_resolved for term in (
                    "best", "better", "recommend", "recommendation", "compare", "comparison",
                ))
                all_catalog_query = any(term in lowered_resolved for term in (
                    "every product", "each product", "all product", "all the products",
                    "all their products", "all of their products", "each of their products",
                    "every item", "each item", "all items",
                ))
                list_query = (
                    any(phrase in lowered_resolved for phrase in (
                        "available products",
                        "products available",
                        "what products",
                        "list products",
                        "show products",
                        "products do you have",
                        "what do you sell",
                    ))
                    or (
                        re.search(r"\b(products?|items?)\b", lowered_resolved)
                        and any(term in lowered_resolved for term in ("available", "in stock", "show", "list", "what"))
                    )
                )
                list_query = list_query or all_catalog_query
                products = self.tools.get_live_products(
                    business_id,
                    limit=None if all_catalog_query or comparison_query else 100,
                    available_only=bool((list_query and not all_catalog_query) or comparison_query),
                )
                live_price_limit = self._extract_live_price_limit(resolved_query)
                if live_price_limit is not None:
                    products = [
                        product for product in products
                        if product.get("price") is not None
                        and product["price"] <= live_price_limit
                    ]
                    if not products:
                        return customer_reply(
                            f"No active products in this shop are listed at or below "
                            f"₹{live_price_limit:,}."
                        )
                normalized_query = re.sub(r"[^a-z0-9]+", " ", lowered_resolved).strip()
                explicit_matches = [
                    product for product in products
                    if re.sub(r"[^a-z0-9]+", " ", product["product_name"].casefold()).strip() in normalized_query
                ]
                product_category = None
                if any(term in lowered_resolved for term in ("phone", "mobile", "smartphone")):
                    product_category = "phone"
                elif "laptop" in lowered_resolved or "notebook" in lowered_resolved:
                    product_category = "laptop"

                if list_query:
                    if not products:
                        return customer_reply("There are no products in this shop's active catalog.")

                    lines = []
                    for product in products:
                        price = f"₹{product['price']:,}" if product.get("price") is not None else "Price not listed"
                        stock = product.get("stock_quantity", 0)
                        details = [f"- {product['product_name']} — {price}"]
                        if product.get("brand"):
                            details.append(f"Brand: {product['brand']}")
                        if product.get("category"):
                            details.append(f"Category: {product['category']}")
                        for label, key in (
                            ("RAM", "ram_gb"),
                            ("Storage", "storage_gb"),
                            ("Processor", "processor"),
                        ):
                            value = product.get(key)
                            if value:
                                details.append(f"{label}: {value}{'GB' if key in ('ram_gb', 'storage_gb') else ''}")
                        if product.get("description"):
                            details.append(f"Features: {product['description']}")
                        details.append(f"Availability: {product.get('availability') or ('In Stock' if stock > 0 else 'Out of Stock')} ({stock} in stock)")
                        lines.append("\n  ".join(details))
                    heading = "Product catalog and recorded features:" if all_catalog_query else "Available products:"
                    return customer_reply(heading + "\n" + "\n".join(lines))

                if live_price_limit is not None:
                    matches = products
                elif explicit_matches:
                    matches = explicit_matches
                elif comparison_query:
                    matches = products
                elif last_entity and pronoun_pattern.search(user_query):
                    matches = [
                        product for product in products
                        if last_entity.casefold() in product["product_name"].casefold()
                    ]
                else:
                    stop_words = {"what", "which", "show", "tell", "me", "about", "have", "you", "the", "is", "are", "price", "cost", "how", "much", "available", "availability", "stock", "in", "do", "sell", "product", "products", "phone", "phones", "mobile", "mobiles", "smartphone", "smartphones", "laptop", "laptops", "notebook", "notebooks"}
                    stop_words.update({
                        "best", "among", "company", "business", "own", "every", "each", "all",
                        "features", "feature", "specification", "specifications", "specs",
                        "compare", "comparison", "recommend", "recommendation", "why", "they",
                        "their", "for", "with", "and",
                    })
                    terms = [
                        term for term in re.findall(r"[a-z0-9]+", lowered_resolved)
                        if len(term) > 2 and term not in stop_words
                    ]
                    mentioned_matches = [
                        product for product in products
                        if any(term in " ".join(str(product.get(field) or "") for field in ("product_name", "brand", "category")).casefold() for term in terms)
                    ] if terms else []
                    if mentioned_matches:
                        matches = mentioned_matches
                    elif product_category:
                        category_aliases = ("phone", "mobile", "smartphone") if product_category == "phone" else (product_category, "notebook")
                        matches = [
                            product for product in products
                            if any(alias in (product.get("category") or "").casefold() for alias in category_aliases)
                            or any(alias in product["product_name"].casefold() for alias in category_aliases)
                        ]
                    else:
                        matches = products if not terms else []

                if not matches:
                    return customer_reply("I couldn't find a matching product in this shop's catalog.")

                asks_price = self._is_live_price_query(resolved_query)
                asks_stock = self._is_live_stock_query(resolved_query)
                if asks_price or asks_stock:
                    if asks_price and not asks_stock:
                        lowest_price_query = bool(re.search(
                            r"\b(?:cheapest|least expensive|lowest priced|most affordable)\b",
                            lowered_resolved,
                        ))
                        highest_price_query = bool(re.search(
                            r"\b(?:most expensive|highest priced|costliest)\b",
                            lowered_resolved,
                        ))
                        priced_matches = [
                            product for product in matches if product.get("price") is not None
                        ]
                        if lowest_price_query and priced_matches:
                            lowest = min(product["price"] for product in priced_matches)
                            matches = [product for product in priced_matches if product["price"] == lowest]
                        elif highest_price_query and priced_matches:
                            highest = max(product["price"] for product in priced_matches)
                            matches = [product for product in priced_matches if product["price"] == highest]
                    fact_lines = []
                    for product in matches:
                        facts = []
                        if asks_price:
                            price = product.get("price")
                            facts.append(
                                f"priced at ₹{price:,}" if price is not None else "price not listed"
                            )
                        if asks_stock:
                            stock = max(0, int(product.get("stock_quantity") or 0))
                            availability = "in stock" if stock > 0 else "out of stock"
                            facts.append(f"{availability} ({stock} unit(s) available)")
                        fact_lines.append(f"{product['product_name']} is " + " and ".join(facts) + ".")
                    return customer_reply("\n".join(fact_lines))

                context = None
                if any(term in lowered_resolved for term in ("warranty", "specification", "specs", "describe", "tell me about", "tell me more", "features")):
                    try:
                        context = self.tools.get_business_context(business_id, resolved_query)
                    except Exception as exc:
                        logger.warning("Customer product context unavailable for %s: %s", business_id, exc)
                payload = {
                    "live_products_from_sqlite": matches,
                    "shop_knowledge_context": context or "No additional shop knowledge was retrieved.",
                    "question": user_query,
                }
                product_system_prompt = (
                    "Answer the customer's question using only the supplied shop knowledge and live product data. "
                    "Use SQLite product fields as the only authority for price, stock, and availability; "
                    "ignore any such values that appear in the shop knowledge context. "
                    "Do not invent prices, stock, popularity, specifications, policies, or products. "
                    "If the customer asks which product is best, make a clear recommendation from the supplied "
                    "products and explain it using only recorded features, price, and availability; describe it as "
                    "best based on the available catalog information, and mention any important missing comparison "
                    "data. Do not claim unsupported performance or quality advantages. "
                    "If the data is insufficient for a requested specific fact, identify that gap instead of guessing. "
                    "Keep the answer concise."
                )
                if comparison_query:
                    payload["live_products_from_sqlite"] = products
                product_response = self.llm.generate_response(
                    [
                        {
                            "role": "system",
                            "content": product_system_prompt,
                        },
                        {"role": "user", "content": json.dumps(payload, default=str)},
                    ],
                    temperature=0.2,
                    max_tokens=600,
                )
                self.memory.add_message("user", user_query)
                self.memory.add_message("assistant", product_response)
                return customer_reply(product_response)

            # Build retrieval query combining recent history and the resolved question
            history_text = "\n".join(
                [f"{msg['role'].capitalize()}: {msg['content']}" for msg in history[-6:]]
            )

            retrieval_query = f"""
            Previous Conversation:

            {history_text}

            Current Question:

            {resolved_query}
            """

            is_business_query = self.tools.rag.is_business_query(resolved_query)
            logger.debug("Original Query: %s", user_query)
            logger.debug("Is Business Query: %s", is_business_query)
            logger.debug("Resolved Retrieval Query: %s", resolved_query)
            logger.debug("Retrieval Query Text: %s", retrieval_query)

            if not is_business_query:
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT}
                ]
                messages.extend(self.memory.get_history())
                messages.append({"role": "user", "content": user_query})
                response = self.llm.generate_response(messages)
                self.memory.add_message("user", user_query)
                self.memory.add_message("assistant", response)
                return customer_reply(response)

            # Retrieve business knowledge using only the resolved question
            context = self.tools.get_business_context(
                business_id,
                resolved_query
            )


            if not context:

                context = "No business information found."

            logger.debug("Retrieved Context: %s", context)


            # Store user message
            self.memory.add_message(
                "user",
                user_query
            )

            if last_entity:
                self.memory.set_last_entity(last_entity)

            if not context or context == "No business information found.":
                response = "I don't have that information in the business knowledge base."
                self.memory.add_message("assistant", response)
                return customer_reply(response)

            messages = [

                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                }

            ]


            # Add conversation history
            messages.extend(
                self.memory.get_history()
            )


            # Add current question with retrieved context
            messages.append(
                {
                    "role": "user",
                    "content":
                    f"""
                    Business Information:

                    {context}


                    Customer Question:

                    {user_query}


                    Answer only using the Business Information provided above.
                    If the answer is not present in the Business Information, reply exactly:
                    "I don't have that information in the business knowledge base."
                    Do not invent business details.
                    Do not use unrelated general knowledge.
                    Do not include any business profile, contact details, or unrelated metadata unless the question explicitly requests them.
                    """
                }
            )


            # Generate response
            response = self.llm.generate_response(
                messages
            )


            # Store AI response
            self.memory.add_message(
                "assistant",
                response
            )

            if self._should_escalate(user_query, context=context, generated_response=response):
                return self._handle_human_escalation(
                    business_id,
                    user_query,
                    customer_phone,
                    customer_session_id,
                    customer_conversation_id,
                )

            return customer_reply(response)


        except Exception as e:

            return {"signal": "reply", "reply": f"Agent Error: {str(e)}"}