from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import httpx

app = FastAPI(title="Caching Showcase API")

# Minimal async SQLAlchemy setup (in-memory SQLite for the hello-world demo)
DATABASE_URL = "sqlite+aiosqlite:///./demo.db"
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@app.get("/")
async def root():
    return {"message": "Hello World from FastAPI"}


@app.get("/health")
async def health():
    # Quick DB ping
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT 1"))
        db_ok = result.scalar() == 1
    return {"status": "ok", "db": db_ok}


@app.get("/external")
async def call_external():
    """Demo httpx call to a public API."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get("https://httpbin.org/get")
        return {"status_code": resp.status_code, "origin": resp.json().get("origin")}
