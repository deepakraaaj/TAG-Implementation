import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.v1.endpoints import onboarding as onboarding_endpoint
from app.schemas.onboarding import (
    SimpleOnboardingArtifact,
    SimpleOnboardingRequest,
    SimpleOnboardingResponse,
)


def _request_with_container(container=None):
    state = SimpleNamespace()
    if container is not None:
        state.container = container
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_simple_onboarding_endpoint_returns_payload(monkeypatch):
    expected = SimpleOnboardingResponse(
        database_target="mysql://db.example.com:3306/ops",
        total_tables=1,
        selection_mode="review",
        categories={"Core Operations": ["task_transaction"]},
        selected_tables=["task_transaction"],
        ignored_tables=[],
        tables=[],
        artifact=SimpleOnboardingArtifact(
            categories={"Core Operations": ["task_transaction"]},
            selected_tables=["task_transaction"],
            table_descriptions={"task_transaction": "Tracks task transaction records."},
            relationships=[],
            business_context="",
            metrics=[],
        ),
    )

    def _fake_build(self, payload):
        assert payload.selection_mode == "review"
        return expected

    monkeypatch.setattr(onboarding_endpoint.SimpleOnboardingService, "build", _fake_build)

    payload = asyncio.run(
        onboarding_endpoint.run_simple_onboarding(
            SimpleOnboardingRequest(),
            _request_with_container(SimpleNamespace(schema_service=object())),
        )
    )

    assert payload == expected


def test_simple_onboarding_endpoint_returns_503_without_schema_service():
    try:
        asyncio.run(
            onboarding_endpoint.run_simple_onboarding(
                SimpleOnboardingRequest(),
                _request_with_container(),
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == "Schema service is unavailable"
    else:
        raise AssertionError("Expected HTTPException when schema service is missing")
