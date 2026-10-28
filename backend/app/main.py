from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.log_setup import configure_logging
from app.core.llm_errors import install_llm_error_handlers
from app.api import auth, users, workspaces, connections, sources, ask, strategy_cards, plan_items, validation, changelog, broker, billing, admin, analysis_runs, evidence

configure_logging(settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.core.database import AsyncSessionLocal
    from app.services.analysis_run_service import close_interrupted_runs
    from app.services.keyframe_service import warn_if_ffmpeg_missing
    from app.workers.poller import create_scheduler

    # Runs are carried out inside this process: one still marked running now
    # was cut off by the restart, and would refuse every run of its kind after it.
    async with AsyncSessionLocal() as db:
        await close_interrupted_runs(db)

    warn_if_ffmpeg_missing()

    scheduler = create_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="TradeMind API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_llm_error_handlers(app)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(workspaces.router)
app.include_router(connections.router)
app.include_router(sources.router)
app.include_router(ask.router)
app.include_router(strategy_cards.router)
app.include_router(evidence.router)
app.include_router(plan_items.router)
app.include_router(validation.router)
app.include_router(analysis_runs.router)
app.include_router(changelog.router)
app.include_router(broker.router)
app.include_router(billing.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
