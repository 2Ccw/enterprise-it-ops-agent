from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field


ROOT = Path(os.getenv("IT_OPS_ROOT", Path(__file__).resolve().parents[2]))
DB_PATH = Path(os.getenv("IT_OPS_DB", ROOT / "data" / "it_ops.db"))
ARTICLES_PATH = ROOT / "knowledge" / "articles.json"
ASSETS_PATH = ROOT / "knowledge" / "assets.json"
API_KEY = os.getenv("IT_OPS_API_KEY", "local-it-ops-key-change-me")
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("it_ops")
requests_by_key: dict[str, deque[float]] = defaultdict(deque)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    category: str = Field(default="general", max_length=40)
    top_k: int = Field(default=4, ge=1, le=8)


class TicketDraftRequest(BaseModel):
    summary: str = Field(min_length=5, max_length=2000)
    category: str = Field(min_length=2, max_length=40)
    priority: str = Field(pattern="^(P1|P2|P3|P4)$")
    asset_tag: str | None = Field(default=None, max_length=40)
    requester_alias: str = Field(default="anonymous-demo", max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=100)


class ReviewRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=80)
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str = Field(default="", max_length=500)


@contextmanager
def session():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with session() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ticket_drafts(
              id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
              summary TEXT NOT NULL, category TEXT NOT NULL, priority TEXT NOT NULL,
              asset_tag TEXT, requester_alias TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending_review', reviewer TEXT,
              review_note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_logs(
              id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
              action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT,
              detail TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def text_tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff-]+", "", text.lower())
    return set(normalized) | {normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))}


def retrieve(query: str, category: str, top_k: int) -> list[dict]:
    query_tokens = text_tokens(query)
    results: list[tuple[float, dict]] = []
    for article in load_json(ARTICLES_PATH):
        if category not in {"general", "unknown"} and article["category"] != category:
            continue
        article_tokens = text_tokens(article["title"] + article["content"] + " ".join(article["keywords"]))
        score = len(query_tokens & article_tokens) / max(1, len(query_tokens))
        score += sum(0.15 for keyword in article["keywords"] if keyword.lower() in query.lower())
        if score > 0.05:
            results.append((min(score, 1.0), article))
    results.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "citation_id": article["citation_id"],
            "category": article["category"],
            "title": article["title"],
            "content": article["content"],
            "document_version": article["version"],
            "effective_date": article["effective_date"],
            "score": round(score, 4),
        }
        for score, article in results[:top_k]
    ]


def verify_key(x_it_ops_key: str | None = Header(default=None)) -> str:
    if not x_it_ops_key or x_it_ops_key != API_KEY:
        logger.warning(
            json.dumps(
                {
                    "event": "authentication_failed",
                    "header_present": bool(x_it_ops_key),
                    "key_match": False,
                }
            )
        )
        raise HTTPException(401, "invalid IT operations key")
    now = time.time()
    bucket = requests_by_key[x_it_ops_key]
    while bucket and bucket[0] < now - 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        raise HTTPException(429, "rate limit exceeded")
    bucket.append(now)
    return x_it_ops_key


def request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID", str(uuid.uuid4()))


def audit(trace_id: str, action: str, entity_type: str, entity_id: str | None, detail: dict) -> None:
    safe_detail = {key: value for key, value in detail.items() if key not in {"password", "token", "api_key", "mfa"}}
    with session() as connection:
        connection.execute(
            "INSERT INTO audit_logs(request_id,action,entity_type,entity_id,detail) VALUES(?,?,?,?,?)",
            (trace_id, action, entity_type, entity_id, json.dumps(safe_detail, ensure_ascii=False)),
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="企业IT运维工单Agent工具服务", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    trace_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = trace_id
    logger.info(json.dumps({"request_id": trace_id, "path": request.url.path, "status": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2)}, ensure_ascii=False))
    return response


