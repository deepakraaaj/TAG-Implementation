import json
from pathlib import Path

from app.services.schema_manifest_service import SchemaManifestService


def test_schema_manifest_load_uses_process_cache(tmp_path, monkeypatch):
    manifest_file = tmp_path / "schema_manifest.json"
    manifest_file.write_text(json.dumps({"tables": {"task_transaction": {}}, "few_shot_examples": []}))

    call_counter = {"count": 0}
    original_read_text = Path.read_text

    def _counted_read_text(self, *args, **kwargs):
        call_counter["count"] += 1
        return original_read_text(self, *args, **kwargs)

    SchemaManifestService._manifest_cache.clear()
    monkeypatch.setattr(Path, "read_text", _counted_read_text)

    first = SchemaManifestService(manifest_file)
    second = SchemaManifestService(manifest_file)

    assert first.manifest.get("tables") == second.manifest.get("tables")
    assert call_counter["count"] == 1
