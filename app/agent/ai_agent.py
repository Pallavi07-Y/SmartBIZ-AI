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
    def _is_order_intent(message):
        text = (message or "").lower()
        if not text:
            return False

        order_triggers = [
            "i want to order",
            "i want to buy",
            "place an order",
            "place order",
            "buy",
            "purchase",
            "order",
        ]
        return any(trigger in text for trigger in order_triggers)

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
            ]
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
    ):

        try:
            customer_conversation_id = None
            if customer_session_id:
                customer_conversation_id, _, stored_history = self._load_customer_conversation(
                    business_id,
                    customer_session_id,
                    customer_phone,
                    conversation_id,
                )
                self.memory.clear()
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

            lower_query = user_query.lower()
            if (
                "where is my order" in lower_query
                or "order status" in lower_query
                or "status of my order" in lower_query
                or "track my order" in lower_query
            ):
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

            if self._is_order_intent(user_query):
                product_name = None
                candidate_products = self.tools.search_products(business_id, user_query)
                if candidate_products:
                    product_name = candidate_products[0].get("product_name")

                if not product_name:
                    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9 &+-]{2,60}", user_query):
                        if len(token.split()) <= 5 and token.lower() not in {"i", "want", "to", "buy", "order", "please", "from"}:
                            product_name = token.strip()
                            break

                if not product_name:
                    return {"signal": "reply", "reply": "I couldn't identify the product you want to order. Please tell me the product name."}

                quantity = 1
                quantity_match = re.search(r"(\d+)\s*(?:units?|pcs?|items?|phones?)?", user_query.lower())
                if quantity_match:
                    quantity = max(1, int(quantity_match.group(1)))

                order_phone = customer_phone or "0000000000"
                if customer_session_id and not customer_phone:
                    return customer_reply("Please add your phone number in checkout before placing an order through chat.")
                order = self.tools.create_order(
                    business_id,
                    customer_name="Customer",
                    phone=order_phone,
                    product_name=product_name,
                    quantity=quantity,
                    customer_session_id=customer_session_id,
                    customer_address=customer_address,
                )
                if isinstance(order, dict) and order.get("error"):
                    return customer_reply(order["error"])
                return customer_reply(
                    f"Order placed successfully for {order['product_name']} x{order['quantity']} "
                    f"({order['status']}). Total: ₹{order['total']:,}"
                )


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
            product_query = any(term in lowered_resolved for term in (
                "product", "products", "price", "how much", "stock", "in stock", "available",
                "availability", "phones", "phone", "mobile", "smartphone", "laptop", "headphone",
                "recommend", "compare", "what do you have", "sell", "charger", "chargers",
                "accessory", "accessories", "usb-c",
            ))
            if product_query and hasattr(self.tools, "get_live_products"):
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
                products = self.tools.get_live_products(
                    business_id,
                    limit=100,
                    available_only=bool(list_query),
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
                        return customer_reply("There are no currently available products in this shop.")

                    lines = []
                    for product in products:
                        price = f"₹{product['price']:,}" if product.get("price") is not None else "Price not listed"
                        stock = product.get("stock_quantity", 0)
                        lines.append(f"- {product['product_name']} — {price} — {stock} in stock")
                    return customer_reply("Available products:\n" + "\n".join(lines))

                if explicit_matches:
                    matches = explicit_matches
                elif last_entity and pronoun_pattern.search(user_query):
                    matches = [
                        product for product in products
                        if last_entity.casefold() in product["product_name"].casefold()
                    ]
                else:
                    stop_words = {"what", "which", "show", "tell", "me", "about", "have", "you", "the", "is", "are", "price", "cost", "how", "much", "available", "availability", "stock", "in", "do", "sell", "product", "products", "phone", "phones", "mobile", "mobiles", "smartphone", "smartphones", "laptop", "laptops", "notebook", "notebooks"}
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

                if any(term in lowered_resolved for term in ("price", "cost", "how much")) and len(matches) == 1:
                    product = matches[0]
                    if product.get("price") is None:
                        return customer_reply(f"The price for {product['product_name']} is not listed.")
                    return customer_reply(f"{product['product_name']} is priced at ₹{product['price']:,}.")

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
                product_response = self.llm.generate_response(
                    [
                        {
                            "role": "system",
                            "content": (
                                "Answer the customer's question using only the supplied shop knowledge and live product data. "
                                "SQLite product fields are authoritative for price, stock, and availability. "
                                "Do not invent prices, stock, popularity, specifications, policies, or products. "
                                "If the data is insufficient, say so. Keep the answer concise."
                            ),
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