from app.services.db_service import DBService


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _stmt):
        return 1


class _FakeEngine:
    def __init__(self):
        self.disposed = False
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        return _FakeConnection()

    def dispose(self):
        self.disposed = True


def test_db_service_normalizes_aiomysql_url_for_sync_engine(monkeypatch):
    captured = {}

    def _fake_create_engine(url, **_kwargs):
        captured["url"] = url
        return _FakeEngine()

    monkeypatch.setattr("app.services.db_service.create_engine", _fake_create_engine)

    DBService(
        "mysql+aiomysql://user:secret@db.example.com:3306/app_db"
        "?allowPublicKeyRetrieval=true&useSSL=false&charset=utf8mb4"
    )

    assert captured["url"].startswith("mysql+mysqlconnector://user:secret@db.example.com:3306/app_db")
    assert "allowPublicKeyRetrieval" not in captured["url"]
    assert "useSSL" not in captured["url"]
    assert "charset=utf8mb4" in captured["url"]


def test_db_service_close_disposes_engine(monkeypatch):
    fake_engine = _FakeEngine()
    monkeypatch.setattr("app.services.db_service.create_engine", lambda *_args, **_kwargs: fake_engine)

    service = DBService("sqlite:///tmp/test.sqlite3")
    service.close()

    assert fake_engine.disposed is True
