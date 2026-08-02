# TradeMind — Build Progress

## How to resume
Open this file first. The current phase and next task tell you exactly where to pick up.
Reference: `trading-knowledge-base-spec.md` for the full spec.

---

## Current phase: COMPLETE — all 12 phases shipped

### Phase 1 checklist

#### Infrastructure
- [x] PROGRESS.md (this file)
- [x] docker-compose.yml (Postgres dev environment)
- [x] backend/ scaffolded (FastAPI + SQLAlchemy + Alembic)
- [x] frontend/ scaffolded (Vite + React + TypeScript)

#### Backend
- [x] Core config (`backend/app/core/config.py`)
- [x] Database session (`backend/app/core/database.py`)
- [x] Security utilities (`backend/app/core/security.py`)
- [x] FastAPI dependencies (`backend/app/core/dependencies.py`)
- [x] SQLAlchemy models: User, Workspace, WorkspaceMember, Subscription, UsageEvent
- [x] Alembic migration (initial schema — all Phase 1 tables)
- [x] Auth endpoints: POST /auth/register, POST /auth/token, POST /auth/refresh, GET /auth/me
- [x] Workspace auto-creation on signup (owner workspace_member row)
- [x] Usage event logging service (`backend/app/services/usage_service.py`)
- [x] Workspace endpoints: GET /workspaces/current, GET /workspaces/{id}

#### Frontend
- [x] Vite + React + TypeScript project
- [x] react-i18next with EN/ES translation files
- [x] Auth context (JWT token management)
- [x] API client (axios with token injection)
- [x] Login page (bilingual)
- [x] Register page (bilingual, language selection)
- [x] Dashboard (welcome, workspace name, language toggle)
- [x] Protected route wrapper

---

## Phase 2 — Telegram text → distillation → embeddings → Ask view
**Status:** COMPLETE

### Phase 2 checklist
- [x] Telegram platform_connection model + encryption (Fernet, key from SECRET_KEY)
- [x] Telegram auth flow (phone OTP via Telethon — `connections.py`)
- [x] source_config model + Telegram connector (`connectors/telegram.py`)
- [x] evidence_item model + BackfillJob model
- [x] Relevance filter — Claude Haiku, runs before any LLM call (`services/relevance_service.py`)
- [x] Distillation service — Claude Sonnet → trade_idea + bilingual summaries (`services/distillation_service.py`)
- [x] embedding_row model + multilingual-E5 pipeline (`services/embedding_service.py`)
- [x] Hybrid retrieval — pgvector cosine + FTS + RRF fusion (`services/retrieval_service.py`)
- [x] Query synthesis — Claude Sonnet with cited sources (`services/query_synthesis_service.py`)
- [x] Ingestion pipeline orchestrator (`services/ingestion_pipeline.py`)
- [x] APScheduler polling worker (`workers/poller.py`)
- [x] Budget/overage check in Ask endpoint
- [x] Ask view (chat UI, cited sources, EN/ES)
- [x] Admin/Sources view (connect Telegram, add channels, fetch, backfill)
- [x] NavBar with Ask + Sources links
- [x] Alembic migration 002 (all Phase 2 tables + pgvector column)

---

## Phase 3 — YouTube ingestion
**Status:** COMPLETE

- [x] YouTube Data API connector (`connectors/youtube.py`) — same BaseConnector interface
- [x] Channel handle/ID/playlist resolution via YouTube Data API v3
- [x] New-video detection — poller compares since_id; dedup via stable_id in DB
- [x] Native caption pull — `youtube-transcript-api`, prefers manual → auto-generated
- [x] Whisper transcription fallback — OpenAI Whisper API via yt-dlp audio download (`services/transcription_service.py`)
- [x] Chunking — ~5-min / 600-word segments → each chunk is its own evidence_item
- [x] Whisper cost logged as `usage_event` (task_type="transcription") per video, not per chunk
- [x] Frontend: source_type selector (Telegram / YouTube) in Admin Sources view
- [x] RawMessage extended: `source_type`, `content_type`, `pre_pipeline_cost_usd`
- [x] Pipeline dispatch generalized — `_build_connector()` factory handles both sources
- [x] Backfill runner updated to use generic connector factory

