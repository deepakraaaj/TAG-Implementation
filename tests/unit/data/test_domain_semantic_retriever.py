from pathlib import Path
import json
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


class _BundleEmbedder:
    @staticmethod
    def _vector(text: str):
        lowered = str(text or "").lower()
        return [
            4.0 if "work order" in lowered else 0.0,
            3.0 if "status" in lowered else 0.0,
            2.0 if "task_transaction" in lowered else 0.0,
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


def test_domain_semantic_retriever_reads_semantic_bundle_chunks(tmp_path):
    bundle_dir = tmp_path / "semantic_bundle"
    bundle_dir.mkdir()
    (tmp_path / "reports.json").write_text('{"reports":{}}', encoding="utf-8")
    (bundle_dir / "schema_context.json").write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "table_name": "task_transaction",
                        "label": "work order",
                        "description": "Operational work orders",
                        "important_columns": [{"column_name": "status"}, {"column_name": "company_id"}],
                        "tenant_scope_candidates": ["company_id"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (bundle_dir / "business_semantics.json").write_text(
        json.dumps({"glossary": [{"term": "backlog", "meaning": "open work orders"}]}),
        encoding="utf-8",
    )
    (bundle_dir / "relationship_map.json").write_text(json.dumps({"relationships": []}), encoding="utf-8")
    (bundle_dir / "enum_dictionary.json").write_text(
        json.dumps({"entries": [{"table_name": "task_transaction", "column_name": "status", "sample_values": ["0", "1"]}]}),
        encoding="utf-8",
    )
    (bundle_dir / "query_patterns.json").write_text(
        json.dumps(
            {
                "patterns": [
                    {
                        "intent": "work_order_list",
                        "question_examples": ["show open work orders"],
                        "preferred_tables": ["task_transaction"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    domain = _FakeDomain(tmp_path)
    domain.name = "warehouse_ops"
    domain.manifest = {"tables": {"task_transaction": {"aliases": ["work order"], "important_columns": {"status": {}}}}}
    retriever = DomainSemanticRetriever(
        lambda: domain,
        enabled=True,
        embedder_factory=lambda: _BundleEmbedder(),
    )

    hits = retriever.search(
        "show open work orders by status",
        kinds={"table", "term", "enum", "special_query"},
        limit=5,
        min_score=0.0,
    )

    assert hits
    assert any(hit["artifact_id"] == "task_transaction" for hit in hits)
    assert any(hit["kind"] == "special_query" for hit in hits)


def test_domain_semantic_retriever_can_use_chroma_store_injection(tmp_path):
    class _FakeChromaStore:
        def is_available(self):
            return True

        def reindex_domain(self):
            return 2

        def search(self, query, *, kinds=None, limit=6, min_score=0.0):
            return [
                {
                    "id": "abc",
                    "kind": "learned_query",
                    "artifact_id": "abc",
                    "text": f"successful nl2sql example question {query}",
                    "candidate_tables": ["task_transaction"],
                    "route": "SQL",
                    "source_file": "runtime_memory",
                    "sql": "SELECT * FROM task_transaction",
                    "score": 0.99,
                }
            ]

        def remember_success(self, *, question, sql, candidate_tables=None):
            return "abc"

    domain = _FakeDomain(tmp_path)
    retriever = DomainSemanticRetriever(
        lambda: domain,
        enabled=True,
        chroma_store=_FakeChromaStore(),
    )
    retriever.provider = "chroma"

    hits = retriever.search("show trips", kinds={"learned_query"}, limit=3, min_score=0.0)

    assert hits
    assert hits[0]["kind"] == "learned_query"
    assert retriever.remember_success(question="show trips", sql="SELECT 1", candidate_tables=["trip"]) == "abc"
