import unittest

from app.services.llm_retry_service import ainvoke_with_retry


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FlakyLLM:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, prompt, max_tokens=None):  # noqa: ANN001
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("temporary failure")
        return _Resp('{"ok": true}')


class _InvalidThenValidLLM:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, prompt, max_tokens=None):  # noqa: ANN001
        self.calls += 1
        if self.calls < 2:
            return _Resp("")
        return _Resp("valid")


class TestLLMRetryService(unittest.IsolatedAsyncioTestCase):
    async def test_retries_on_exception(self):
        llm = _FlakyLLM()
        resp = await ainvoke_with_retry(
            llm,
            "x",
            attempts=3,
            validator=lambda r: bool(getattr(r, "content", "")),
            task_name="test_retry_exception",
        )
        self.assertEqual(resp.content, '{"ok": true}')
        self.assertEqual(llm.calls, 3)

    async def test_retries_on_invalid_response(self):
        llm = _InvalidThenValidLLM()
        resp = await ainvoke_with_retry(
            llm,
            "x",
            attempts=2,
            validator=lambda r: bool(getattr(r, "content", "")),
            task_name="test_retry_invalid",
        )
        self.assertEqual(resp.content, "valid")
        self.assertEqual(llm.calls, 2)


if __name__ == "__main__":
    unittest.main()
