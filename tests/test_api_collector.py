import json
import tempfile
import unittest
from pathlib import Path

from brand_monitor.api_collector import ApiConfigStore, OfficialApiCollector


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None, data=None, timeout=None):
        body = json
        if data is not None:
            body = __import__("json").loads(data)
        self.calls.append((url, headers or {}, body))
        action = (headers or {}).get("X-TC-Action")
        if url.endswith("/responses"):
            return FakeResponse({"output": [{"type": "message", "content": [
                {"type": "output_text", "text": "推荐瑞思迈 ResMed。[1]",
                 "annotations": [{"type": "url_citation", "url": "https://doubao.example/a", "title": "豆包来源"}]}
            ]}]})
        if "dashscope" in url:
            return FakeResponse({"output": {"choices": [{"message": {"content": "千问提及瑞思迈。"}}],
                                            "search_info": {"search_results": [
                                                {"url": "https://qwen.example/a", "title": "千问来源"}
                                            ]}}})
        if url.endswith("/v2/ai_search/chat/completions"):
            return FakeResponse({"choices": [{"message": {"content": "文心提及瑞思迈 ResMed。[1]"}}],
                                 "references": [{"url": "https://baidu.example/a", "title": "百度来源"}]})
        if url.endswith("/v2/ai_search/web_search"):
            return FakeResponse({"references": [{"url": "https://search.example/a", "title": "搜索来源",
                                                  "content": "瑞思迈是常见品牌"}]})
        if "api.deepseek.com" in url:
            return FakeResponse({"choices": [{"message": {"content": "DeepSeek 根据资料提及瑞思迈。[1]"}}]})
        if action == "ChatCompletions":
            return FakeResponse({"Response": {"Choices": [{"Message": {"Content": "混元提及瑞思迈。[1]"}}],
                                               "SearchInfo": json_module({"items": [
                                                   {"url": "https://tencent.example/a", "title": "腾讯来源"}
                                               ]})}})
        if action == "SearchPro":
            return FakeResponse({"Response": {"Pages": json_module([
                {"url": "https://wsa.example/a", "title": "联网搜索", "content": "瑞思迈品牌资料"}
            ])}})
        raise AssertionError(f"unexpected URL: {url}")


def json_module(value):
    return json.dumps(value, ensure_ascii=False)


class OfficialApiCollectorTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = ApiConfigStore(Path(self.tempdir.name) / "api.json")
        self.store.update({
            "DOUBAO_API_KEY": "db-key", "QWEN_API_KEY": "qw-key", "BAIDU_API_KEY": "bd-key",
            "TENCENT_SECRET_ID": "tc-id", "TENCENT_SECRET_KEY": "tc-key",
            "DEEPSEEK_API_KEY": "ds-key", "DEEPSEEK_SEARCH_PROVIDER": "baidu",
        })
        self.session = FakeSession()
        self.collector = OfficialApiCollector(self.store, self.session)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_all_platforms_return_answer_and_sources(self):
        for slug in ("doubao", "qwen", "ernie", "yuanbao", "deepseek"):
            with self.subTest(slug=slug):
                result = self.collector.collect({"slug": slug, "name": slug}, "家用呼吸机品牌")
                self.assertEqual(result.status, "success")
                self.assertIn("瑞思迈", result.answer)
                self.assertGreaterEqual(len(result.sources), 1)
                self.assertTrue(result.sources[0]["url"].startswith("https://"))
                self.assertGreaterEqual(len(result.source_observations), 1)
                observation = result.source_observations[0]
                self.assertIn("local_relevance", observation)
                self.assertIn("search_rank", observation)

    def test_config_status_hides_secrets_and_file_is_private(self):
        public = self.collector.public_config()
        self.assertTrue(public["secrets"]["DOUBAO_API_KEY"])
        self.assertNotIn("db-key", json.dumps(public))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(x["configured"] for x in self.collector.statuses()))

    def test_tc3_signature_contains_no_secret(self):
        headers = OfficialApiCollector._tc3_headers(
            "id", "very-secret", "hunyuan", "hunyuan.tencentcloudapi.com",
            "ChatCompletions", "2023-09-01", "{}", timestamp=1700000000,
        )
        self.assertTrue(headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=id/"))
        self.assertNotIn("very-secret", headers["Authorization"])


if __name__ == "__main__":
    unittest.main()
