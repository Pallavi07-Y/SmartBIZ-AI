"""
llm.py

This module connects to the xAI (Grok) API and
generates responses using the configured model.
"""

from openai import OpenAI

from app.config import settings
from app.utils.logger import logger


class LLM:

    def __init__(self):

        self.client = OpenAI(
            api_key=settings.API_KEY,
            base_url=settings.BASE_URL
        )

        self.model = settings.MODEL

    def generate_response(
            self,
            messages,
            temperature=0.7,
            max_tokens=1024
    ):

        try:

            response = self.client.chat.completions.create(

                model=self.model,

                messages=messages,

                temperature=temperature,

                max_tokens=max_tokens

            )

            return response.choices[0].message.content

        except Exception as e:

            logger.error("LLM Error: %s", e)

            return "Sorry, something went wrong while generating the response."