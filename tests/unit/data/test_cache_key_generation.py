from app.services.platform.cache import RedisCache


def test_generate_key_preserves_argument_boundaries():
    key_one = RedisCache.generate_key("chat", "ab", 12, "3")
    key_two = RedisCache.generate_key("chat", "a", 1, "23")

    assert key_one != key_two


def test_generate_key_normalizes_set_values():
    key_one = RedisCache.generate_key("chat", {"roles": {"assistant", "user"}})
    key_two = RedisCache.generate_key("chat", {"roles": {"user", "assistant"}})

    assert key_one == key_two
