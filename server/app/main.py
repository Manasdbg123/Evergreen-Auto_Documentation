"""The FastAPI app. `uvicorn app.main:app --reload`.

Thin by design. Every route is a call into the pipeline modules plus a bit of
HTTP; no business logic lives here, because everything the API can do must
also be doable from the CLI on a machine with no server running.

No auth, no multi-tenancy, no deployment concerns — out of scope for this MVP
and deliberately so.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .db import init_db
from .routes import diffs, documents, jobs


@asynccontextmanager
async def lifespan(api: FastAPI):
    cfg = load_config()
    path = init_db(cfg)
    cfg.jobs_root.mkdir(parents=True, exist_ok=True)
    print(f"[api] database {path}")
    print(f"[api] jobs      {cfg.jobs_root}")
    print(f"[api] provider  {cfg.llm.provider} (offline={cfg.llm.offline})")

    # Screenshots are served straight off disk. The alternative — a route that
    # reads and streams each file — would add nothing but a chance to get the
    # path handling wrong.
    api.mount("/files", StaticFiles(directory=cfg.jobs_root), name="files")
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Evergreen",
    description="Screen recordings in, structured SOPs out, with a reviewable "
                "diff when the workflow is recorded again.",
    version="0.1.0",
)

# The Vite dev server runs on another port, so the browser treats it as a
# different origin. Wide open because this only ever runs on localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(documents.router)
app.include_router(diffs.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    cfg = load_config()
    return {
        "ok": True,
        "provider": cfg.llm.provider,
        "offline": cfg.llm.offline,
        "models": cfg.models.model_dump(),
    }


@app.get("/api/config")
def get_config() -> dict[str, object]:
    """The whole tunable surface.

    Exposed because the client-configurable surface is part of the pitch —
    thresholds, models, tone and granularity are a selling point, so they are
    inspectable rather than buried in a YAML file on someone's laptop.
    """
    return load_config().model_dump()
