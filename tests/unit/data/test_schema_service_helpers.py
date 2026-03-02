from app.services.data.schema_service import SchemaService


def test_sanitize_mysqlconnector_url_removes_unsupported_flags():
    url = (
        "mysql+mysqlconnector://user:secret@db.example.com:3306/app_db"
        "?allowPublicKeyRetrieval=true&useSSL=false&charset=utf8mb4"
    )

    sanitized = SchemaService._sanitize_mysqlconnector_url(url)

    assert "allowPublicKeyRetrieval" not in sanitized
    assert "useSSL" not in sanitized
    assert "charset=utf8mb4" in sanitized


def test_safe_db_target_hides_credentials():
    safe_target = SchemaService._safe_db_target(
        "mysql+mysqlconnector://user:secret@db.example.com:3306/app_db?charset=utf8mb4"
    )

    assert safe_target == "mysql+mysqlconnector://db.example.com:3306/app_db"
