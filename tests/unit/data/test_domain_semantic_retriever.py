from pathlib import Path
from types import SimpleNamespace

from app.assistant.engine.metadata.domain_semantic_retriever import DomainSemanticRetriever


class _FakeEmbedder:
    @staticmethod
    def _vector(text: str):
        lowered = str(text or "").lower()
        return [
            3.0 if "overspeed" in lowered else 0.0,
            2.0 if any(term in lowered for term in ("truck", "vehicle")) else 0.0,
            4.0 if "report" in lowered else 0.0,
        ]

    def embed(self, texts):
        return [self._vector(text) for text in texts]


class _FakeDomain:
    def __init__(self, domain_path: Path):
        self.name = "vts"
        self.domain_path = domain_path
        self.manifest = {
            "tables": {
                "vehicle": {
                    "description": "Vehicle master",
                    "aliases": ["truck", "vehicle"],
                    "important_columns": {"id": {}, "vehicle_number": {}},
                },
                "vts_exception": {
                    "description": "Vehicle exception events including overspeed",
                    "aliases": ["overspeed exception"],
                    "important_columns": {"vehicle_id": {}, "over_speed_count": {}, "company_id": {}},
                    "joins": {"vehicle": "vts_exception.vehicle_id = vehicle.id"},
                },
            }
        }
        self.spec = SimpleNamespace(
            config=SimpleNamespace(
                few_shot_examples=[
                    {
                        "question": "Which truck overspeeded the most?",
                        "intent": {
                            "table": "vts_exception",
                            "joins": ["vehicle"],
                            "columns": ["vehicle.vehicle_number", "vts_exception.over_speed_count"],
                        },
                    }
                ]
            ),
            language=SimpleNamespace(
                glossary={"truck": "vehicle", "overspeed": "vts_exception"}
            ),
        )

    def get_config_section(self, section: str):
        if section == "sql_builder":
            return {
                "special_queries": [
                    {
                        "id": "vehicle_overspeed_ranking",
                        "description": "Find the truck with the highest overspeed total",
                        "example_queries": ["which truck overspeeded the most"],
                        "candidate_tables": ["vts_exception", "vehicle"],
                    }
                ]
            }
        return {}

    @staticmethod
    def get_domain_knowledge_config():
        return {
            "example_queries": ["which truck overspeeded the most"],
            "business_relationships": {
                "overspeed_ranking": "Aggregate vts_exception.over_speed_count by vehicle."
            },
        }


def test_domain_semantic_retriever_surfaces_special_query_artifacts(tmp_path):
    reports_path = tmp_path / "reports.json"
    reports_path.write_text(
        '{"reports":{"trip_status_summary":{"name":"Trip Status Summary","aliases":["trip status report"]}}}',
        encoding="utf-8",
    )
    domain = _FakeDomain(tmp_path)
    retriever = DomainSemanticRetriever(
        lambda: domain,
        enabled=True,
        embedder_factory=lambda: _FakeEmbedder(),
    )

    hits = retriever.search(
        "which truck overspeeded the most",
        kinds={"special_query", "example", "table"},
        limit=5,
        min_score=0.0,
    )

    assert any(hit["kind"] == "special_query" for hit in hits)
    assert any("vts_exception" in (hit.get("candidate_tables") or []) for hit in hits)
    rendered = retriever.render_hits(hits)
    assert "vehicle_overspeed_ranking" in rendered


def test_domain_semantic_retriever_can_find_reports(tmp_path):
    reports_path = tmp_path / "reports.json"
    reports_path.write_text(
        '{"reports":{"trip_status_summary":{"name":"Trip Status Summary","aliases":["trip status report"]}}}',
        encoding="utf-8",
    )
    domain = _FakeDomain(tmp_path)
    retriever = DomainSemanticRetriever(
        lambda: domain,
        enabled=True,
        embedder_factory=lambda: _FakeEmbedder(),
    )

    hits = retriever.search(
        "show trip status report",
        kinds={"report"},
        limit=3,
        min_score=0.0,
    )

    assert hits
    assert hits[0]["kind"] == "report"
    assert hits[0]["route"] == "REPORT"
