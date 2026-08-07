from app.llm.llm import LLM
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.agent.memory import ConversationMemory
from app.agent.tools import AgentTools
from app.utils.logger import logger
import re


class AIAgent:

    def __init__(self):

        self.llm = LLM()

        self.memory = ConversationMemory()

        self.tools = AgentTools()


    def chat(
        self,
        business_id,
        user_query
    ):

        try:

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
                return response

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
                return response

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


            return response


        except Exception as e:

            return f"Agent Error: {str(e)}"