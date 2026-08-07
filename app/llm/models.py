from enum import Enum

class LLMModels(str, Enum):

    LLAMA_3_1_8B = "llama-3.1-8b-instant"

    LLAMA_3_3_70B = "llama-3.3-70b-versatile"

    GEMMA2_9B = "gemma2-9b-it"

    MIXTRAL = "mixtral-8x7b-32768"