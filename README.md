# AI Business Automation System

An AI-powered business assistant built using RAG.

## Features

- Business-specific AI Agent
- Retrieval Augmented Generation
- ChromaDB Vector Database
- Groq Llama LLM
- Document Understanding
- Multi-business Knowledge Base

## Architecture

Business Documents

↓

Document Loader

↓

Chunking

↓

Embeddings

↓

ChromaDB

↓

Retriever

↓

Groq Llama

↓

AI Response


## Technologies

- Python
- LangChain
- ChromaDB
- Sentence Transformers
- Groq API
- Llama Model


## Run

Install dependencies:

pip install -r requirements.txt


Run:

python -m app.main