## Phase 4 — Media extraction (images, video, URLs)
**Status:** COMPLETE

- [x] `services/vision_service.py` — single Claude Vision call: extraction + relevance judgment in one (no duplicate Haiku call)
- [x] `services/keyframe_service.py` — ffmpeg key-frame extraction; skips gracefully if ffmpeg absent or file > 100 MB
- [x] `services/url_service.py` — readability-lxml article extraction; BeautifulSoup fallback
- [x] `connectors/telegram.py` — full rewrite: yields image / URL / video RawMessages when enabled by content_filters
- [x] `services/ingestion_pipeline.py` — branches on content_type: text/video_transcript, image, url, video
- [x] Image pipeline: vision cost logged as task_type="vision"; temp image bytes cleaned from metadata before DB write
- [x] Video pipeline: per-frame vision calls aggregated into one usage_event; temp file always deleted (finally block)
- [x] URL pipeline: fetch → extract → standard text pipeline (Haiku relevance + Sonnet distillation)
- [x] Frontend content filter toggles in Add Source form (per-type checkboxes, sensible defaults per source_type)
- [x] Active filters shown as badges on each source card
- [x] Requirements: Pillow, readability-lxml, requests
- [x] Note: ffmpeg must be in PATH for video key-frame extraction

## Phase 7 — Learning loop
**Status:** COMPLETE

- [x] `services/confidence_tier.py` — `compute_confidence_tier()` shared across mining + validation
- [x] `confidence_tier` column on strategy_cards (still_learning | developing | established) — Migration 005
- [x] Mining service: richer version_history snapshots (win_rate, confidence_tier, changes_en, change_source)
- [x] Validation service: updates confidence_tier after win_rate is set; adds validation snapshot to version_history only when win_rate or tier actually changed (avoids noise)
- [x] `api/changelog.py` — GET /changelog (flattens version_history across all cards, sorted newest-first), POST /recompute (chains mining → validation)
- [x] `Changelog.tsx` — chronological activity feed, grouped by date, colored by change source
- [x] `StrategyLibrary.tsx` — tier badge replaces old stillLearning heuristic; both Mining and Validation buttons present
- [x] NavBar: Changelog link added
- [x] EN/ES translations for changelog + tier labels

---

## Phase 6 — Validation layer
**Status:** COMPLETE

- [x] `models/outcome_check.py` — per-trade_idea result (won/lost/open/expired/inconclusive), MAE, MFE, holding_days
- [x] Migration 004 — outcome_checks + walk_forward_result + validation_updated_at on strategy_cards
- [x] `connectors/market_data.py` — yfinance default (no key), Polygon upgrade via POLYGON_API_KEY
- [x] `services/validation_service.py` — bar-by-bar outcome scan, recency-weighted win rate (6-month half-life), Wilson 95% CI, walk-forward 60/40 split with overfit flag
- [x] `api/validation.py` — POST /validation/run (background), GET /validation/outcomes
- [x] APScheduler: Sunday 03:30 UTC validation job (after mining at 03:00)
- [x] StrategyLibrary.tsx: win rate + CI display, walk-forward in/out-sample detail, overfit warning badge, "Run validation" button
- [x] Requirements: yfinance==0.2.44

---

## Phase 5 — Pattern mining
**Status:** COMPLETE

