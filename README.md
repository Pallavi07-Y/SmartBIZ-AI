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

For JPG/PNG knowledge uploads, `VISION_MODEL` selects an image-capable model supported by the configured `BASE_URL`. The default is `meta-llama/llama-4-scout-17b-16e-instruct`. Image contents are sent to that provider for text extraction before indexing; the uploader requires an explicit consent checkbox. Chat prompts and retrieved shop knowledge are also sent to the configured AI provider. Do not include sensitive personal or financial information unless the provider and data handling have been approved for that use. Original uploaded files are processed from temporary storage and are not retained; re-upload them if the ChromaDB index is lost.

There is no Google Maps integration or Maps API key requirement. Customers and shopkeepers can enter an address and may optionally use browser geolocation; latitude and longitude are never required as manual input. Customer delivery addresses and available coordinates, and shopkeeper addresses and available coordinates, are stored in SQLite. Customer nearby discovery uses a fixed 5 km radius when device coordinates are available; address-only searches use the existing OpenStreetMap Nominatim town lookup and cannot calculate an exact distance.

## Run

Python 3.13.2 is the version exercised in the development workspace; the repository does not declare a broader supported Python range. For a fresh clone, run these commands from the project root in PowerShell:

```powershell
py -3.13 --version
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Customer and shopkeeper signups require a six-digit email verification code sent through Gmail SMTP before an account is created. Codes expire after 10 minutes by default, are stored only as keyed hashes, are single-use, and have attempt, resend-cooldown, and hourly email/IP request limits. Existing accounts are marked verified by the additive schema migration so current users retain access. Shopkeepers and customers receive permanent IDs (`SBZ-S-...` and `SBZ-U-...`) and can sign in using either that ID or their registered email.

Set `API_KEY` in a private environment, ignored local `.env`, or Streamlit secrets for AI features. Configure optional `BASE_URL`, `MODEL`, and `VISION_MODEL` for the compatible AI provider. Never put secrets in source control, logs, screenshots, or chat. Login and upload limits can be tuned with the `LOGIN_*` and `MAX_UPLOAD_SIZE_MB` environment variables; all limits must be positive integers.

Email verification requires `SMTP_USERNAME`, `SMTP_PASSWORD`, and `EMAIL_OTP_HASH_SECRET`. Use a Gmail App Password (with Google 2-Step Verification enabled), not your normal Gmail password. Generate a separate random `EMAIL_OTP_HASH_SECRET` of at least 32 characters and keep it private; changing it invalidates pending codes. Optional settings include `SMTP_HOST` (defaults to `smtp.gmail.com`), `SMTP_PORT` (defaults to `465`), `SMTP_FROM_EMAIL` (defaults to `SMTP_USERNAME`), and the `EMAIL_OTP_*` limits and expiry settings.

```powershell
notepad .env
```

Create `.env` locally without committing it. Start the dashboard:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app\dashboard.py
```

Open the local URL Streamlit prints (normally `http://localhost:8501`). Check `http://localhost:8501/_stcore/health` for a successful health response. The app uses local SQLite and ChromaDB stores; do not run the demo seeder against a database containing user data.

Run the complete local regression suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Streamlit Community Cloud deployment

The app entrypoint is `app/dashboard.py`. From the repository root, the local startup command is:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app\dashboard.py
```

To deploy a demo on Streamlit Community Cloud:

1. Revoke any API key that has ever been committed, pasted into chat, or otherwise exposed; create a replacement with the provider and store it only in Streamlit Community Cloud's app **Settings → Secrets**.
2. Push the intended branch to GitHub only after reviewing its complete diff and confirming `.env`, local databases, and ChromaDB files are ignored and untracked.
3. In Streamlit Community Cloud, choose **Create app**, select the repository and branch, and set the main file path to `app/dashboard.py`.
4. In **Advanced settings**, select Python 3.13 (the version tested for this project), then create the secrets below in the app's Secrets editor. Use your own secret values there; do not commit a `secrets.toml` file.
5. Deploy and verify the app opens, account signup sends an email, customer and shopkeeper login work, and AI/RAG features can load their configured models.

The following TOML shows the required key names and expected shape only; replace placeholders in the Cloud Secrets editor. `BASE_URL`, `MODEL`, and `VISION_MODEL` are optional if the defaults are suitable.

```toml
API_KEY = "<replacement Groq API key>"
EMAIL_OTP_HASH_SECRET = "<random private string at least 32 characters long>"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = "465"
SMTP_USERNAME = "<Gmail address>"
SMTP_PASSWORD = "<Gmail App Password>"
SMTP_FROM_EMAIL = "<Gmail address>"

# Optional AI provider settings:
BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "<model supported by the configured provider>"
VISION_MODEL = "<vision model supported by the configured provider>"
```

For Gmail, enable 2-Step Verification and create an App Password for this application. Put that App Password in `SMTP_PASSWORD`; never use your regular Google account password. SMTP credentials and the independent OTP hash secret must remain in Cloud Secrets or a private local environment. If the OTP hash secret is rotated, outstanding verification codes stop working.

`requirements.txt` is at the repository root and is used by Community Cloud. No custom startup script or `PORT` variable is needed; the platform launches the selected Streamlit entrypoint. The app reads API and mail credentials from environment variables first and Streamlit secrets second.

### Persistence and feature limitations on Community Cloud

The current application stores account data and OTP challenges in local SQLite and embeddings in a local ChromaDB directory under `data/`. Community Cloud does not provide this app with a durable volume contract. Treat SQLite, OTP challenges, and ChromaDB on the instance as **ephemeral**: rebuilds, restarts, or instance replacement may discard them. Do not use this setup for durable customer or business records. Before production use, move these stores to managed durable services and verify backups and restore procedures. No data migration or external storage has been configured by this project.

The embedding model is loaded with `local_files_only=True`. AI/RAG functions that need embeddings will fail unless `all-MiniLM-L6-v2` is already available in the deployment's model cache; the repository does not currently provision that model as part of the Community Cloud build. Verify model availability in the target environment before relying on document indexing or semantic search.

Schema startup migrations are additive and recorded in `schema_migrations`. Version 5 adds email verification state and OTP challenge storage; legacy accounts are marked verified to preserve existing access. For local upgrades, take a verified backup first. There is no automatic destructive down-migration; roll back by restoring the pre-upgrade SQLite backup and matching ChromaDB backup, if used.

### Production blockers

- Password recovery is unavailable. Implement and test a secure email-based reset flow before opening public signup, or provide a documented and staffed recovery process.
- COD and EMI are stored as order metadata only; the application does not process payments.
- Customer access is browser-session based rather than account authenticated. Customers can lose access to their conversation/order history when that browser session is lost.
- Shop coordinates in the current database are town-level approximate pins (`location_precision="town"`), not verified storefront locations.

Community Cloud's local storage and the uncached embedding-model requirement are additional deployment limitations. Before a public or production launch, configure durable storage and model provisioning, implement password recovery, verify AI provider behavior in the deployment environment, and review external AI data handling and consent with users.