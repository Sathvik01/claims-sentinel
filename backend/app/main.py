from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import admin, documents, findings, ingest
from app.config import PROJECT_DIR, settings
from app.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Claims Sentinel API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
for r in (ingest.router, documents.router, findings.router, admin.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True}


if settings.sample_data_dir.exists():  # downloadable sample files for the ingestion/documents pages
    app.mount("/samples", StaticFiles(directory=settings.sample_data_dir), name="samples")

_dist = PROJECT_DIR / "frontend" / "dist"
if _dist.exists():  # single-process demo: `npm run build` then serve the SPA from FastAPI
    app.mount("/", StaticFiles(directory=_dist, html=True), name="spa")