- [x] `models/strategy_card.py` — workspace-scoped, JSONB flowchart_spec + version_history
- [x] `models/plan_item.py` — user-scoped (never shared), unique per user+card
- [x] Migration 003 — strategy_cards + plan_items tables
- [x] `services/pattern_mining_service.py` — language-blind SQL aggregation by (symbol, setup_type); cross-source corroboration + time-spread confidence; LLM card generation (EN+ES); versioned upsert
- [x] `api/strategy_cards.py` — list/get + POST /mining/run (background job)
- [x] `api/plan_items.py` — personal CRUD (scoped to current user, not shared)
- [x] APScheduler weekly mining job (Sunday 03:00 UTC)
- [x] `StrategyLibrary.tsx` — grid, EN/ES toggle, flowchart expand, Add to plan
- [x] `MyPlan.tsx` — status tabs (watching/adopted/rejected), inline notes + risk edit
- [x] NavBar updated (Library + My Plan links)

## Phase 6 — Validation layer
- [ ] Market data connector
- [ ] Outcome checker + win rate computation

## Phase 7 — Learning loop
- [ ] strategy_card versioning + recurring recomputation

## Phase 8 — Teaching layer + Strategy library / My plan / Changelog UI
**Status:** COMPLETE

- [x] `StrategyCardDetail.tsx` — full teaching page at `/library/:cardId`
- [x] Visual flowchart: color-coded step boxes (Entry → Confirmation → Risk Management → Exit) with arrows
- [x] Outcome breakdown: stacked bar + legend (won/lost/open/expired/inconclusive counts from OutcomeCheck)
- [x] Cited examples: each with EN/ES summary, original-text toggle (with language label), source attribution (channel, author, timestamp), outcome badge, YouTube source link
- [x] Version history: reverse-chronological snapshots with changes description, source, date, win rate, sample size
- [x] Walk-forward block on detail page (in/out-sample win rates + overfit warning if applicable)
- [x] Add-to-plan button on detail page; "View card" link added to MyPlan items
- [x] StrategyLibrary "How to trade it" button navigates to detail page (replaces inline expand)
- [x] Route `/library/:cardId` wired in App.tsx
- [x] Fixed JSON syntax errors in en.json and es.json (missing commas after `plan` block)
- [x] Full bilingual EN/ES translations for all detail view keys

## Phase 9 — Broker connector (paper trading only)
**Status:** COMPLETE

- [x] `models/broker_order.py` — per-user, status: proposed|confirmed|submitted|filled|rejected|cancelled, mode: paper|live, confirmed_at enforces the mandatory human confirmation gate
- [x] `models/journal_entry.py` — per-user, auto-created on fill; links back to broker_order and strategy_card
- [x] `alembic/versions/006_broker.py` — broker_orders + journal_entries tables with indexes
- [x] Config: ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER_BASE_URL added to settings
- [x] `connectors/alpaca.py` — paper trading wrapper: submit_order, get_order, cancel_order; graceful 503 if keys not configured; ALPACA_TERMINAL_MAP for status normalization
- [x] `api/broker.py` — full REST endpoints:
  - POST /users/me/orders (propose)
  - GET /users/me/orders (list, with status filter)
  - GET /users/me/orders/{id}
  - POST /users/me/orders/{id}/confirm (proposed → confirmed, sets confirmed_at — hard gate)
  - POST /users/me/orders/{id}/submit (confirmed → submitted, calls Alpaca)
  - POST /users/me/orders/{id}/cancel (proposed/confirmed/submitted → cancelled; cancels on Alpaca if submitted)
  - POST /users/me/orders/{id}/refresh (polls Alpaca, updates status; creates JournalEntry on fill)
  - GET /users/me/journal
- [x] Paper mode only — live mode blocked; mode="paper" hardcoded on all new orders
- [x] `pages/PaperTrading.tsx` — full UI:
  - Status filter tabs (All / Proposed / Confirmed / Submitted / Filled / Rejected / Cancelled)
  - OrderCard: status badge, price levels, confirm/submit/cancel/refresh actions
  - Confirm dialog with warning before moving to confirmed state
  - Propose order modal (symbol, action, entry/target/stop, size, notes)
  - Auto-opens modal with pre-filled params from ?symbol=&cardId= query string
  - Journal section (filled trades)
