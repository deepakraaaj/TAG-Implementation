import asyncio
import json
from types import SimpleNamespace

from app.api.v1.endpoints import health


class _FakeCache:
    def __init__(self, configured=True, ping_ok=True):
        self._configured = configured
        self._ping_ok = ping_ok

    def is_configured(self):
        return self._configured

    async def ping(self):
        return self._ping_ok


class _FakeContainer:
    def __init__(self, workflow, cache):
        self._workflow = workflow
        self.cache = cache

    def get_workflow(self):
        return self._workflow


class _SnapshotContainer(_FakeContainer):
    async def readiness_snapshot(self):
        return {
            "status": "ok",
            "ready": True,
            "env": "test",
            "checks": {
                "container": {"status": "ok", "required": True, "detail": "ready"},
                "config": {"status": "ok", "required": True, "detail": "valid"},
                "workflow": {"status": "ok", "required": True, "detail": "ready"},
                "database": {"status": "ok", "required": True, "detail": "reachable"},
                "cache": {"status": "disabled", "required": False, "detail": "disabled"},
            },
        }


def _request_with_container(container=None):
    state = SimpleNamespace()
    if container is not None:
        state.container = container
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _json_body(response):
    return json.loads(response.body.decode("utf-8"))


def test_liveness_check_reports_alive():
    payload = asyncio.run(health.liveness_check())

    assert payload["status"] == "ok"
    assert payload["alive"] is True


def test_readiness_check_returns_ok_when_workflow_and_cache_are_ready():
    req = _request_with_container(
        _FakeContainer(
            workflow=object(),
            cache=_FakeCache(configured=True, ping_ok=True),
        )
    )

    response = asyncio.run(health.readiness_check(req))
    payload = _json_body(response)

    assert response.status_code == 200
    assert payload["ready"] is True
    assert payload["status"] == "ok"
    assert payload["checks"]["workflow"]["status"] == "ok"
    assert payload["checks"]["cache"]["status"] == "ok"


def test_readiness_check_returns_503_when_container_is_missing():
    response = asyncio.run(health.readiness_check(_request_with_container()))
    payload = _json_body(response)

    assert response.status_code == 503
    assert payload["ready"] is False
    assert payload["status"] == "not_ready"
    assert payload["checks"]["container"]["status"] == "not_ready"


def test_health_check_reports_degraded_cache_without_failing_probe():
    req = _request_with_container(
        _FakeContainer(
            workflow=object(),
            cache=_FakeCache(configured=True, ping_ok=False),
        )
    )

    response = asyncio.run(health.health_check(req))
    payload = _json_body(response)

    assert response.status_code == 200
    assert payload["ready"] is True
    assert payload["status"] == "degraded"
    assert payload["checks"]["cache"]["status"] == "degraded"


def test_readiness_check_uses_container_snapshot_when_available():
    req = _request_with_container(
        _SnapshotContainer(
            workflow=object(),
            cache=_FakeCache(configured=False, ping_ok=False),
        )
    )

    response = asyncio.run(health.readiness_check(req))
    payload = _json_body(response)

    assert response.status_code == 200
    assert payload["ready"] is True
    assert payload["checks"]["config"]["status"] == "ok"
    assert payload["checks"]["database"]["status"] == "ok"
