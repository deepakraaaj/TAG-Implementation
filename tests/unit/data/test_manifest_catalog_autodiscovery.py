"""Unit tests for ManifestCatalog auto-discovery fallback."""
from app.assistant.engine.metadata.manifest_catalog import ManifestCatalog


class _FakeSchemaService:
    """Stub returning predefined tables and columns."""

    def __init__(self, tables=None, columns_map=None):
        self._tables = tables or []
        self._columns_map = columns_map or {}

    def get_all_tables(self, db_url=None):
        return list(self._tables)

    def get_table_columns(self, tables, db_url=None):
        return {t: set(self._columns_map.get(t, [])) for t in tables}


class _FakeDomain:
    """Stub domain with an optional manifest."""

    def __init__(self, manifest=None):
        self.manifest = manifest or {}


def _catalog(*, manifest=None, schema_service=None, db_url=None, semantic_retriever=None):
    domain = _FakeDomain(manifest=manifest)
    return ManifestCatalog(
        domain_provider=lambda: domain,
        schema_service=schema_service,
        db_url=db_url,
        semantic_retriever=semantic_retriever,
    )


# ------------------------------------------------------------------
# When manifest HAS tables → static manifest is used
# ------------------------------------------------------------------


def test_catalog_uses_manifest_when_tables_exist():
    catalog = _catalog(
        manifest={
            "tables": {
                "users": {
                    "important_columns": {"id": {}, "name": {}},
                    "aliases": ["user"],
                },
            },
        },
        schema_service=_FakeSchemaService(
            tables=["should_not_see"],
            columns_map={"should_not_see": ["x"]},
        ),
    )
    assert catalog.table_names() == {"users"}
    assert catalog.important_columns("users") == {"id", "name"}


# ------------------------------------------------------------------
# When manifest is empty → falls back to auto-discovery
# ------------------------------------------------------------------


def test_catalog_falls_back_to_autodiscovery_when_no_manifest_tables():
    catalog = _catalog(
        manifest={},
        schema_service=_FakeSchemaService(
            tables=["products", "categories"],
            columns_map={
                "products": ["id", "sku", "name"],
                "categories": ["id", "label"],
            },
        ),
    )
    assert "products" in catalog.table_names()
    assert "categories" in catalog.table_names()
    assert catalog.important_columns("products") == {"id", "sku", "name"}


def test_catalog_resolve_table_from_query_with_autodiscovery():
    catalog = _catalog(
        manifest={},
        schema_service=_FakeSchemaService(
            tables=["task_transaction", "asset"],
            columns_map={
                "task_transaction": ["id", "title"],
                "asset": ["id", "name"],
            },
        ),
    )
    resolved = catalog.resolve_table_from_query("show me all task transactions")
    assert resolved == "task_transaction"


def test_catalog_resolves_mapping_table_from_related_entities():
    catalog = _catalog(
        manifest={
            "tables": {
                "user": {
                    "important_columns": {"id": {}, "first_name": {}},
                    "aliases": ["user", "users"],
                },
                "location": {
                    "important_columns": {"id": {}, "name": {}},
                    "aliases": ["location", "locations"],
                },
                "user_location_mapping": {
                    "important_columns": {"id": {}, "user_id": {}, "location_id": {}},
                    "aliases": ["user location mapping", "user location mappings"],
                    "joins": {
                        "user": "user_location_mapping.user_id = user.id",
                        "location": "user_location_mapping.location_id = location.id",
                    },
                },
            },
        },
    )

    resolved = catalog.resolve_table_from_query("Which users are mapped to which locations?")
    assert resolved == "user_location_mapping"


class _FakeSemanticRetriever:
    def search(self, _query, **_kwargs):
        return [
            {
                "kind": "special_query",
                "candidate_tables": ["vehicle", "vts_exception"],
                "score": 0.91,
            }
        ]


def test_catalog_adds_semantic_candidates_before_lexical_fallback():
    catalog = _catalog(
        manifest={
            "tables": {
                "vehicle": {"important_columns": {"id": {}, "vehicle_number": {}}, "aliases": ["truck"]},
                "vts_exception": {"important_columns": {"vehicle_id": {}, "over_speed_count": {}}},
                "trip": {"important_columns": {"id": {}, "vehicle_id": {}}},
            },
        },
        semantic_retriever=_FakeSemanticRetriever(),
    )

    candidates = catalog.get_candidate_tables("which asset had the most speed violations", limit=5)

    assert list(candidates.keys())[:2] == ["vehicle", "vts_exception"]


# ------------------------------------------------------------------
# When NO schema_service → empty fallback
# ------------------------------------------------------------------


def test_catalog_returns_empty_when_no_manifest_and_no_schema_service():
    catalog = _catalog(manifest={}, schema_service=None)
    assert catalog.table_names() == set()
    assert catalog.important_columns("anything") == set()
