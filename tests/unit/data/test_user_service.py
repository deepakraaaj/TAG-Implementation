from app.services.data.user_service import UserService


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _FakeConnection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, stmt, params):
        self.calls.append((str(stmt), params))
        return _FakeResult(self.row)


class _FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


class _FakeSchemaService:
    def __init__(self, engine):
        self.engine = engine

    def get_engine_for_url(self, db_url=None):
        return self.engine


class _FakeDomain:
    def __init__(self, config):
        self.config = config

    def get_user_lookup_config(self):
        return self.config


def test_user_service_sanitizes_lookup_identifiers():
    connection = _FakeConnection({"first_name": "", "last_name": ""})
    schema_service = _FakeSchemaService(_FakeEngine(connection))
    domain = _FakeDomain(
        {
            "table": "users; DROP TABLE users",
            "id_column": "id OR 1=1",
            "first_name_column": "first_name",
            "last_name_column": "last_name",
            "fallback_name": "Teammate",
        }
    )
    service = UserService(schema_service=schema_service, domain_provider=lambda: domain)

    result = service.get_user_info("42")
    sql, params = connection.calls[0]

    assert result == {"user_name": "Teammate"}
    assert "FROM `user`" in sql
    assert "WHERE `id` = :uid" in sql
    assert params == {"uid": 42}


def test_user_service_skips_non_numeric_user_ids():
    service = UserService(
        schema_service=_FakeSchemaService(engine=None),
        domain_provider=lambda: _FakeDomain({}),
    )

    assert service.get_user_info("abc") == {}
