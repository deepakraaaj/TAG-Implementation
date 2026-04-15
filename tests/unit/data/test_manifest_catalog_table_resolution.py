from app.assistant.engine.metadata.manifest_catalog import ManifestCatalog


class _FakeDomain:
    def __init__(self, manifest=None):
        self.manifest = manifest or {}


def _catalog(manifest=None):
    domain = _FakeDomain(manifest=manifest)
    return ManifestCatalog(domain_provider=lambda: domain)


def test_resolve_table_prefers_plural_business_entity_over_tenant_reference():
    catalog = _catalog(
        manifest={
            "tables": {
                "vehicle": {
                    "important_columns": {"id": {}, "company_id": {}},
                    "aliases": ["vehicle"],
                },
                "company": {
                    "important_columns": {"id": {}, "name": {}},
                    "aliases": ["company"],
                },
            },
        }
    )

    assert catalog.resolve_table_from_query("list vehicles for this company") == "vehicle"


def test_resolve_table_matches_pluralized_phrase_alias():
    catalog = _catalog(
        manifest={
            "tables": {
                "task_transaction": {
                    "important_columns": {"id": {}, "title": {}},
                    "aliases": ["task transaction"],
                },
                "task": {
                    "important_columns": {"id": {}, "title": {}},
                    "aliases": ["task"],
                },
            },
        }
    )

    assert catalog.resolve_table_from_query("show me all task transactions") == "task_transaction"