@app.get("/health")
def health():
    return {"status": "ok", "knowledge_articles": len(load_json(ARTICLES_PATH)), "assets": len(load_json(ASSETS_PATH))}


@app.post("/api/v1/search")
def search(payload: SearchRequest, request: Request, _key: str = Depends(verify_key)):
    evidence = retrieve(payload.query, payload.category, payload.top_k)
    trace_id = request_id(request)
    audit(trace_id, "search", "knowledge", payload.category, {"query_length": len(payload.query), "result_count": len(evidence)})
    return {"request_id": trace_id, "evidence_found": bool(evidence), "evidence": evidence, "instruction": "Answer only from returned citation_id values; otherwise escalate to a human service desk."}


@app.get("/api/v1/assets/{asset_tag}")
def get_asset(asset_tag: str, request: Request, _key: str = Depends(verify_key)):
    asset = next((item for item in load_json(ASSETS_PATH) if item["asset_tag"].upper() == asset_tag.upper()), None)
    trace_id = request_id(request)
    audit(trace_id, "lookup", "asset", asset_tag.upper(), {"found": bool(asset)})
    if not asset:
        raise HTTPException(404, "asset not found")
    return {"request_id": trace_id, "asset": asset}


@app.post("/api/v1/ticket-drafts", status_code=201)
def create_ticket(payload: TicketDraftRequest, request: Request, _key: str = Depends(verify_key)):
    trace_id = request_id(request)
    with session() as connection:
        existing = connection.execute("SELECT * FROM ticket_drafts WHERE idempotency_key=?", (payload.idempotency_key,)).fetchone()
        if existing:
            return {"id": existing["id"], "status": existing["status"], "deduplicated": True, "message": "重复请求已合并，未创建第二张工单。"}
        ticket_id = f"ITD-{uuid.uuid4().hex[:10].upper()}"
        connection.execute(
            "INSERT INTO ticket_drafts(id,idempotency_key,summary,category,priority,asset_tag,requester_alias) VALUES(?,?,?,?,?,?,?)",
            (ticket_id, payload.idempotency_key, payload.summary, payload.category, payload.priority, payload.asset_tag, payload.requester_alias),
        )
    audit(trace_id, "create", "ticket_draft", ticket_id, {"category": payload.category, "priority": payload.priority, "status": "pending_review"})
    return {"id": ticket_id, "status": "pending_review", "deduplicated": False, "message": "工单草稿已创建，尚未提交生产工单系统，等待人工审核。"}


@app.get("/api/v1/ticket-drafts/{ticket_id}")
def get_ticket(ticket_id: str, _key: str = Depends(verify_key)):
    with session() as connection:
        row = connection.execute("SELECT * FROM ticket_drafts WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404, "ticket draft not found")
    return dict(row)


@app.post("/api/v1/ticket-drafts/{ticket_id}/review")
def review_ticket(ticket_id: str, payload: ReviewRequest, request: Request, _key: str = Depends(verify_key)):
    with session() as connection:
        row = connection.execute("SELECT status FROM ticket_drafts WHERE id=?", (ticket_id,)).fetchone()
        if not row:
            raise HTTPException(404, "ticket draft not found")
        if row["status"] != "pending_review":
            raise HTTPException(409, "ticket draft already reviewed")
        connection.execute("UPDATE ticket_drafts SET status=?,reviewer=?,review_note=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (payload.decision, payload.reviewer, payload.note, ticket_id))
    audit(request_id(request), "review", "ticket_draft", ticket_id, {"decision": payload.decision, "reviewer": payload.reviewer})
    return {"id": ticket_id, "status": payload.decision, "message": "审核状态已记录；本MVP不会自动提交到真实ITSM。"}


@app.get("/api/v1/audit")
def list_audit(limit: int = 50, _key: str = Depends(verify_key)):
    safe_limit = max(1, min(limit, 200))
    with session() as connection:
        rows = connection.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (safe_limit,)).fetchall()
    return [dict(row) for row in rows]
