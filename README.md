# SmartBiz AI Business Automation System

SmartBiz AI combines business-scoped SQLite operations with a Streamlit shopkeeper dashboard and a customer-facing assistant backed by the existing AI, RAG, memory, and ChromaDB components.

## Dashboard capabilities

- Live shop-scoped dashboard KPIs, recent orders, product demand, notifications, and customer conversations.
- Orders page with deterministic revenue/order analytics and status updates.
- Confirming an order deducts stock once, rejects insufficient stock, and creates de-duplicated low-stock notifications.
- Product catalog management, stock adjustment, configurable low-stock thresholds, and existing product-to-RAG synchronization.
- Inventory, notifications, conversation handling, knowledge-base, analytics, and settings pages.
- On-demand structured business insights using verified SQLite metrics plus relevant business-scoped RAG context. Metrics remain available when the LLM or RAG is unavailable.
- Customer shop discovery, category/search filters, selected-shop product browsing, and SQLite-backed checkout.
- Random browser-scoped customer sessions isolate orders; conversations are scoped to both customer session and selected business.
- Customer AI chat uses the existing agent, live SQLite product/order tools, business-specific ChromaDB retrieval, and persisted `Conversation`/`Message` history.
- Ten sample shops and business-specific policy/product knowledge can be created explicitly for development without seeding on app startup.

## Architecture

```text
Shopkeeper session (business_id, role)
					|
			 Streamlit dashboard
		  /         |          \
	  Orders     Products    Conversations
		  \         |          /
			  SQLite data
				   |
		 Deterministic analytics
				   |
	Existing AI Agent + business RAG
				   |
		   Configured existing LLM
				   |
	   Structured, evidence-based insights

Business documents and product context -> existing RAG pipeline -> business-scoped ChromaDB
Customer messages -> existing conversation storage and memory -> existing AI Agent and tools
```

Customer request flow:

```text
Customer session -> Shop discovery -> Selected business context -> Existing AI Agent
																														 /          |          \
																									SQLite product/order  RAG       Conversation memory
																														 \          |          /
																													 Existing LLM
```

SQLite is the source of truth for live orders, prices, inventory, notifications, and conversations. RAG supplies contextual business knowledge; it is not used to determine live prices, stock, order status, or numeric KPIs. Every shopkeeper query uses authenticated `business_id`; customer orders use a random customer-session ID and chats use both that ID and the selected `business_id`.

## Demo Shops

Demo data is opt-in and is not created during normal application startup. Run:

```powershell
python -m app.seed_demo_data
```

The command idempotently seeds ABC Mobiles and nine additional sample shops, adds sample products to SQLite, and indexes shop-specific knowledge through the existing document uploader and ChromaDB pipeline.

## Technology

- Python and Streamlit
- SQLAlchemy and SQLite
- ChromaDB, LangChain, and Sentence Transformers
- Existing configured OpenAI-compatible LLM client

## Run

Install dependencies:

```powershell
pip install -r requirements.txt
```

Start the dashboard:

```powershell
python -m streamlit run app/dashboard.py
```

Run tests:

```powershell
pytest -q
```