- [x] `StrategyCardDetail.tsx` — "Propose paper order" button navigates to /trading with symbol+cardId prefilled
- [x] Route /trading added to App.tsx
- [x] NavBar: "Paper Trading" link added
- [x] Full bilingual EN/ES translations for all trading/order/journal keys

## Phase 10 — Trade journal feedback loop
**Status:** COMPLETE

- [x] `models/journal_outcome.py` — per-journal-entry outcome (unique on journal_entry_id); stores entry/target/stop, actual_exit, MAE/MFE, outcome, data_source
- [x] `alembic/versions/007_journal_outcome.py` — journal_outcomes table with user_id and strategy_card_id indexes
- [x] `services/journal_validation_service.py`:
  - `score_journal_entry` — three-path scoring: (1) exit recorded → compare against broker_order target/stop; (2) no exit but have levels → scan market bars via `_check_bars` (reused from validation_service); (3) no levels → inconclusive
  - `_score_from_exit` — direct win/loss/inconclusive from actual exit price vs predicted levels
  - `run_journal_validation` — iterates all user journal entries, scores each, commits
  - `get_execution_stats` — per-card personal stats: total/won/lost/open, win_rate, entry list with outcomes
- [x] `api/broker.py` updates:
  - `GET /users/me/journal` — now joins JournalOutcome; returns outcome, target_price, stop_price, MAE, MFE per entry
  - `PATCH /users/me/journal/{entry_id}` — update exit price (close trade) or notes
  - `POST /users/me/journal/score` — background job: score all user journal entries
  - `JournalEntryOut` extended with outcome fields
- [x] `api/strategy_cards.py` — `GET /workspaces/{id}/strategy-cards/{id}/my-execution` returns ExecutionStats (total, won, lost, open, win_rate, entry list)
- [x] `pages/PaperTrading.tsx` updates:
  - `JournalCard` component — shows outcome badge, MAE/MFE, target/stop; "Close trade" inline form to record exit price
  - `ScoreButton` component — triggers journal scoring with feedback message
- [x] `pages/StrategyCardDetail.tsx` — "My execution" section: fetches personal stats, shows my win rate vs channel win rate (delta in ±pp), lists individual trades with outcomes
- [x] Full bilingual EN/ES translations for execution/scoring keys

## Phase 11 — Billing & payments (Stripe)
**Status:** COMPLETE

**Backend:**
- [x] `models/admin_audit_log.py` — every super-admin action logged (who, action, target workspace/user, details, timestamp)
- [x] `alembic/versions/008_billing_admin.py` — admin_audit_logs table + `current_period_start` and `stripe_subscription_item_id` added to subscriptions
- [x] `Subscription` model updated with the two new columns
- [x] `core/config.py` — STRIPE_STANDARD_PRICE_ID, FRONTEND_URL added
- [x] `requirements.txt` — stripe==11.4.1 added
- [x] `services/billing_service.py`:
  - `PLANS` dict — plan catalog as data, no hardcoded pricing in API layer
  - `sync_budget_cap` — auto-derives workspace.monthly_budget_cap from subscription + member count; only fires when sub is active/past_due
  - `get_period_spend` — sums non-overage usage_events since current_period_start (fixes the inverted period bug)
  - `is_budget_exhausted` / `is_payment_lapsed` — guards for poller
  - `get_or_create_stripe_customer`, `create_checkout_session`, `create_portal_session`
  - `handle_stripe_webhook` — dispatches subscription.created/updated/deleted, invoice.payment_succeeded/failed; respects manually_overridden_by
  - `update_seat_quantity` — calls Stripe SubscriptionItem.modify when member count changes
