from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://trademind:trademind@localhost:5432/trademind"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://trademind:trademind@localhost:5432/trademind"

    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    CORS_ORIGINS: List[str] = ["http://localhost:5173"]

    DEFAULT_TOPIC_SCOPE: str = (
        "stock trading, equities, market analysis, trading strategies, "
        "technical analysis, chart patterns, options trading"
    )

    # Phase 2: LLM
    ANTHROPIC_API_KEY: str = ""
    RELEVANCE_MODEL: str = "claude-haiku-4-5-20251001"
    DISTILLATION_MODEL: str = "claude-sonnet-4-6"
    SYNTHESIS_MODEL: str = "claude-sonnet-4-6"

    # Phase 2: Embeddings
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-base"
    EMBEDDING_DIM: int = 768

    # Phase 6: Validation
    # Market data: yfinance used by default (no key needed). Set POLYGON_API_KEY for better rate limits.
    POLYGON_API_KEY: str = ""
    VALIDATION_WINDOW_DAYS: int = 30   # days after signal to check target/stop
    VALIDATION_DECAY_HALF_LIFE_DAYS: int = 180  # recency-weighting half-life (6 months)

    # Phase 3: YouTube
    YOUTUBE_API_KEY: str = ""
    # Whisper fallback via OpenAI API (optional — videos without captions are skipped if unset)
    OPENAI_API_KEY: str = ""
    WHISPER_MODEL: str = "whisper-1"
    # Max seconds per transcript chunk (≈5 min); increase for longer-form analysis
    YOUTUBE_CHUNK_SECONDS: int = 300

    # Phase 11: Stripe billing
    # Create a Product + Price in your Stripe Dashboard and paste the Price ID here.
    # The price should be recurring/monthly. Leave empty to disable Checkout.
    STRIPE_STANDARD_PRICE_ID: str = ""
    # Frontend URLs for Stripe redirect after Checkout/Portal
    FRONTEND_URL: str = "http://localhost:5173"

    # Phase 9: Alpaca paper trading
    # If unset, submit returns a 503 instructing the user to configure keys.
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    ALPACA_PAPER_BASE_URL: str = "https://paper-api.alpaca.markets"

    # Phase 11+
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""


settings = Settings()
