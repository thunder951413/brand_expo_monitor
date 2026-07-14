import tempfile
import unittest
from pathlib import Path

from brand_monitor.collector import Collection, analyze_answer, brand_terms, normalize_sources
from brand_monitor.db import initialize
from brand_monitor.service import MonitorService, iso_now, source_variability
from brand_monitor.webdriver_collector import clean_answer_text, extract_body_delta


class CollectorTest(unittest.TestCase):
    def test_brand_aliases_and_rank(self):
        terms = brand_terms("瑞思迈ResMed", "瑞思迈, ResMed,resmed")
        self.assertEqual(terms, ["瑞思迈ResMed", "瑞思迈", "ResMed"])
        hit, count, rank = analyze_answer("1. 飞利浦\n2. ResMed 瑞思迈\n3. 鱼跃", terms)
        self.assertTrue(hit)
        self.assertEqual(count, 2)
        self.assertEqual(rank, 2)

        hit, count, _ = analyze_answer("推荐瑞思迈ResMed呼吸机。", terms)
        self.assertTrue(hit)
        self.assertEqual(count, 1)

    def test_source_normalization_and_url_extraction(self):
        sources = normalize_sources(
            [{"url": "https://www.resmed.com.cn/a", "title": "A"}],
            "更多内容 https://example.com/path。",
        )
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["domain"], "resmed.com.cn")
        redirected = normalize_sources(["https://example.ai/redirect?url=https%3A%2F%2Fnews.example.com%2Fa"])
        self.assertEqual(redirected[0]["url"], "https://news.example.com/a")

    def test_webdriver_body_delta_fallback(self):
        before = "豆包\n新对话\n联网搜索\n发送"
        after = "豆包\n新对话\n家用呼吸机品牌都有哪些？\n常见品牌包括：\n1. 瑞思迈 ResMed\n2. 飞利浦伟康\n鱼跃价格多少？\n慢阻肺怎么选？\n下载豆包电脑版，体验更强大的 AI 能力\n内容由AI生成，仅供参考\n发送"
        answer = extract_body_delta(before, after, "家用呼吸机品牌都有哪些？")
        self.assertIn("瑞思迈 ResMed", answer)
        self.assertNotIn("家用呼吸机品牌都有哪些", answer)

        cleaned = clean_answer_text("正文\n推荐型号如下\n鱼跃多少钱？\n慢阻肺怎么选？\n下载豆包电脑版，体验更强大的 AI 能力")
        self.assertEqual(cleaned, "正文\n推荐型号如下")


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "test.db")
        initialize(self.db_path, iso_now())
        self.service = MonitorService(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_demo_run_generates_platform_results(self):
        prompt_id = self.service.config()["prompts"][0]["id"]
        run_id = self.service.run(prompt_id, "demo")
        dashboard = self.service.dashboard()
        self.assertGreater(run_id, 0)
        self.assertEqual(dashboard["totals"]["total"], 5)
        self.assertEqual(len(dashboard["results"]), 5)
        self.assertGreater(dashboard["totals"]["citations"], 0)
        self.assertEqual(dashboard["totals"]["success_rate"], 100.0)
        self.assertEqual(dashboard["totals"]["citation_rate"], 80.0)
        self.assertEqual(dashboard["totals"]["unique_sources"], 4)
        self.assertEqual(len(dashboard["prompts"]), 1)
        self.assertEqual(self.service.dashboard(days=30)["totals"]["total"], 5)

    def test_manual_capture(self):
        config = self.service.config()
        self.service.manual_capture({
            "prompt_id": config["prompts"][0]["id"],
            "platform_id": config["platforms"][0]["id"],
            "answer": "首选瑞思迈 ResMed。",
            "sources": "https://www.resmed.com.cn/\nhttps://example.org/article",
        })
        result = self.service.dashboard()["results"][0]
        self.assertTrue(result["brand_hit"])
        self.assertEqual(result["citation_count"], 2)
        self.assertEqual(result["mode"], "manual")

    def test_webdriver_schedule_mode_is_supported(self):
        settings = self.service.settings()
        settings.update({"schedule_mode": "webdriver", "schedule_enabled": True, "schedule_minutes": 30})
        updated = self.service.update_settings(settings)
        self.assertEqual(updated["schedule_mode"], "webdriver")
        self.assertTrue(updated["schedule_enabled"])

    def test_auto_mode_falls_back_to_webdriver(self):
        class ApiStub:
            def collect(self, platform, prompt):
                return Collection("", [], "api_error", "API 暂不可用")

        class WebDriverStub:
            def collect(self, platform, prompt, enable_web_search=True):
                return Collection("WebDriver 回答提及瑞思迈。", [{"url": "https://example.com/source"}])

        service = MonitorService(self.db_path, webdriver_manager=WebDriverStub(), api_collector=ApiStub())
        settings = service.settings()
        settings["owned_domains"] = "example.com"
        service.update_settings(settings)
        run_id = service.run(service.config()["prompts"][0]["id"], "auto")
        dashboard = service.dashboard()
        results = [x for x in dashboard["results"] if x["run_id"] == run_id]
        self.assertEqual(len(results), 5)
        self.assertTrue(all(x["status"] == "success" for x in results))
        self.assertTrue(all(x["mode"] == "auto" for x in results))
        self.assertEqual(dashboard["retrieval"]["funnel"]["cited"], 5)
        self.assertEqual(dashboard["retrieval"]["funnel"]["owned_cited"], 5)
        self.assertTrue(all(x["source_observations"] for x in results))
        self.assertIn("本地相关度", service.export_retrieval_csv().splitlines()[0])

    def test_source_variability_finds_core_and_rotation(self):
        metrics = source_variability([
            {"cited_domains": {"core.com", "a.com"}, "citation_ranks": {"core.com": 1, "a.com": 2}},
            {"cited_domains": {"core.com", "b.com"}, "citation_ranks": {"core.com": 1, "b.com": 2}},
            {"cited_domains": {"core.com", "a.com"}, "citation_ranks": {"core.com": 2, "a.com": 1}},
            {"cited_domains": {"core.com", "b.com"}, "citation_ranks": {"core.com": 1, "b.com": 2}},
        ])
        self.assertEqual(metrics["pattern"], "rotation")
        self.assertEqual(metrics["core_domains"][0]["domain"], "core.com")
        self.assertEqual({x["domain"] for x in metrics["rotating_domains"]}, {"a.com", "b.com"})
        self.assertGreater(metrics["change_rate"], 0)

    def test_platform_strategy_compares_different_prompts(self):
        config = self.service.config()
        platform_id = config["platforms"][0]["id"]
        for prompt, domain in zip(config["prompts"][:3], ["a.com", "b.com", "c.com"]):
            self.service.manual_capture({
                "prompt_id": prompt["id"], "platform_id": platform_id,
                "answer": "瑞思迈 ResMed 被提及。", "sources": f"https://{domain}/article",
            })
        strategy = self.service.dashboard()["retrieval"]["platform_strategies"][0]
        self.assertEqual(strategy["overall_pattern"], "volatile")
        self.assertEqual(strategy["prompt_effect"], 100.0)
        self.assertEqual(strategy["prompt_effect_label"], "提示词影响明显")
        self.assertEqual(len(strategy["prompt_pairs"]), 3)
        self.assertTrue(all(pair["change"] == 100.0 for pair in strategy["prompt_pairs"]))


if __name__ == "__main__":
    unittest.main()
