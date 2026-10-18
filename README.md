# SmartBiz AI Business Automation System

SmartBiz AI combines business-scoped SQLite operations with a Streamlit shopkeeper dashboard and a customer-facing assistant backed by the existing AI, RAG, memory, and ChromaDB components.

## Dashboard capabilities

- Live shop-scoped dashboard KPIs, recent orders, product demand, notifications, and customer conversations.
- Orders page with deterministic revenue/order analytics and status updates.
- Confirming an order deducts stock once, rejects insufficient stock, and creates de-duplicated low-stock notifications.
- Product catalog management with editable descriptions and specifications, stock adjustment, configurable low-stock thresholds, and product knowledge sync without live price, stock, or availability in RAG.
- Combined Products & Stock management, notifications, conversation handling, analytics, and settings.
- An AI Business Hub with shop knowledge uploads, data-grounded insights, and a shop-scoped AI chat.
- Shop documents can be PDF, DOCX, TXT, CSV, JPG, or PNG; image text is extracted by the configured vision model.
- On-demand structured business insights using verified SQLite metrics plus relevant business-scoped RAG context. Metrics remain available when the LLM or RAG is unavailable.
- Customer shop discovery, category/search filters, selected-shop product browsing, and SQLite-backed checkout.
- Nearby shop discovery with browser geolocation or manual town/postal-code search, ordered by real shop-coordinate distance and showing current active products.
- Shopkeepers can save shop coordinates during registration or update the address and exact coordinates in Settings using on-site browser location or manual fields; existing addresses are geocoded to approximate town/city locations and can be refined by each shopkeeper.
- Random browser-scoped customer sessions isolate orders; conversations are scoped to both customer session and selected business.
- Customer AI chat uses the existing agent, live SQLite product/order tools, business-specific ChromaDB retrieval, and persisted `Conversation`/`Message` history.
- Customer product answers list recorded features for every catalog item and compare in-stock products using only that business's live catalog data.
- Conversational and cart orders require COD or EMI, then explicit customer confirmation; pending product, quantity, payment, and confirmation state is stored on the conversation.
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

Shop knowledge uploads are split by section and indexed with sentence-transformer embeddings in the active business's ChromaDB collection. Retrieval ranks by semantic distance without requiring prepared FAQ pairs; replacing an uploaded file updates its indexed chunks and removes obsolete ones. Product prices, availability, and inventory remain SQLite/tool data.

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

For JPG/PNG knowledge uploads, `VISION_MODEL` selects an image-capable model supported by the configured `BASE_URL`. The default is `meta-llama/llama-4-scout-17b-16e-instruct`. Uploaded images are sent to that provider for text extraction before being indexed in the business-specific ChromaDB collection.

Nearby discovery uses OpenStreetMap Nominatim to resolve a town from live coordinates or a manually entered place. The customer is informed before choosing either search method. GPS access is only requested after selecting **Use My Location**. Existing shop addresses are geocoded to approximate town/city coordinates; shopkeepers can replace those pins with exact shop coordinates. Town search shows shops without active products and separates shops outside the 50 km radius from shops missing coordinates.

## Run

Run these commands from the project root in PowerShell. They reuse the checked-in local `.venv`; do not create another environment or install packages for the demo.

```powershell
.\.venv\Scripts\python.exe --version
```

Set the LLM provider's `API_KEY` in the ignored local `.env` file if it is not already configured. Keep the key private; do not paste it into chat, screenshots, or source control. `BASE_URL` and `MODEL` can use the existing values in that file.

```powershell
notepad .env
```

Start the existing Streamlit dashboard:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app\dashboard.py
```

Open the local URL Streamlit prints (normally `http://localhost:8501`). Use the shopkeeper login or customer shop browsing from the home page. The app uses the existing `data\business.sqlite3` and `data\chroma_db`; do not run the demo seeder for this presentation because it writes to those stores.

Run the complete local regression suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Deployment notes

The application can be started with the command above, but this repository does not include a container image, deployment manifest, or CI/CD configuration. For a hosted Streamlit service, bind to the interface and port provided by the host, for example:

```sh
python -m streamlit run app/dashboard.py --server.address 0.0.0.0 --server.port "${PORT:-8501}"
```

Configure `API_KEY` through the host's secret store. `BASE_URL` and `MODEL` can select the compatible LLM endpoint and model; `VISION_MODEL` is optional. Do not commit `.env` or put secret values in deployment files.

The default SQLite database and the ChromaDB index are local files. Configure `DATABASE_URL`, `DOCUMENT_PATH`, and `CHROMA_DB_PATH` to locations on durable storage before deployment. For example, with a persistent volume mounted at `/mnt/smartbiz-data`, use a SQLite URL such as `sqlite:////mnt/smartbiz-data/business.sqlite3`, `DOCUMENT_PATH=/mnt/smartbiz-data/documents`, and `CHROMA_DB_PATH=/mnt/smartbiz-data/chroma_db`. Back up the database and uploaded documents as well as the vector index. Keep this deployment to one app instance: SQLite and the local ChromaDB persistence directory are not a shared, multi-replica data service. For concurrent or horizontally scaled production use, migrate these stores to suitable managed services and test the migration.

### Production blockers

- Shopkeeper signup currently uses a development OTP: the code is shown in the UI and no SMS provider is connected. Do not treat this as phone verification for a public deployment.
- Shopkeeper password login has no account lockout or login rate limit.
- The “Forgot Password?” link is only a placeholder; there is no password recovery flow.
- Customer access is browser-session based rather than account authenticated. Customers can lose access to their conversation/order history when that browser session is lost.
- The displayed COD/EMI choices are order metadata; no payment gateway integration is configured.
- Shop coordinates in the current database are town-level approximate pins (`location_precision="town"`), not verified storefront locations.

Address these limitations, configure durable storage and secret management, and exercise the production deployment before making the service publicly available.