- [x] `api/billing.py`:
  - `GET /workspaces/{id}/billing` — BillingDetails: status, plan, period spend, budget %, overage, cost breakdown by task_type
  - `POST /workspaces/{id}/billing/checkout` — creates Stripe Checkout session (owner only), returns redirect URL
  - `POST /workspaces/{id}/billing/portal` — creates Stripe Billing Portal session
  - `POST /billing/webhook` — Stripe webhook receiver with signature verification
  - `GET /billing/plans` — available plan catalog (read-only)
- [x] `api/admin.py` (super_admin only, 403 for others):
  - `GET /admin/workspaces` — all workspaces with owner email, status, spend, member count
  - `POST /admin/workspaces` — create workspace on behalf of any user by email
  - `POST /admin/workspaces/{id}/members` — add member, triggers seat quantity update
  - `DELETE /admin/workspaces/{id}/members/{user_id}` — remove member (owner protected)
  - `PATCH /admin/workspaces/{id}/payment` — toggle payment_enabled + marks manually_overridden_by
  - `GET /admin/profitability` — revenue / cost / margin + at-risk workspaces (>80% of budget)
  - `GET /admin/audit-log` — recent admin actions
- [x] Bug fix in `api/ask.py` — overage check now uses `current_period_start` (was erroneously using `current_period_end`)
- [x] `workers/poller.py` — payment gate: skips ingestion/mining if payment lapsed OR budget cap exhausted; Ask view never gated

**Frontend:**
- [x] `pages/Billing.tsx` — subscription status badge, period spend / budget cap progress bar, overage bar, soft warning (>80%) + hard pause (100%) banners, Subscribe (Checkout) / Manage billing (Portal) buttons, cost breakdown table; auto-detects Stripe Checkout return via `?session_id=`
- [x] `pages/SuperAdmin.tsx` — three-tab view: Workspaces (table with spend/budget, toggle payment per workspace, create workspace panel), Profitability (revenue/cost/margin stats + at-risk list), Audit log; 403 guard for non-super-admins
- [x] NavBar — "Billing" link added
- [x] `/billing` and `/admin` routes added to App.tsx
- [x] Full bilingual EN/ES translations for all billing/admin keys

## Phase 12 — Help & documentation
**Status:** COMPLETE

- [x] **Help content authored in full EN and ES** — 8 sections covering every feature module: Ask, Sources (ingestion, content filters, backfill), Strategy Library, My Plan, Paper Trading, Billing & Cost Dashboard, Changelog, Admin/Config. Each section has a one-sentence summary and a detailed body with usage notes and tips. Spanish is a full translation, not a machine-generated pass.
- [x] `pages/Help.tsx` — searchable, section-navigable help view:
  - Sidebar nav (sticky) with active-section highlight; hidden during search to not obscure results
  - Client-side search filters across title + summary + body text of all sections
  - Deep-linkable via `?section=<id>` query param — used by HelpLink for contextual navigation
  - Auto-scrolls to the targeted section on load and when the param changes
  - `BodyText` renderer: splits body on `\n\n` into paragraphs; lines starting with `•` render as bullet rows
- [x] `components/HelpLink.tsx` — contextual `?` icon that navigates to the relevant Help section; placed on Ask and Sources pages; ready to add to any other view
- [x] `components/OnboardingModal.tsx` — 3-step first-login walkthrough:
  - Step 1: what TradeMind is
  - Step 2: add first source (action button to /sources)
  - Step 3: make first Ask (action button to /ask)
  - Progress bar across top; skip link; gated by `localStorage["trademind_onboarding_done"]` so it shows only once
  - Fully bilingual — reads from `onboarding.*` i18n keys, no hardcoded strings
- [x] `pages/Dashboard.tsx` — triggers OnboardingModal on first visit; adds a "Help" quick-link card alongside Ask and Sources
- [x] `components/NavBar.tsx` — `?` icon button in the header right section, always visible, navigates to `/help`
- [x] `/help` route added to App.tsx
- [x] All help content and onboarding strings in both EN/ES translation files — no hardcoded UI text anywhere (bilingual, in-app)
