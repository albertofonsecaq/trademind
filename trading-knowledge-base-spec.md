# Trading Knowledge Base — Project Spec

## Overview

A personal (initially) knowledge base product that ingests trading-related content from multiple sources, extracts structured trade ideas, mines recurring patterns across sources, validates those patterns against real market outcomes, and teaches validated strategies back to the user in an accessible way (plain language, diagrams, bilingual EN/ES).

This is not a signal-following bot. The system surfaces what has historically worked, with evidence and confidence — the user decides what to act on. No auto-execution of trades.

**Initial sources:** Telegram (multiple channels), YouTube (multiple channels).
**Architecture requirement:** every source must plug into the same schema/pipeline via a connector interface, so adding source #3+ later (e.g. market price data, a personal trade journal, Twitter/X, news APIs) requires no changes to core pipeline logic.

**Scale:** single user (me) initially, but design the schema, auth boundary, and every layer below so multi-user is an additive change later, not a rewrite.

**Multi-tenancy model: workspaces.** Sources (Telegram/YouTube channels) and the resulting knowledge base (evidence, strategy cards) belong to a workspace, not directly to a user. Every user gets a private workspace by default — behaving exactly like a fully isolated personal KB. A workspace can optionally gain more members later (e.g. a trading partner), sharing its sources and strategy cards without duplicating ingestion. Personal layers — plan, trade journal, broker orders — are always scoped to the individual user, never shared at the workspace level, regardless of how many members a workspace has.

**Platform connections are per-workspace, not shared, and a workspace can have more than one per platform.** For any messaging-style platform that requires a logged-in account to read from (Telegram now; WhatsApp or similar later), each workspace connects and authenticates its own account(s) rather than the app using one shared account across all workspaces. A single workspace isn't limited to one account per platform either — e.g. two separate Telegram accounts can coexist in the same workspace, each assigned to different channels, useful for spreading load across rate limits or reusing an account that's already a member of a private channel. API-key-based platforms with no personal login requirement (YouTube, market data) don't need this — a workspace just supplies/uses an API key or none at all.

**Delivery surface:** a web UI for learning/teaching (query, strategy library, personal plan, changelog), not just a backend pipeline — this is the primary way the user interacts with the system day to day.

**Cost visibility:** every LLM/transcription/vision call is logged with its cost, task type, and source, rolled up into a per-workspace budget the user can see and cap — so spend is visible and controllable, not a surprise at the end of the month.

**Per-source content filters:** each tracked source can selectively skip content types it doesn't need to process (e.g. skip videos in a Telegram channel that's mostly text chatter, skip images in a channel that never posts charts) — this both keeps the knowledge base focused and directly reduces vision/transcription cost on content that was never going to be useful.

**Topic relevance filter:** the system stays scoped to trading/markets content by design. Every piece of ingested content is checked against the workspace's `topic_scope` before any expensive processing runs — off-topic chatter, unrelated media, and irrelevant links never reach vision, transcription, or distillation. This is enforced as an early pipeline stage, not left to downstream retrieval/ranking to quietly deprioritize.

**Full bilingual UI:** the user chooses a display language (EN or ES) that applies to everything they see — not just ingested content and strategy cards, but every UI label, menu, and the in-app Help & documentation section covering all features.

**Billing:** workspaces can optionally have payment enforcement active, gated through a subscription with a payment provider (Stripe). A platform-level **super admin** role — distinct from and outside any single workspace's membership — can create workspaces on any user's behalf, add users to any workspace, and manually activate/deactivate payment enforcement per workspace, independent of the payment provider's own subscription status. Plan pricing is stored as data, not hardcoded, so it can change without a deploy. Pricing scales with team size: price and included compute budget both grow with `workspace_member` count beyond a plan's included seats, so a large multi-user workspace is priced for its actual scale rather than relying entirely on overage to cover it. Since some spend (query-driven usage from the Ask view) can't simply be paused without hurting the user's experience, usage beyond the seat-adjusted included budget is metered and billed as overage rather than absorbed as an unbilled loss — up to a hard overage ceiling, beyond which even querying pauses as a backstop against runaway or abusive spend.

---

## Tech stack (recommended defaults — adjust as needed)

