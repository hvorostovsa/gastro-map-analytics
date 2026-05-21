from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
_LOG_DIR = _BASE_DIR / "logs"
_LOG_FILE = _LOG_DIR / "request_timings.jsonl"
_LOG_LOCK = Lock()


@dataclass(slots=True)
class RequestTimingRecord:
    method: str
    path: str
    status_code: int
    duration_ms: float
    started_at: str
    client: str | None = None


class RequestTimingItem(BaseModel):
    method: str = Field(..., examples=["GET"])
    path: str = Field(..., examples=["/api/market/forecast"])
    status_code: int = Field(..., examples=[200])
    duration_ms: float = Field(..., examples=[12.34])
    started_at: str = Field(..., examples=["2026-05-21T12:34:56.123456+00:00"])
    client: str | None = Field(default=None, examples=["127.0.0.1:53124"])


class RequestTimingsResponse(BaseModel):
    count: int = Field(..., examples=[2])
    items: list[RequestTimingItem]
    log_file: str = Field(..., examples=["backend/logs/request_timings.jsonl"])


router = APIRouter(prefix="/api/debug", tags=["debug", "timings"])
_recent_timings: deque[RequestTimingRecord] = deque(maxlen=500)


def _client_host(request: Request) -> str | None:
    client = request.client
    if client is None:
        return None
    if client.host and client.port:
        return f"{client.host}:{client.port}"
    return client.host


def _should_track_path(path: str) -> bool:
    return path.startswith("/api") or path in {"/restaurants"}


def _write_timing_to_file(record: RequestTimingRecord) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(record), ensure_ascii=False)
    with _LOG_LOCK:
        with _LOG_FILE.open("a", encoding="utf-8") as file_handle:
            file_handle.write(payload + "\n")


def record_timing(*, request: Request, response: Response, duration_ms: float) -> None:
    if not _should_track_path(request.url.path):
        return

    record = RequestTimingRecord(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
        started_at=datetime.now(timezone.utc).isoformat(),
        client=_client_host(request),
    )
    _recent_timings.append(record)
    _write_timing_to_file(record)
    logger.info(
        "timing recorded: %s %s -> %s in %.2f ms",
        record.method,
        record.path,
        record.status_code,
        record.duration_ms,
    )


async def timing_middleware(request: Request, call_next):
    started = perf_counter()
    response = await call_next(request)
    duration_ms = (perf_counter() - started) * 1000.0
    response.headers["X-Process-Time-ms"] = f"{duration_ms:.2f}"
    record_timing(request=request, response=response, duration_ms=duration_ms)
    return response


@router.get(
    "/request-timings",
    response_model=RequestTimingsResponse,
    summary="Get recent request timings",
    description="Returns recent tracked endpoint timings and the JSONL log file path.",
)
def get_recent_timings(limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 500))
    items = list(_recent_timings)[-safe_limit:]
    return {
        "count": len(items),
        "items": [asdict(item) for item in items],
        "log_file": str(_LOG_FILE),
    }


@router.post(
    "/request-timings/clear",
    summary="Clear recent request timings",
    description="Clears the in-memory recent timing cache. The on-disk log file is left intact.",
)
def clear_recent_timings() -> dict[str, str]:
    _recent_timings.clear()
    return {"status": "cleared"}