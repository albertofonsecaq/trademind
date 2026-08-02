from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import auth, users, workspaces, connections, sources, ask, strategy_cards, plan_items, validation, changelog, broker, billing, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.workers.poller import create_scheduler
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

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(workspaces.router)
app.include_router(connections.router)
app.include_router(sources.router)
app.include_router(ask.router)
app.include_router(strategy_cards.router)
app.include_router(plan_items.router)
app.include_router(validation.router)
app.include_router(changelog.router)
app.include_router(broker.router)
app.include_router(billing.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