- **Language:** Python
- **Database:** Postgres + `pgvector` extension (single source-agnostic embeddings table)
- **Telegram ingestion:** Telethon (MTProto client — required to read channels not administered by a bot); each workspace authenticates and stores its own session, no shared account
- **Credential storage:** `platform_connection.session_credential` and any other login-required platform tokens must be encrypted at rest (e.g. via the app's secrets manager or a KMS-backed encryption layer), never stored as plaintext in the database
- **YouTube ingestion:** YouTube Data API v3 + `youtube-transcript-api`, Whisper fallback for missing captions
- **Embeddings:** a multilingual embedding model (e.g. multilingual-E5 or OpenAI text-embedding-3) so EN/ES content shares one vector space
- **LLM tasks** (distillation, translation, pattern description, teaching content): Claude, via API
- **Vision/OCR:** Claude vision or a dedicated OCR+vision pipeline for chart screenshots
- **Transcription:** Whisper (local or API) for video/voice content
- **Task orchestration:** simple queue/worker model (e.g. Postgres-backed job queue, or Celery/RQ if complexity grows)
- **Reranking:** cross-encoder model for final ranking of retrieved candidates
- **Web UI:** a lightweight web framework (e.g. FastAPI backend + a simple React/HTMX frontend) serving the query, strategy library, plan, and changelog views
- **Authentication:** a standard auth library/service (e.g. FastAPI's own auth utilities, or an auth-as-a-service provider like Clerk/Auth0/Supabase Auth) rather than hand-rolled auth — cheap to set up correctly now, expensive to retrofit later
- **Broker integration (later phase):** Alpaca (supports both paper and live trading through the same API) as the default choice
- **Payment processing:** Stripe (Checkout + Billing + webhooks) — never handle raw card data directly; Stripe's PCI-compliant flows own that entirely
- **UI internationalization:** a standard i18n framework (e.g. `react-i18next` for a React frontend, or FastAPI + Jinja2 i18n extensions for a server-rendered UI) — every UI string externalized to EN/ES translation files from the start, not hardcoded inline

---

## Data model (core, source-agnostic)

```
workspace {                -- owns sources and the resulting knowledge base
  id
  name
  owner_user_id
  monthly_budget_cap        -- optional internal spend ceiling, null = no cap (distinct from what's charged to the customer)
  payment_enabled            -- manual override set only by a super_admin; independent of subscription.status
  topic_scope                -- plain-language description of what's in-scope, e.g. "stock trading, equities, market analysis, trading strategies"; drives the relevance filter, adjustable per workspace
  created_at
}

workspace_member {          -- links users to workspaces they belong to
  workspace_id
  user_id
  role                    -- owner | member
  joined_at
}

platform_connection {         -- a workspace's own logged-in account for a login-required platform
  id
  workspace_id
  platform                 -- "telegram", "whatsapp" (future), etc.
  label                    -- human-readable name to distinguish multiple accounts on the same platform, e.g. "Main Telegram", "Backup account"
  session_credential         -- encrypted session string/token, never stored in plaintext
  connected_by_user_id
  status                   -- pending | active | expired | revoked
  connected_at
  last_verified_at
}
-- A workspace may have more than one platform_connection for the same platform
-- (e.g. two separate Telegram accounts). Each source_config picks exactly one
-- connection to read through via platform_connection_id — there is no
-- one-connection-per-platform limit.

source_config {              -- a tracked Telegram/YouTube channel, belongs to a workspace
  id
  workspace_id
  platform_connection_id      -- required for login-required platforms, null for API-key-only sources (YouTube, market data)
  source_type              -- "telegram", "youtube", etc.
  identifier                -- channel handle/id
  fetch_cadence
  content_filters            -- which evidence types to ingest for this source, e.g. {text: true, image: true, video: false, url: true}
  backfill_start_date         -- optional; only ingest history from this date forward, null = full available history
  created_by_user_id
}

backfill_job {                -- an on-demand or initial historical ingestion run over a date range
  id
  source_config_id
  workspace_id
  date_range_start
  date_range_end             -- optional, null = up to now
  status                    -- queued | running | completed | failed
  items_ingested
  triggered_by_user_id
  created_at
}

embedding_row {
  id
  source_type        -- "telegram", "youtube", "market_data" (future), etc.
  source_id           -- stable unique id (channel+message, video+segment)
  workspace_id         -- scopes to the workspace's knowledge base, not directly to a user
  text                 -- normalized/distilled content actually embedded
  embedding            -- vector
  source_language      -- detected at ingestion
  metadata             -- symbol, author, channel, timestamp, evidence_type, etc.
  created_at
}

evidence_item {          -- raw extracted content, pre-distillation
  message_id
  source_type
  type                  -- text | image | video_transcript | url
  content
  confidence
  original_language
  is_on_topic              -- result of the relevance filter against workspace.topic_scope
  relevance_reason          -- short note on why it was judged on/off topic, for auditability
}

trade_idea {              -- structured output of distillation
  message_id
  source_language
  symbol
  setup_type
  action                -- long | short | info | commentary
  entry, target, stop
  summary_en, summary_es
  original_text          -- preserved, not discarded
  author, channel, timestamp
}

strategy_card {            -- output of pattern mining, scoped to a workspace
  id
  workspace_id
  setup_type
  symbol_scope           -- specific symbol or general pattern
  description_en, description_es
  flowchart_spec         -- structured entry/confirm/manage/exit logic
  supporting_evidence[]   -- linked trade_idea ids across sources
  win_rate, sample_size, confidence_interval
  version_history[]
  last_updated
}

plan_item {                -- personal curation layer, always per-user, never shared
  strategy_card_id
  user_id
  user_notes
  risk_tolerance_match
  status                  -- watching | adopted | rejected
}

user {                     -- present from day one, single row until multi-user
  id
  email
  auth_provider_id
  default_workspace_id      -- private workspace created automatically on signup
  preferred_language      -- en | es
  platform_role             -- "user" | "super_admin", default "user" — global, not scoped to any workspace
  created_at
}

journal_entry {             -- user's own executed trades, always per-user, future feedback source
  id
  user_id
  strategy_card_id          -- optional link back to the strategy that prompted it
  symbol, action, entry, exit, size
  broker_order_id
  mode                    -- paper | live
  timestamp
}

broker_order {               -- prepared, human-confirmed order, always per-user
  id
  user_id
  strategy_card_id
  symbol, action, entry, target, stop, size
  status                  -- proposed | confirmed | submitted | filled | rejected | cancelled
  mode                    -- paper | live
  confirmed_at             -- null until user explicitly confirms
  created_at
}

usage_event {                -- one row per billable API call, for cost tracking
  id
  workspace_id
  task_type                -- "distillation" | "translation" | "vision" | "transcription" | "query_synthesis" | "pattern_mining" | "embedding"
  provider_model            -- e.g. "claude-haiku-4-5", "whisper-1"
  input_units, output_units   -- tokens or minutes, unit depends on provider_model
  cost_usd
  is_overage                -- true if this call occurred after included_budget_usd was already exhausted for the current period
  source_id                 -- optional link back to the source_config that triggered this call
  created_at
}

subscription {                -- billing state for a workspace, one active row expected per workspace
  id
  workspace_id
  plan_name                 -- e.g. "standard" — pricing looked up from plan data, never hardcoded
  price_usd_monthly          -- e.g. 50.00; stored as data so it can change without a deploy
  included_budget_usd         -- e.g. 30.00; the compute allowance the price is calculated to cover — the gap to price_usd_monthly is the owner's margin
  included_seats              -- e.g. 3; number of workspace_members covered by the base price before per-seat charges apply
  price_per_seat_usd           -- e.g. 10.00; added to price_usd_monthly for each member beyond included_seats
  included_budget_per_seat_usd  -- e.g. 8.00; added to included_budget_usd for each member beyond included_seats — so compute allowance scales with team size, not just price
  overage_rate_multiplier      -- e.g. 1.5; applied to raw usage_event.cost_usd for anything billed as overage, so excess spend still carries margin
  overage_ceiling_usd          -- hard stop-loss on metered overage per period; once reached, even query synthesis pauses until upgrade or period reset — the backstop against runaway or abusive spend, distinct from normal overage
  current_period_overage_usd   -- running total of metered overage accrued this billing period, reported to Stripe as metered usage
  payment_provider_customer_id
  payment_provider_subscription_id
  status                    -- inactive | active | past_due | canceled
  manually_overridden_by      -- super_admin user_id, if status was set manually rather than by webhook
  current_period_end
  created_at
}
-- When a subscription is active, workspace.monthly_budget_cap is set from
-- included_budget_usd + (extra_seats × included_budget_per_seat_usd), where
-- extra_seats = max(0, workspace_member count - included_seats). Deferrable
-- jobs (ingestion, pattern mining) pause once this cap is reached, same as
-- any other budget cap. Query-driven spend (Ask view) is never paused for
-- UX reasons — instead, once the (seat-adjusted) included budget is
-- exhausted, further query_synthesis usage_events are tagged is_overage:
-- true and billed as metered overage at period end, up to overage_ceiling_usd.
-- Only beyond that hard ceiling does query synthesis itself pause — the
-- backstop against genuinely runaway spend (e.g. a bug, a script, abuse),
-- not something normal usage variance should ever reach.
```

---

## Feature list by module

### 1. Ingestion

**Shared / connector framework**
- [ ] Define a common connector interface: `fetch()`, `normalize()`, config (fetch cadence, source-specific settings)
- [ ] Every tracked source (`source_config`) belongs to a workspace, not directly to a user — added via the workspace's admin/config view by any workspace member
- [ ] **Per-source content-type filters**: each source can independently toggle which evidence types it ingests (text, image, video, url) — e.g. skip video entirely for a channel that never posts anything relevant on video, skip images for a text-only channel
- [ ] Filtered-out content types are skipped before extraction, not after — no vision/transcription cost is incurred on a type the source has disabled
- [ ] **Configurable backfill start date per source** (`source_config.backfill_start_date`) — when adding a channel, optionally ingest history only from a chosen date forward instead of the full available history
- [ ] **On-demand date-range backfill** (`backfill_job`): at any later point, trigger a fresh ingestion run over a specific `[date_range_start, date_range_end]` window for an existing source — e.g. "pull everything from this channel between March 1 and March 15," independent of the source's original backfill setting
- [ ] Backfill jobs are tracked with status (queued/running/completed/failed) and items-ingested count, visible in the UI — not a silent background action
- [ ] Date-range backfill respects each platform's native way of filtering by date (Telegram: message timestamp via history API; YouTube: video publish date)
- [ ] Per-source dedup using stable IDs (no reprocessing on re-fetch) — re-running a backfill over an overlapping date range doesn't duplicate already-ingested items
- [ ] Language detection at ingestion time, tagged on every record
- [ ] Config-driven source registration (add a new tracked channel without code changes)
- [ ] Per-source fetch cadence (some channels/videos need near-real-time, others daily)

**Telegram**
- [ ] **Per-workspace account connection flow**: workspace admin authenticates their own Telegram account (phone number + OTP via Telethon) once; resulting session stored encrypted as a `platform_connection` scoped to that workspace
- [ ] **A workspace may connect more than one Telegram account** (e.g. to spread channels across accounts for rate-limit or membership reasons) — each is its own `platform_connection` row with a distinct label
- [ ] No shared/global Telegram account across workspaces — every connected account belongs to exactly one workspace only
- [ ] When adding a channel as a source, the admin selects which of the workspace's connected accounts to read it through (defaults to the only one if there's just one)
- [ ] Connection status surfaced in the UI per account (pending / active / expired / revoked) so a broken login is visible, not a silent ingestion failure
- [ ] Re-authentication flow for when a session expires or is revoked by Telegram, scoped to the specific account affected
- [ ] Track multiple channels per workspace, each as its own configured data source under whichever connection it's assigned to
- [ ] Real-time ingestion where possible (event-driven), polling fallback
- [ ] Full thread/context resolution (pull complete context around a message, not just the message in isolation)
- [ ] Forwarded-post origin resolution (trace back to source, don't treat forward as new content)
- [ ] Per-author tracking within each channel
- [ ] Private/invite-only channels require the assigned connected account to join manually before the channel can be added as a source

**YouTube**
- [ ] Track multiple channels via YouTube Data API
- [ ] New-video detection (polling)
- [ ] Native caption pull when available
- [ ] Whisper transcription fallback when captions unavailable
- [ ] Topic/timestamp-based chunking of long videos into multiple evidence pieces (not one blob per video)

**Future login-required platforms (e.g. WhatsApp)**
- [ ] Same pattern as Telegram: per-workspace `platform_connection`, own authenticated session, no shared cross-workspace account
- [ ] New platform = new connector implementation + a new `platform` value, no changes to `source_config`, ingestion pipeline, or downstream schema
- [ ] WhatsApp specifically will need its own connection flow (e.g. WhatsApp Business API or a linked-device session, to be decided when this source is actually built) — noted here as a placeholder, not yet speced in detail

---

### 2. Media extraction

- [ ] **Topic relevance filter (runs first, before any expensive processing)**: a cheap classification pass checks incoming content against the workspace's `topic_scope` — anything judged off-topic (not trading/markets-related) is tagged `is_on_topic: false` and skipped from every downstream step: no vision call, no transcription, no distillation
- [ ] For text-first content, the relevance check runs directly on the message text — cheap and fast, filters out pure off-topic chatter before anything else happens
- [ ] For image/video content with no usable caption, relevance can't be judged from text alone — the vision/transcription pass itself doubles as the relevance judgment (e.g. "is this a genuine trading chart or an unrelated meme/personal photo"), so cost is only spent once, not as a separate redundant check
- [ ] `workspace.topic_scope` is a plain-language, editable description (default: stock trading, equities, market analysis, trading strategies) — adjustable if the user wants to broaden scope (e.g. include crypto/forex) or narrow it further
- [ ] Off-topic content is never silently deleted — `evidence_item.is_on_topic = false` plus a short `relevance_reason` are kept for auditability and for spot-checking that the filter isn't over- or under-triggering
- [ ] Text extraction (native passthrough)
- [ ] Image OCR (text-in-image) + vision-based chart description (pattern, drawn levels, annotations)
- [ ] Video: audio transcription (Whisper) + key-frame extraction for on-screen charts
- [ ] URL fetch + readable content extraction for linked articles
- [ ] Confidence scoring attached to every extracted evidence item

---

### 3. Distillation (structured extraction)

- [ ] **Distillation only runs on evidence tagged `is_on_topic: true`** — this is the main cost payoff of the relevance filter, since distillation (with its bilingual summary generation) is one of the highest-frequency LLM calls in the whole pipeline
- [ ] LLM extraction pass: raw evidence → structured `trade_idea` (symbol, setup_type, action, entry/target/stop, timestamp)
- [ ] Bilingual canonical summary generation (EN + ES) regardless of source language
- [ ] Original untranslated text always preserved alongside translation
- [ ] Message-level extraction
- [ ] Burst-level extraction (catch signal buried in long threads/videos that a whole-message/video summary would miss) — relevance filter still applies at the burst level, not just whole-message
- [ ] Author + channel + timestamp attached to every structured record
- [ ] Symbol/ticker normalization (consistent format across sources)

---

### 4. Storage / retrieval core

- [ ] Single Postgres + pgvector embeddings table, source-agnostic schema
- [ ] Multilingual embedding model integration
- [ ] Full-text (exact match) index for tickers/tokens
- [ ] IDF-based rarity weighting in scoring
- [ ] Recency decay in ranking (configurable per content type — trade signals decay faster than general commentary)
- [ ] Hybrid retrieval: full-text + embedding + IDF + recency, fused via RRF
- [ ] Cross-encoder reranking of fused candidates
- [ ] Context expansion on final results (pull neighboring context back in after chunking)
- [ ] Query interface: ask a question, get an answer with citations back to source evidence

---

### 5. Pattern mining

- [ ] Recurring job clustering `trade_idea` records by symbol / setup_type / indicator combination
- [ ] Language-blind clustering (operate on structured fields + canonical embedding, never raw source text)
- [ ] Cross-source corroboration weighting (same setup flagged on Telegram *and* YouTube = stronger signal)
- [ ] Per-author and per-channel reliability scoring (feeds into cluster confidence)
- [ ] LLM-generated `strategy_card` description per cluster

---

### 6. Validation

- [ ] Market/price data connector (new source, same connector interface) — e.g. Polygon, Alpha Vantage
- [ ] Outcome checker: for each `trade_idea` with entry/target/stop, did target hit before stop within a defined window
- [ ] Win rate + sample size + confidence interval computed per `strategy_card` (never win rate alone)
- [ ] Recency-weighted validation (recent outcomes matter more)
- [ ] Walk-forward validation check (pattern re-tested on data outside what defined it) to guard against overfitting

---

### 7. Learning loop

- [ ] Recurring recomputation job: refresh strategy card stats as new evidence/outcomes arrive
- [ ] Versioning of `strategy_card` — never overwrite silently, keep history
- [ ] Change surfacing: show what changed between versions and why (e.g. win rate shift, new sample size)
- [ ] Confidence tiering: flag low-sample-size patterns as "still learning" distinctly from established ones

---

### 8. Teaching layer

- [ ] Strategy card rendering: plain-language explanation
- [ ] Flowchart/diagram generation from `flowchart_spec` (entry → confirmation → risk management → exit)
- [ ] Real cited examples pulled from ingested evidence, linked to original source
- [ ] Honest stats display (win rate, sample size, confidence — including failure cases, not just wins)
- [ ] Bilingual output (EN/ES) selectable independent of source language
- [ ] Original-language source text viewable alongside translation

---

### 9. Personal plan layer

- [ ] Curated view: subset of strategy cards matched to user-defined instruments/risk tolerance
- [ ] Review/adjust workflow (accept, reject, annotate — no auto-execution)
- [ ] Placeholder connector slot for future personal trade journal source (score actual execution against strategy cards)

---

### 10. Web UI

- [ ] **Ask view** — chat/query box against the knowledge base, cited answers, EN/ES toggle
- [ ] **Strategy library view** — browsable/searchable strategy cards: explanation, flowchart, cited examples, win rate/sample size/confidence, version history
- [ ] **My plan view** — curated `plan_item` list (watching/adopted/rejected) with personal notes
- [ ] **Activity/changelog view** — surfaces what changed on strategy cards and why, chronologically
- [ ] **Admin/config view** — manage the current workspace's platform connections (connect additional accounts per platform, reconnect, view per-account status), tracked channels/sources with which connection each uses, fetch cadences, per-source content-type filters, backfill start date, on-demand date-range re-backfill runs (with live status), and the workspace's `topic_scope` definition, without touching code
- [ ] **Cost dashboard view** — spend by task type and source, budget cap status, soft-warning and hard-pause thresholds
- [ ] **Full UI internationalization**: `user.preferred_language` (EN/ES) drives everything the user sees — not just bilingual ingested content (already covered in Distillation/Teaching layer), but every UI label, button, menu, error message, onboarding flow, and the Help & documentation section itself
- [ ] Language preference set once during onboarding, changeable anytime from account settings, applied instantly across every view without needing a reload
- [ ] Every answer/card in the UI links back to original source evidence (Telegram message, YouTube timestamp)

---

### 11. Broker / execution layer (later phase)

- [ ] Broker connector (Alpaca or similar) implemented as a connector like any other source, but for writes instead of reads
- [ ] **Paper trading mode as the default and initial-only mode** — live trading is a deliberate, separate opt-in later, not the default
- [ ] Order preparation from an adopted `strategy_card` / `plan_item` — proposes symbol, entry, target, stop, size
- [ ] **Mandatory human confirmation on every order** — no order transitions from `proposed` to `submitted` without explicit user action; this is a hard constraint, not a setting
- [ ] Order status tracking (proposed → confirmed → submitted → filled/rejected/cancelled)
- [ ] Trade journal (`journal_entry`) capturing every executed trade, paper or live, as its own evidence source
- [ ] Journal entries feed back into the learning loop — validation layer can score the user's own execution against strategy card predictions, separately from the channels' original calls

---

### 12. Authentication & multi-user readiness (workspace model)

- [ ] Standard auth provider/library integration (not hand-rolled), supporting email/password or OAuth at minimum
- [ ] On signup, every user automatically gets a private `workspace` (they are its sole `workspace_member`, role `owner`) — this is what makes single-user feel simple today while staying multi-user-ready
- [ ] Sources (`source_config`), ingested evidence (`embedding_row`), and `strategy_card` records are scoped to `workspace_id`, never directly to `user_id`
- [ ] Personal layers — `plan_item`, `journal_entry`, `broker_order` — are always scoped to `user_id` directly, regardless of workspace membership, and are never visible to other workspace members
- [ ] Inviting a second member to a workspace is an additive feature (add a `workspace_member` row) — requires no schema change, only an invite/accept flow when built
- [ ] Session management (login, logout, session expiry)
- [ ] All queries and background jobs scoped by `workspace_id` (for shared data) or `user_id` (for personal data) — never a global unscoped query, even with only one workspace in the system
- [ ] `user.default_workspace_id` determines which workspace the UI loads into on login; a workspace switcher UI element is a placeholder for later, not needed while every user has exactly one workspace

---

### 13. Cost tracking & budget management

- [ ] Every billable call (distillation, translation, vision, transcription, query synthesis, pattern mining, embeddings) logged as a `usage_event` with task type, provider/model, units consumed, and computed cost
- [ ] Cost dashboard in the UI: spend broken down by task type and by source, over selectable time ranges (today, this week, this month)
- [ ] Optional `workspace.monthly_budget_cap` — when set, the system tracks cumulative spend against it. For workspaces with an active paid `subscription`, this cap is driven automatically by `subscription.included_budget_usd` rather than set manually (see Billing & payments) — this is what guarantees the owner's margin structurally rather than by assumption
- [ ] **Soft warning** at a configurable threshold (e.g. 80% of budget) surfaced in the UI
- [ ] **Hard pause** at 100% of budget for non-essential/deferrable jobs only (backfill, pattern mining recomputation) — never pauses the Ask view or anything the user is actively waiting on. For paid workspaces, spend that can't be paused (query synthesis) instead becomes metered overage once the cap is reached (see Billing & payments) — pause and overage billing together are what make the budget cap meaningful for every type of spend, not just the pausable kind
- [ ] Per-source content-type filters (see Ingestion) are the primary lever surfaced to the user for reducing spend directly from the source list, not just after the fact from a dashboard
- [ ] Backfill start date and on-demand date-range backfills (see Ingestion) are the second major cost lever — bounding how much history gets pulled and processed directly bounds distillation/vision/transcription spend on a backfill run
- [ ] The topic relevance filter (see Media extraction) is the third major cost lever, and typically the largest one — off-topic content never reaches distillation at all, which is where most of the per-item LLM cost sits
- [ ] Cost estimates shown at the point of adding a new source (e.g. "this channel posts ~5 videos/week, transcription will add roughly $X/month") where estimable

---

### 14. Cross-cutting / product-level

- [ ] Auditing: every synthesized answer traceable back to specific source evidence
- [ ] Cost controls: pre-filtering before expensive vision/transcription/LLM calls
- [ ] Logging/observability on ingestion pipeline (catch silent failures per source)

---

### 15. Billing & payments (super admin)

- [ ] Stripe integration (Checkout + Billing Portal + webhooks) for subscription billing — no raw card data ever touches the app's own database or servers
- [ ] `subscription` entity per workspace, with plan, price, and `included_budget_usd` stored as data (not hardcoded), so pricing/tiers can change without a code deploy
- [ ] Default plan: $50/month standard tier with `included_budget_usd` = $30 — a starting point, not fixed; revisit both numbers once real `usage_event` cost data shows actual per-workspace compute cost
- [ ] **Margin is structurally enforced, not assumed**: when a subscription is active, `workspace.monthly_budget_cap` is automatically set from `subscription.included_budget_usd` — so a workspace's real compute spend is mechanically bounded by what its price was calculated to cover, using the pause behavior already built in Cost tracking rather than a separate mechanism
- [ ] **Seat-based pricing**: price and included budget both scale with `workspace_member` count beyond `included_seats` — a 30-person workspace is priced and budgeted for 30 people from the start, not left to absorb that scale entirely through overage on a personal-tier plan
- [ ] Adding a member beyond `included_seats` triggers a Stripe subscription quantity update (per-seat line item), so the next invoice reflects the new price automatically
- [ ] **Overage billing for spend that can't be paused**: query synthesis (Ask view) is never blocked, so once the seat-adjusted `included_budget_usd` is exhausted for the period, further query-driven `usage_event` rows are tagged `is_overage: true` and metered as billed overage via Stripe (at `overage_rate_multiplier` over raw cost) rather than being an unbilled loss — this covers normal usage variance within a plan
- [ ] **Overage ceiling as a hard backstop**: `overage_ceiling_usd` caps how much overage a workspace can accrue in a period before query synthesis itself pauses — this is distinct from the soft included-budget threshold, and exists to protect against runaway or abusive spend (e.g. a bug, a script, deliberate abuse), not normal usage. Reaching this point should be rare; if a workspace reaches it regularly, that's a signal it's on the wrong plan/seat tier, not that overage billing failed
- [ ] Overage accrual visible to the workspace in real time (not just at invoice time) — the cost dashboard shows current-period overage separately from included spend, so there's no bill surprise
- [ ] Overage is billed at period end via Stripe metered usage reporting, added to the next invoice alongside the flat subscription charge
- [ ] **Owner profitability dashboard** (super admin only): per-workspace and aggregate view of subscription revenue vs. actual `usage_event` cost vs. realized margin, over selectable time ranges — this is what validates or corrects the $50/$30 assumption against real data instead of leaving it as a guess
- [ ] Alert to super admin if a workspace's actual cost trend is approaching its `included_budget_usd` before the budget-cap pause kicks in — early warning that a specific workspace's usage pattern doesn't fit the current plan, before it becomes a margin problem
- [ ] **Super admin role** (`user.platform_role = "super_admin"`): global, not scoped to any single workspace
- [ ] Super admin can create a workspace on behalf of any user
- [ ] Super admin can add or remove users from any workspace (bypassing the normal invite/accept flow)
- [ ] Super admin can manually activate or deactivate payment enforcement per workspace (`workspace.payment_enabled`), independent of what the payment provider's own subscription status says — e.g. to comp an account or override a billing edge case
- [ ] Webhook handler keeps `subscription.status` in sync automatically with the payment provider (payment succeeded, failed, subscription canceled); a super admin's manual override always takes precedence over the webhook-driven status when both are present
- [ ] All super admin actions are audited (who did what, to which workspace, when) — same principle as auditing everywhere else in the spec
- [ ] **Behavior when payment lapses** (assumed default, confirm/adjust as needed): background ingestion and other cost-generating jobs pause immediately, mirroring the existing budget-cap pause behavior; already-ingested content stays viewable read-only rather than the workspace being locked out entirely, with a clear prompt to reactivate billing
- [ ] Separate platform-level admin/back-office view, visible only to `super_admin` users — distinct from the regular per-workspace Admin/config view

#### Workspace Clone

Super admins can clone an existing workspace's full knowledge base to a new workspace owned by a different user. The workflow is two-step: first create the target user account (via the Create User panel in the admin view), then trigger the clone.

**What is cloned:** topic scope, source channel definitions (identifier, source type, fetch cadence, content filters), all evidence items, embeddings, trade ideas, outcome checks, and strategy cards. Internal UUID references (evidence_item_id on trade_ideas, trade_idea_id on outcome_checks, supporting_evidence UUID list on strategy_cards) are remapped to the new workspace's IDs.

**What is reset on clone:** source configs start with no platform connection (`platform_connection_id = NULL`) and no fetch state (`last_fetched_id`, `last_fetched_at`, `backfill_start_date` are NULL). The receiving user connects their own Telegram (or other platform) account and manages ingestion cadence from there.

**What is not cloned:** platform credentials (`platform_connections`), subscription and billing records (`subscriptions`, `usage_events`, `backfill_jobs`), and all user-personal data (`plan_items`, `journal_entries`, `broker_orders`, `journal_outcomes`).

**Invariants:**
- The cloned workspace always starts with `payment_enabled = false` — billing is never inherited
- Platform session credentials are never transferred across workspaces (enforces the "no shared platform account" non-goal)
- Every create-user and clone-workspace action is recorded in `admin_audit_logs` with action strings `"create_user"` and `"clone_workspace"` respectively

---

### 16. Help & documentation

- [ ] **In-app Help view**, accessible from anywhere in the UI (not buried), covering every feature module in plain language: what it does, why it exists, and how to use it — Ingestion & sources (connecting platforms, adding channels, content filters, backfill dates), Ask view, Strategy library, My plan, Broker/paper trading, Cost dashboard, Billing/subscription, Admin/config
- [ ] **Fully bilingual**, using the same `user.preferred_language` mechanism as the rest of the UI — help content is authored and maintained in both EN and ES, not machine-translated on the fly, so the explanations read naturally in either language
- [ ] Contextual help: relevant tooltips/inline hints on each view link directly to the matching section of the Help view, rather than requiring the user to search from scratch
- [ ] Searchable within the Help view itself
- [ ] Help content versioned alongside feature releases — when a feature's behavior changes (e.g. a new content-type filter, a new billing rule), the corresponding help entry is part of that change, not a follow-up task
- [ ] A short onboarding walkthrough on first login, in the user's chosen language, pointing to the Help view for anything not covered inline

---

## Suggested build order

1. **Auth + workspace scaffolding** (signup auto-creates a private workspace per user; `workspace_id`/`user_id` wired everywhere from the start — cheapest to do first, expensive to retrofit). Wire `usage_event` logging into the connector/LLM-call framework at this stage too — every task type should log spend from its very first call, not retrofitted once cost becomes a concern. Set up the i18n framework here too — externalizing UI strings from the start is far cheaper than retrofitting translations onto an already-built UI.
2. **Telegram text ingestion → distillation → embeddings → search working end-to-end**, exposed via a minimal Ask view (fastest path to a usable, queryable knowledge base)
3. YouTube ingestion (text/transcript path only first)
4. Media extraction: images, then video, then URLs
5. Pattern mining (needs accumulated data to be meaningful — don't build too early)
6. Market data connector + validation layer
7. Learning loop (versioning, recurring recomputation)
8. Teaching layer (diagrams, bilingual rendering) + Strategy library / My plan / Changelog views
9. Broker connector, paper trading mode only, with confirmation workflow
10. Trade journal feedback loop into validation
11. Billing & payments (Stripe, subscription entity, super admin tooling) — build once ready to onboard workspaces beyond personal use
12. Help & documentation — written and translated alongside each feature as it's built (see step 1's i18n note), with a final pass once the full UI is stable to fill any gaps

---

## Explicit non-goals (for Claude Code to respect)

- No automated trade execution, ever — every order requires explicit human confirmation before submission
- No live trading as a default or unprompted mode — paper trading is the default; live is a deliberate, separate opt-in built later
- No personalized "buy/sell now" directives — output is educational/evidence-based, framed as historical pattern performance, not advice
- No discarding of original-language source text in favor of translations
- No single retrieval method trusted alone (always hybrid + fusion)
- No hand-rolled authentication/session logic — use a vetted library or provider
- No shared platform account (Telegram, or any future login-required platform) reused across multiple workspaces — every workspace connects and authenticates its own account
- No plaintext storage of platform session credentials or tokens
- No silent budget overruns — spend is always visible before it's a surprise, and a hard budget cap pauses deferrable jobs rather than failing silently or continuing to spend unbounded
- Budget pausing never blocks the Ask view or anything the user is actively waiting on — only backfill and recurring recomputation jobs are deferrable
- No direct handling or storage of raw payment card data — always through a PCI-compliant provider (Stripe)
- No hardcoded subscription pricing in code — plan price is data, changeable without a deploy
- No silent billing state changes — a workspace's payment status change (lapse, reactivation, super admin override) is always visible to that workspace's members, not just logged internally
- No paid workspace allowed to accrue compute cost beyond its plan's `included_budget_usd` without triggering the same pause behavior as any other budget cap — margin protection is automatic, not dependent on someone noticing a spend report after the fact
- No off-topic content reaches vision, transcription, or distillation — the relevance filter is a mandatory early pipeline stage for every source and every content type, not an optional or best-effort step
- No unbounded, unbilled compute spend on a paid workspace — anything that can't be paused (query synthesis) is metered and billed as overage once the included budget is exhausted, rather than being left as an uncapped loss to the owner
- No workspace running significantly more members than its plan's included seats without price scaling accordingly — seat count changes trigger a pricing update, not silent absorption via overage alone
- No unlimited overage exposure — every workspace has a hard `overage_ceiling_usd`; beyond it, query synthesis pauses too, rather than the owner extending unbounded credit on the assumption a bill will eventually be paid
- No hardcoded UI strings — every label, message, and Help entry is sourced from the EN/ES translation layer, so adding a language later doesn't require hunting through code
- No Help & documentation content that exists in only one language — EN and ES are maintained together, not ES as an afterthought translation pass
