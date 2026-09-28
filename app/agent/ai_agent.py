import re

from app.agent.memory import ConversationMemory
from app.agent.tools import AgentTools
from app.database import (
    Conversation,
    ConversationStatus,
    Message,
    MessageSender,
    get_session,
)
from app.llm.llm import LLM
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.utils.logger import logger


class AIAgent:


    def __init__(self):

        self.llm = LLM()

        self.memory = ConversationMemory()

        self.tools = AgentTools()

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

    def _handle_human_escalation(self, business_id, user_query, customer_phone=None):
        session = get_session()
        try:
            conversation = (
                session.query(Conversation)
                .filter(
                    Conversation.business_id == business_id,
                    Conversation.customer_phone == customer_phone,
                )
                .order_by(Conversation.created_at.desc())
                .first()
            )

            if conversation is None:
                conversation = Conversation(
                    business_id=business_id,
                    customer_phone=customer_phone,
                    status=ConversationStatus.ESCALATED,
                )
                session.add(conversation)
                session.flush()
            elif conversation.status in {ConversationStatus.ESCALATED, ConversationStatus.KEEPER_HANDLING}:
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        sender=MessageSender.CUSTOMER,
                        text=user_query,
                    )
                )
                session.commit()
                return {"signal": "awaiting_human", "reply": None}

            conversation.status = ConversationStatus.ESCALATED
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

    def chat(
        self,
        business_id,
        user_query,
        customer_phone=None,
    ):

        try:
            session = get_session()
            try:
                conversation = (
                    session.query(Conversation)
                    .filter(
                        Conversation.business_id == business_id,
                        Conversation.customer_phone == customer_phone,
                    )
                    .order_by(Conversation.created_at.desc())
                    .first()
                )
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
                return self._handle_human_escalation(business_id, user_query, customer_phone)


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
                    if msg.get("role") == "assistant":
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
                return {"signal": "reply", "reply": response}

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
                return {"signal": "reply", "reply": response}

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
                return self._handle_human_escalation(business_id, user_query, customer_phone)

            return {"signal": "reply", "reply": response}


        except Exception as e:

            return f"Agent Error: {str(e)}"