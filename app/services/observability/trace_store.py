"""In-memory ring buffer of recent chat requests for the admin dashboard.

This is deliberately process-local and bounded: it is an operator debugging aid,
not durable storage. It captures one record per completed chat request with the
route taken, intent, generated SQL, row count, timings and any error so the
admin "Traces" view can answer "what did the bot just do for this query?".
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional


class RequestTraceStore:
    def __init__(self, max_size: int = 200):
        self._max_size = max(1, int(max_size or 1))
        self._items: Deque[Dict[str, Any]] = deque(maxlen=self._max_size)
        self._lock = threading.Lock()

    def record(self, trace: Dict[str, Any]) -> None:
        if not isinstance(trace, dict):
            return
        entry = dict(trace)
        entry.setdefault("id", uuid.uuid4().hex[:12])
        entry.setdefault("ts", time.time())
        with self._lock:
            self._items.appendleft(entry)

    def list(self, limit: int = 100, app_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._items)
        if app_id:
            wanted = str(app_id).strip().lower()
            items = [it for it in items if str(it.get("app_id", "")).strip().lower() == wanted]
        try:
            capped = int(limit)
        except (TypeError, ValueError):
            capped = 100
        if capped > 0:
            items = items[:capped]
        return items

    def get(self, trace_id: str) -> Optional[Dict[str, Any]]:
        wanted = str(trace_id or "").strip()
        if not wanted:
            return None
        with self._lock:
            for item in self._items:
                if str(item.get("id")) == wanted or str(item.get("trace_id")) == wanted:
                    return dict(item)
        return None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            items = list(self._items)
        total = len(items)
        errors = sum(1 for it in items if it.get("error"))
        by_route: Dict[str, int] = {}
        for it in items:
            route = str(it.get("route", "") or "unknown").upper()
            by_route[route] = by_route.get(route, 0) + 1
        return {
            "buffered": total,
            "capacity": self._max_size,
            "errors": errors,
            "by_route": by_route,
        }
