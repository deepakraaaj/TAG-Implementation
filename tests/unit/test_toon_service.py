from app.services.core.toon_service import ToonService


def test_toon_encode_tabular_array_of_objects():
    rows = [
        {"id": 1, "name": "Pump-1", "is_active": True},
        {"id": 2, "name": "Pump-2", "is_active": False},
    ]

    toon = ToonService.encode(rows)

    assert toon.startswith("[2]{id,name,is_active}:")
    assert "1,Pump-1,true" in toon
    assert "2,Pump-2,false" in toon


def test_toon_encode_quotes_unsafe_values():
    payload = {
        "tags": ["a,b", "normal", "  padded"],
        "status": "in progress",
    }

    toon = ToonService.encode(payload)

    assert "tags[3]: \"a,b\",normal,\"  padded\"" in toon
    assert "status: in progress" in toon


def test_toon_estimate_tokens_returns_positive_for_non_empty_text():
    text = "[2]{id,name}:\n  1,Pump-1\n  2,Pump-2"
    count = ToonService.estimate_tokens(text)
    assert isinstance(count, int)
    assert count > 0
