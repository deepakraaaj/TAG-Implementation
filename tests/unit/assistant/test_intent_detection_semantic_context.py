import asyncio
from types import SimpleNamespace

from app.assistant.engine.intent.intent_detection_service import IntentDetectionService
from app.assistant.engine import intent_detection_service as intent_detection_service_module


class _FakeToon:
    @staticmethod
    def encode(value):
        return str(value)


class _FakeCatalog:
    @staticmethod
    def get_candidate_tables(_query, limit=15):
        del limit
        return {
            "vehicle": {
                "description": "Vehicle master",
                "aliases": ["truck", "vehicle"],
            }
        }


class _FakeRetriever:
    default_prompt_k = 4

    @staticmethod
    def search(_query, **_kwargs):
        return [
            {
                "kind": "special_query",
                "artifact_id": "vehicle_overspeed_ranking",
                "text": "Find the truck with the highest overspeed total",
                "score": 0.91,
            }
        ]

    @staticmethod
    def render_hits(_hits, **_kwargs):
        return "- [special query] vehicle_overspeed_ranking (score=0.91): Find the truck with the highest overspeed total"


class _FakeDomain:
    description = "vehicle tracking assistant"
    manifest = {
        "tables": {
            "vehicle": {
                "description": "Vehicle master",
                "aliases": ["truck", "vehicle"],
                "columns": [{"name": "vehicle_number", "type": "varchar"}],
            }
        }
    }
    spec = SimpleNamespace(
        config=SimpleNamespace(
            few_shot_examples=[
                {
                    "question": "Which truck overspeeded the most?",
                    "intent": {"table": "vts_exception", "joins": ["vehicle"]},
                }
            ]
        ),
        semantics={"join_hints": {"vts_exception_to_vehicle": "vts_exception.vehicle_id = vehicle.id"}},
        domain_knowledge={"business_relationships": {"overspeed_ranking": "Aggregate overspeed counts by vehicle."}},
    )

    @staticmethod
    def get_intent_detection_config():
        return {
            "assistant_context": "vts reporting assistant",
            "rules": ["Infer operation and target table from the user query and schema aliases."],
        }


def test_intent_detection_prompt_includes_semantic_retrieval_context(monkeypatch):
    captured_prompt = {"value": ""}

    class _FakeResponse:
        content = '{"operation":"SELECT","table":"vts_exception","filters":[],"confidence":95}'

    async def _fake_llm(*args, **kwargs):
        captured_prompt["value"] = str(args[1]) if len(args) > 1 else str(kwargs.get("prompt", ""))
        return _FakeResponse()

    monkeypatch.setattr(intent_detection_service_module, "ainvoke_with_retry", _fake_llm)

    service = IntentDetectionService(
        llm=object(),
        domain_provider=lambda: _FakeDomain(),
        toon_service=_FakeToon(),
        manifest_catalog=_FakeCatalog(),
        semantic_retriever=_FakeRetriever(),
    )

    intent, usage = asyncio.run(
        service.detect_intent_with_usage(
            "which truck overspeeded the most",
            metadata={"token_minimization": False},
        )
    )

    assert intent["table"] == "vts_exception"
    assert int(usage.get("llm_calls", 0)) >= 1
    assert "Retrieved Domain Context:" in captured_prompt["value"]
    assert "vehicle_overspeed_ranking" in captured_prompt["value"]
