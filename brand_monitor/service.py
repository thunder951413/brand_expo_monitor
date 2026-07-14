from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import combinations
from statistics import pstdev

from .collector import analyze_answer, brand_terms, demo_collect, normalize_sources, relevance_score, webhook_collect
from .db import connect


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def row_dict(row):
    return dict(row) if row is not None else None


def source_variability(result_rows: list[dict]) -> dict:
    """Measure repeatability of cited domains across repeated answers."""
    cited_sets = [set(row.get("cited_domains", [])) for row in result_rows if row.get("cited_domains")]
    overlaps = []
    for left, right in combinations(cited_sets, 2):
        overlaps.append(len(left & right) / len(left | right) if left | right else 1.0)
    stability = sum(overlaps) / len(overlaps) if overlaps else (1.0 if len(cited_sets) == 1 else 0.0)
    counts = Counter(domain for domains in cited_sets for domain in domains)
    appearances = sum(counts.values())
    entropy = 0.0
    if appearances and len(counts) > 1:
        entropy = -sum((count / appearances) * math.log(count / appearances) for count in counts.values()) / math.log(len(counts))
    citation_runs = len(cited_sets)
    frequencies = [
        {"domain": domain, "runs": count, "rate": round(count * 100 / citation_runs, 1)}
        for domain, count in counts.most_common()
    ]
    core = [item for item in frequencies if item["rate"] >= 70]
    rotating = [item for item in frequencies if 30 <= item["rate"] < 70]
    occasional = [item for item in frequencies if item["rate"] < 30]
    ranks = defaultdict(list)
    for row in result_rows:
        for domain, rank in row.get("citation_ranks", {}).items():
            if rank:
                ranks[domain].append(int(rank))
    rank_volatility = round(sum(pstdev(values) for values in ranks.values() if len(values) > 1) /
                            max(1, sum(1 for values in ranks.values() if len(values) > 1)), 2)
    runs = len(result_rows)
    if runs < 3 or citation_runs < 2:
        pattern, label = "insufficient", "样本不足"
    elif stability >= .7:
        pattern, label = "stable", "来源稳定"
    elif stability >= .4 or core:
        pattern, label = "rotation", "有规律轮换"
    else:
        pattern, label = "volatile", "波动较高"
    return {
        "runs": runs,
        "citation_runs": citation_runs,
        "coverage_rate": round(citation_runs * 100 / runs, 1) if runs else 0,
        "unique_domains": len(counts),
        "avg_sources": round(appearances / citation_runs, 1) if citation_runs else 0,
        "stability": round(stability * 100, 1),
        "change_rate": round((1 - stability) * 100, 1),
        "entropy": round(entropy * 100, 1),
        "rank_volatility": rank_volatility,
        "pattern": pattern,
        "pattern_label": label,
        "core_domains": core[:5],
        "rotating_domains": rotating[:5],
        "occasional_domains": occasional[:5],
    }


class MonitorService:
    def __init__(self, db_path: str, webdriver_manager=None, api_collector=None):
        self.db_path = db_path
        self.webdriver_manager = webdriver_manager
        self.api_collector = api_collector

    def settings(self) -> dict:
        with connect(self.db_path) as conn:
            row = row_dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
        row["schedule_enabled"] = bool(row["schedule_enabled"])
        row["alias_list"] = brand_terms(row["brand_name"], row["aliases"])
        row["owned_domain_list"] = [x.strip().lower().removeprefix("www.") for x in row.get("owned_domains", "").replace("，", ",").split(",") if x.strip()]
        return row

    def update_settings(self, data: dict) -> dict:
        brand = str(data.get("brand_name", "")).strip()
        if not brand:
            raise ValueError("品牌名不能为空")
        minutes = max(5, int(data.get("schedule_minutes", 1440)))
        mode = data.get("schedule_mode", "demo")
        if mode not in {"demo", "webhook", "webdriver", "api", "auto"}:
            raise ValueError("定时模式不受支持")
        with connect(self.db_path) as conn:
            conn.execute(
                """UPDATE settings SET brand_name=?, aliases=?, owned_domains=?, webhook_url=?,
                   schedule_enabled=?, schedule_minutes=?, schedule_mode=?, updated_at=? WHERE id=1""",
                (
                    brand,
                    str(data.get("aliases", "")).strip(),
                    str(data.get("owned_domains", "")).strip(),
                    str(data.get("webhook_url", "")).strip(),
                    int(bool(data.get("schedule_enabled", False))),
                    minutes,
                    mode,
                    iso_now(),
                ),
            )
        return self.settings()

    def config(self) -> dict:
        with connect(self.db_path) as conn:
            prompts = [dict(x) for x in conn.execute("SELECT * FROM prompts ORDER BY id")]
            platforms = [dict(x) for x in conn.execute("SELECT * FROM platforms ORDER BY id")]
        for item in prompts + platforms:
            item["active"] = bool(item["active"])
        return {"settings": self.settings(), "prompts": prompts, "platforms": platforms}

    def platform(self, platform_id: int) -> dict:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM platforms WHERE id=?", (platform_id,)).fetchone()
        if not row:
            raise ValueError("平台不存在")
        return dict(row)

    def platforms(self) -> list[dict]:
        with connect(self.db_path) as conn:
            return [dict(x) for x in conn.execute("SELECT * FROM platforms ORDER BY id")]

    def add_prompt(self, text: str) -> dict:
        text = text.strip()
        if not text:
            raise ValueError("提示词不能为空")
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO prompts (text, active, created_at) VALUES (?, 1, ?)", (text, iso_now())
            )
            row = conn.execute("SELECT * FROM prompts WHERE id=?", (cursor.lastrowid,)).fetchone()
            return dict(row)

    def reverse_prompt_suggestions(self, goal: str, limit: int = 12) -> dict:
        goal = str(goal or "").strip()
        limit = max(1, min(30, int(limit or 12)))
        settings = self.settings()
        if not goal:
            goal = f"让目标用户在品牌发现、推荐、排行和对比问题中看到{settings['brand_name']}"
        if len(goal) > 500:
            raise ValueError("最终目标不能超过 500 字")
        with connect(self.db_path) as conn:
            history = [dict(row) for row in conn.execute(
                """SELECT ru.prompt_text,COUNT(DISTINCT r.id) runs,
                   ROUND(AVG(CASE WHEN r.status='success' THEN r.brand_hit END)*100,1) hit_rate,
                   ROUND(AVG(o.local_relevance),4) avg_relevance
                   FROM runs ru LEFT JOIN results r ON r.run_id=ru.id
                   LEFT JOIN source_observations o ON o.result_id=r.id
                   GROUP BY ru.prompt_text ORDER BY runs DESC,hit_rate DESC LIMIT 30"""
            )]
            existing = [row[0] for row in conn.execute("SELECT text FROM prompts ORDER BY id")]
            actual_queries = [dict(row) for row in conn.execute(
                """SELECT query_text,COUNT(*) count FROM search_queries
                   WHERE query_text!='' GROUP BY query_text ORDER BY count DESC LIMIT 20"""
            )]

        subject = "目标品类"
        for text in existing:
            candidate = re.split(r"品牌|推荐|排名|排行|对比|怎么|如何|有哪些|选购", text, maxsplit=1)[0].strip()
            if len(candidate) >= 2:
                subject = candidate
                break
        fallback = [
            (f"{subject}都有哪些主流品牌？", "品牌发现", "覆盖用户建立候选品牌池的入口问题"),
            (f"{subject}哪个品牌更值得推荐？", "推荐", "直接触发模型给出品牌候选与推荐理由"),
            (f"{subject}品牌排行榜怎么看？", "排行", "测试目标品牌能否进入榜单型回答"),
            (f"{subject}进口品牌和国产品牌有什么区别？", "对比", "通过品牌阵营比较扩大候选集合"),
            (f"第一次买{subject}应该怎么选？", "新手选购", "新手问题通常需要品牌、参数和避坑建议"),
            (f"家用场景选择{subject}要看哪些指标？", "使用场景", "从场景与参数引出符合条件的品牌"),
            (f"预算有限时{subject}有哪些靠谱选择？", "预算", "测试价格约束下目标品牌的进入能力"),
            (f"长期使用{subject}更看重哪些品牌能力？", "长期使用", "引出可靠性、服务和耗材等品牌证据"),
            (f"{subject}常见品牌的优缺点分别是什么？", "优缺点", "比较型回答更容易形成多品牌提及"),
            (f"适合睡眠呼吸暂停人群的{subject}怎么选？", "目标人群", "用具体人群需求触发产品与品牌匹配"),
            (f"{subject}售后服务和耗材成本怎么比较？", "售后成本", "从长期成本反推品牌与渠道选择"),
            (f"专业机构通常如何评价{subject}品牌？", "权威评价", "测试权威信源与专业评价对品牌曝光的影响"),
        ]
        instruction = (
            f"最终目标：{goal}\n监测品类：{subject}\n目标品牌：{settings['brand_name']}（生成的问题中不要直接出现该品牌）\n"
            f"历史提示词表现：{json.dumps(history[:12], ensure_ascii=False)}\n"
            f"平台实际搜索词：{json.dumps(actual_queries[:12], ensure_ascii=False)}\n"
            f"现有提示词：{json.dumps(existing, ensure_ascii=False)}\n请生成最多 {limit} 个新的自然用户问题。"
        )
        provider = "数据规则"
        raw_items = []
        ai_error = ""
        if self.api_collector and hasattr(self.api_collector, "reverse_prompts"):
            try:
                generated = self.api_collector.reverse_prompts(instruction)
                provider = generated.get("provider", "AI")
                raw_items = generated.get("items", [])
            except Exception as exc:
                ai_error = str(exc)
        candidates = []
        for item in raw_items:
            if isinstance(item, str):
                candidates.append((item, "AI 扩展", "模型根据目标与历史证据反推"))
            elif isinstance(item, dict):
                candidates.append((str(item.get("text", "")), str(item.get("intent", "AI 扩展")),
                                   str(item.get("reason", "模型根据目标与历史证据反推"))))
        candidates.extend(fallback)
        brand_values = [value.casefold() for value in settings["alias_list"]]
        existing_folded = {text.casefold() for text in existing}
        seen = set()
        suggestions = []
        total_runs = sum(int(row.get("runs") or 0) for row in history)
        for text, intent, reason in candidates:
            text = re.sub(r"\s+", " ", text).strip(" -—。")
            folded = text.casefold()
            if not text or folded in seen or folded in existing_folded or any(brand in folded for brand in brand_values):
                continue
            seen.add(folded)
            closest = max(history, key=lambda row: relevance_score(text, row["prompt_text"]), default=None)
            similarity = relevance_score(text, closest["prompt_text"]) if closest else 0
            benchmark_hit = float(closest.get("hit_rate") or 0) / 100 if closest else .35
            observed_relevance = float(closest.get("avg_relevance") or 0) if closest else 0
            goal_fit = relevance_score(goal, text)
            predicted = round(min(95, max(20, (benchmark_hit * .45 + similarity * .25 +
                                                     observed_relevance * .15 + goal_fit * .15) * 100)))
            evidence = (f"参考最相近历史问题“{closest['prompt_text']}”，命中率 {closest.get('hit_rate') or 0}%，"
                        f"来源相关度 {round(observed_relevance * 100)}%。") if closest else "暂无历史样本，先作为探索问题测试。"
            suggestions.append({
                "text": text, "intent": intent or "探索", "reason": reason or "目标导向扩展",
                "predicted_exposure": predicted, "confidence": "高" if total_runs >= 20 else "中" if total_runs >= 5 else "低",
                "evidence": evidence, "goal_fit": round(goal_fit * 100),
            })
            if len(suggestions) >= limit:
                break
        suggestions.sort(key=lambda item: (-item["predicted_exposure"], -item["goal_fit"], item["text"]))
        return {"goal": goal, "subject": subject, "provider": provider, "ai_error": ai_error,
                "history_runs": total_runs, "suggestions": suggestions}

    def ai_research_context(self, days: int = 30) -> dict:
        days = min(365, max(0, int(days or 0)))
        dashboard = self.dashboard(limit=30, days=days)
        settings = self.settings()
        retrieval = dashboard.get("retrieval", {})
        recent = []
        for result in dashboard.get("results", [])[:15]:
            recent.append({
                "platform": result["platform_name"], "prompt": result["prompt_text"],
                "captured_at": result["captured_at"], "status": result["status"],
                "collection_method": result.get("collection_method") or result.get("mode"),
                "brand_hit": result["brand_hit"], "rank_position": result["rank_position"],
                "citation_count": result["citation_count"], "error": result.get("error_message", ""),
                "answer_excerpt": str(result.get("answer_text", ""))[:800],
                "source_domains": list(dict.fromkeys(source["domain"] for source in result.get("sources", [])))[:12],
            })
        data = {
            "scope": {
                "brand": settings["brand_name"], "aliases": settings["alias_list"],
                "owned_domains": settings["owned_domain_list"], "days": days or "all",
                "generated_at": iso_now(),
            },
            "metrics": dashboard["totals"],
            "platform_performance": dashboard["platforms"],
            "prompt_performance": dashboard["prompts"],
            "top_cited_domains": dashboard["sources"],
            "retrieval": {
                "funnel": retrieval.get("funnel", {}), "actual_queries": retrieval.get("queries", [])[:20],
                "domain_opportunities": retrieval.get("opportunities", [])[:20],
                "strategy_insights": retrieval.get("insights", []),
                "same_prompt_variability": retrieval.get("variability", [])[:20],
                "platform_strategies": retrieval.get("platform_strategies", []),
            },
            "recent_evidence": recent,
        }
        return {
            "data": data,
            "meta": {
                "days": days, "results": len(dashboard.get("results", [])),
                "platforms": len(dashboard.get("platforms", [])),
                "prompts": len(dashboard.get("prompts", [])),
                "sources": len(dashboard.get("sources", [])),
                "last_capture": dashboard["totals"].get("last_capture"),
            },
        }

    def ai_chat(self, messages: list[dict], days: int = 30) -> dict:
        if not self.api_collector or not hasattr(self.api_collector, "openai_chat"):
            raise RuntimeError("OpenAI 分析服务未初始化")
        if not isinstance(messages, list):
            raise ValueError("messages 必须是数组")
        clean_messages = []
        for message in messages[-20:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", ""))
            content = str(message.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                clean_messages.append({"role": role, "content": content[:12000]})
        if not clean_messages:
            clean_messages = [{
                "role": "user",
                "content": ("请对当前监测数据做默认评估：先给出曝光结论和数据可信度，再指出最重要的问题，"
                            "解释可能原因，并给出按优先级排序、可以继续验证的提升行动。"),
            }]
        context = self.ai_research_context(days)
        methodology = {
            "product_goal": "监测目标品牌在多种 AI 平台和品牌相关问题中的曝光、位置与引用来源，并通过重复实验寻找可提升曝光的规律。",
            "workflow": ["采集回答与来源", "检测品牌命中和近似位次", "记录召回/选材/引用阶段", "比较提示词差异和时间波动", "形成可验证的内容与信源策略"],
            "metric_rules": {
                "visibility": "成功回答中出现目标品牌的比例",
                "rank": "品牌首次出现在列表中的近似位置，只在命中回答中统计",
                "citation_coverage": "成功回答中至少包含一个外部来源的比例",
                "local_relevance": "系统计算的词项重合度，不等同于平台内部相关度",
                "stability": "同平台同提示词多轮引用域名集合的平均重合率；少于 3 轮只能视为样本不足",
            },
            "evidence_boundary": "只把 API 或网页明确暴露的过程当作观测事实；不可见的搜索、排序和选材过程不得被当作事实补全。演示数据不能代表平台实时表现。",
        }
        instructions = (
            "你是 BrandScope 的 AI 品牌曝光研究助理。只根据下面的方法论和当前数据回答。"
            "必须区分：观测事实、基于数据的推断、目前未知；不要把相关性写成因果。"
            "样本不足时要明确说明，不要用看似精确的结论掩盖不确定性。"
            "回答默认使用中文，先给结论，再列证据、问题和行动；行动必须可执行、可复测、可衡量。"
            "如果用户询问当前数据之外的事实，明确说明数据中没有。\n\n"
            f"应用方法论：\n{json.dumps(methodology, ensure_ascii=False)}\n\n"
            f"当前监测上下文：\n{json.dumps(context['data'], ensure_ascii=False)}"
        )
        result = self.api_collector.openai_chat(instructions, clean_messages)
        return {**result, "context_meta": context["meta"]}

    def toggle(self, table: str, item_id: int, active: bool) -> None:
        if table not in {"prompts", "platforms"}:
            raise ValueError("无效配置类型")
        with connect(self.db_path) as conn:
            conn.execute(f"UPDATE {table} SET active=? WHERE id=?", (int(active), item_id))

    def delete_prompt(self, prompt_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM prompts WHERE id=?", (prompt_id,))

    def _insert_result(self, conn, run_id: int, platform_id: int, collection, terms: list[str], prompt_text: str):
        hit, count, rank = analyze_answer(collection.answer, terms)
        sources = normalize_sources(collection.sources, collection.answer)
        cursor = conn.execute(
            """INSERT INTO results
               (run_id, platform_id, answer_text, brand_hit, mention_count, rank_position,
                citation_count, status, error_message, captured_at, collection_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, platform_id, collection.answer, int(hit), count, rank, len(sources),
             collection.status, collection.error, iso_now(), collection.method),
        )
        conn.executemany(
            "INSERT INTO sources (result_id, url, domain, title) VALUES (?, ?, ?, ?)",
            [(cursor.lastrowid, x["url"], x["domain"], x["title"]) for x in sources],
        )
        result_id = cursor.lastrowid
        conn.executemany(
            """INSERT INTO search_queries
               (result_id, provider, query_text, query_rank, evidence_level, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(
                result_id, str(x.get("provider", "")), str(x.get("query_text", "")),
                x.get("query_rank"), str(x.get("evidence_level", "observed")),
                json.dumps(x.get("metadata", {}), ensure_ascii=False),
            ) for x in collection.search_queries if str(x.get("query_text", "")).strip()],
        )
        observations = list(collection.source_observations)
        if not observations:
            observations = [{
                **source, "provider": collection.method, "query_text": prompt_text,
                "selected": True, "cited": True, "citation_rank": index,
                "evidence_level": "final_only",
            } for index, source in enumerate(sources, 1)]
        rows = []
        for observation in observations:
            normalized = normalize_sources([observation])
            if not normalized:
                continue
            source = normalized[0]
            snippet = str(observation.get("snippet", ""))[:8000]
            local_score = observation.get("local_relevance")
            if local_score is None:
                local_score = relevance_score(prompt_text, f"{source['title']} {snippet}")
            rows.append((
                result_id, str(observation.get("provider", collection.method)),
                str(observation.get("query_text", prompt_text)), observation.get("query_rank"),
                source["url"], source["domain"], source["title"], observation.get("search_rank"),
                observation.get("provider_score"), local_score,
                int(bool(observation.get("retrieved", False))), int(bool(observation.get("selected", False))),
                int(bool(observation.get("cited", False))), observation.get("citation_rank"), snippet,
                str(observation.get("published_at", "")), str(observation.get("evidence_level", "observed")),
                json.dumps(observation.get("metadata", {}), ensure_ascii=False),
            ))
        conn.executemany(
            """INSERT INTO source_observations
               (result_id, provider, query_text, query_rank, url, domain, title, search_rank,
                provider_score, local_relevance, retrieved, selected, cited, citation_rank,
                snippet, published_at, evidence_level, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def run(self, prompt_id: int, mode: str = "demo", trigger: str = "manual") -> int:
        settings = self.settings()
        if mode not in {"demo", "webhook", "webdriver", "api", "auto"}:
            raise ValueError("请选择自动、官方 API、WebDriver、Webhook 或演示采集")
        if mode == "webhook" and not settings["webhook_url"]:
            raise ValueError("请先设置 Webhook 地址")
        if mode == "webdriver" and not self.webdriver_manager:
            raise ValueError("WebDriver 采集器未初始化")
        if mode in {"api", "auto"} and not self.api_collector:
            raise ValueError("官方 API 采集器未初始化")
        with connect(self.db_path) as conn:
            prompt = conn.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
            platforms = conn.execute("SELECT * FROM platforms WHERE active=1 ORDER BY id").fetchall()
            if not prompt:
                raise ValueError("提示词不存在")
            cursor = conn.execute(
                """INSERT INTO runs
                   (mode, trigger_type, brand_name, prompt_id, prompt_text, status, started_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?)""",
                (mode, trigger, settings["brand_name"], prompt_id, prompt["text"], iso_now()),
            )
            run_id = cursor.lastrowid
        terms = brand_terms(settings["brand_name"], settings["aliases"])
        for platform_row in platforms:
            platform = dict(platform_row)
            if mode == "demo":
                result = demo_collect(platform["slug"], prompt["text"])
                result.method = "demo"
            elif mode == "webhook":
                result = webhook_collect(
                    settings["webhook_url"],
                    {k: platform[k] for k in ("slug", "name", "url")},
                    prompt["text"],
                    {"name": settings["brand_name"], "aliases": terms},
                )
                result.method = "webhook"
            elif mode == "webdriver":
                result = self.webdriver_manager.collect(platform, prompt["text"], enable_web_search=True)
                result.method = "webdriver"
            elif mode == "api":
                result = self.api_collector.collect(platform, prompt["text"])
                result.method = "api"
            else:
                api_result = self.api_collector.collect(platform, prompt["text"])
                api_result.method = "api"
                if api_result.status == "success":
                    result = api_result
                elif self.webdriver_manager:
                    result = self.webdriver_manager.collect(platform, prompt["text"], enable_web_search=True)
                    result.method = "webdriver"
                    if result.status != "success":
                        result.error = f"API：{api_result.error}；WebDriver：{result.error}"
                else:
                    result = api_result
            with connect(self.db_path) as conn:
                self._insert_result(conn, run_id, platform["id"], result, terms, prompt["text"])
        with connect(self.db_path) as conn:
            conn.execute("UPDATE runs SET status='completed', finished_at=? WHERE id=?", (iso_now(), run_id))
        return run_id

    def run_all(self, mode: str, trigger: str = "manual") -> list[int]:
        with connect(self.db_path) as conn:
            prompt_ids = [x[0] for x in conn.execute("SELECT id FROM prompts WHERE active=1 ORDER BY id")]
        return [self.run(prompt_id, mode, trigger) for prompt_id in prompt_ids]

    def manual_capture(self, data: dict) -> int:
        settings = self.settings()
        prompt_id = int(data["prompt_id"])
        platform_id = int(data["platform_id"])
        with connect(self.db_path) as conn:
            prompt = conn.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
            platform = conn.execute("SELECT * FROM platforms WHERE id=?", (platform_id,)).fetchone()
            if not prompt or not platform:
                raise ValueError("提示词或平台不存在")
            cursor = conn.execute(
                """INSERT INTO runs (mode, trigger_type, brand_name, prompt_id, prompt_text,
                   status, started_at, finished_at) VALUES ('manual', 'manual', ?, ?, ?, 'completed', ?, ?)""",
                (settings["brand_name"], prompt_id, prompt["text"], iso_now(), iso_now()),
            )
            sources = [x.strip() for x in str(data.get("sources", "")).splitlines() if x.strip()]
            from .collector import Collection
            collection = Collection(answer=str(data.get("answer", "")), sources=sources, method="manual")
            self._insert_result(
                conn, cursor.lastrowid, platform_id, collection,
                brand_terms(settings["brand_name"], settings["aliases"]), prompt["text"],
            )
            return cursor.lastrowid

    def dashboard(self, limit: int = 100, days: int = 0) -> dict:
        days = max(0, int(days or 0))
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds") if days else None
        result_where = "WHERE r.captured_at >= ?" if cutoff else ""
        result_params = (cutoff,) if cutoff else ()
        join_date = "AND r.captured_at >= ?" if cutoff else ""
        with connect(self.db_path) as conn:
            totals = dict(conn.execute(
                f"""SELECT COUNT(*) collected,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN 1 ELSE 0 END),0) total,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN r.brand_hit ELSE 0 END),0) hits,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN r.citation_count ELSE 0 END),0) citations,
                   COALESCE(SUM(CASE WHEN r.status='success' AND r.citation_count>0 THEN 1 ELSE 0 END),0) cited_results,
                   COALESCE(SUM(CASE WHEN r.status!='success' THEN 1 ELSE 0 END),0) errors,
                   COUNT(DISTINCT r.run_id) runs,
                   ROUND(AVG(CASE WHEN r.status='success' AND r.brand_hit=1 THEN r.rank_position END),1) avg_rank,
                   MAX(r.captured_at) last_capture
                   FROM results r {result_where}""",
                result_params,
            ).fetchone())
            unique_sources = conn.execute(
                f"""SELECT COUNT(DISTINCT s.domain) FROM sources s
                    JOIN results r ON r.id=s.result_id {result_where}""",
                result_params,
            ).fetchone()[0]
            platform_stats = [dict(x) for x in conn.execute(
                f"""SELECT p.name, p.slug, p.color,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN 1 ELSE 0 END),0) total,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN r.brand_hit ELSE 0 END),0) hits,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN r.citation_count ELSE 0 END),0) citations,
                   COALESCE(SUM(CASE WHEN r.status='success' AND r.citation_count>0 THEN 1 ELSE 0 END),0) cited_results,
                   COALESCE(SUM(CASE WHEN r.status!='success' THEN 1 ELSE 0 END),0) errors,
                   ROUND(AVG(CASE WHEN r.status='success' AND r.brand_hit=1 THEN r.rank_position END),1) avg_rank
                   FROM platforms p LEFT JOIN results r ON r.platform_id=p.id {join_date}
                   GROUP BY p.id ORDER BY p.id""",
                result_params,
            )]
            source_stats = [dict(x) for x in conn.execute(
                f"""SELECT s.domain, COUNT(*) count FROM sources s
                   JOIN results r ON r.id=s.result_id {result_where}
                   GROUP BY s.domain ORDER BY count DESC, s.domain LIMIT 12""",
                result_params,
            )]
            funnel = dict(conn.execute(
                f"""SELECT
                   COUNT(DISTINCT CASE WHEN o.retrieved=1 THEN o.result_id || '|' || o.url END) retrieved,
                   COUNT(DISTINCT CASE WHEN o.selected=1 THEN o.result_id || '|' || o.url END) selected,
                   COUNT(DISTINCT CASE WHEN o.cited=1 THEN o.result_id || '|' || o.url END) cited,
                   ROUND(AVG(CASE WHEN o.retrieved=1 THEN o.local_relevance END),4) avg_relevance
                   FROM source_observations o JOIN results r ON r.id=o.result_id {result_where}""",
                result_params,
            ).fetchone())
            stage_sources = [dict(x) for x in conn.execute(
                f"""SELECT stage, domain, COUNT(*) count,
                   ROUND(AVG(local_relevance),4) avg_relevance,
                   ROUND(AVG(provider_score),4) avg_provider_score
                   FROM (
                     SELECT 'retrieved' stage,o.domain,o.local_relevance,o.provider_score,r.captured_at
                       FROM source_observations o JOIN results r ON r.id=o.result_id WHERE o.retrieved=1
                     UNION ALL
                     SELECT 'selected',o.domain,o.local_relevance,o.provider_score,r.captured_at
                       FROM source_observations o JOIN results r ON r.id=o.result_id WHERE o.selected=1
                     UNION ALL
                     SELECT 'cited',o.domain,o.local_relevance,o.provider_score,r.captured_at
                       FROM source_observations o JOIN results r ON r.id=o.result_id WHERE o.cited=1
                   ) x {"WHERE captured_at >= ?" if cutoff else ""}
                   GROUP BY stage,domain ORDER BY stage,count DESC,domain""",
                result_params,
            )]
            domain_funnel = [dict(x) for x in conn.execute(
                f"""SELECT o.domain,
                   SUM(CASE WHEN o.retrieved=1 THEN 1 ELSE 0 END) retrieved,
                   SUM(CASE WHEN o.selected=1 THEN 1 ELSE 0 END) selected,
                   SUM(CASE WHEN o.cited=1 THEN 1 ELSE 0 END) cited,
                   ROUND(AVG(o.local_relevance),4) avg_relevance,
                   ROUND(AVG(o.provider_score),4) avg_provider_score
                   FROM source_observations o JOIN results r ON r.id=o.result_id {result_where}
                   GROUP BY o.domain ORDER BY retrieved DESC,cited DESC LIMIT 50""",
                result_params,
            )]
            query_stats = [dict(x) for x in conn.execute(
                f"""SELECT q.provider,q.query_text,COUNT(*) count
                   FROM search_queries q JOIN results r ON r.id=q.result_id {result_where}
                   GROUP BY q.provider,q.query_text ORDER BY count DESC,q.provider LIMIT 50""",
                result_params,
            )]
            prompt_stats = [dict(x) for x in conn.execute(
                f"""SELECT ru.prompt_text, COUNT(r.id) collected,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN 1 ELSE 0 END),0) total,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN r.brand_hit ELSE 0 END),0) hits,
                   COALESCE(SUM(CASE WHEN r.status='success' THEN r.citation_count ELSE 0 END),0) citations,
                   COALESCE(SUM(CASE WHEN r.status!='success' THEN 1 ELSE 0 END),0) errors,
                   ROUND(AVG(CASE WHEN r.status='success' AND r.brand_hit=1 THEN r.rank_position END),1) avg_rank
                   FROM runs ru JOIN results r ON r.run_id=ru.id
                   {result_where} GROUP BY ru.prompt_text ORDER BY ru.prompt_text""",
                result_params,
            )]
            results = [dict(x) for x in conn.execute(
                f"""SELECT r.id, r.run_id, r.answer_text, r.brand_hit, r.mention_count,
                   r.rank_position, r.citation_count, r.status, r.error_message, r.captured_at,
                   r.collection_method,
                   p.name platform_name, p.slug platform_slug, p.color,
                   ru.prompt_text, ru.mode, ru.trigger_type
                   FROM results r JOIN platforms p ON p.id=r.platform_id
                   JOIN runs ru ON ru.id=r.run_id {result_where}
                   ORDER BY r.id DESC LIMIT ?""", (*result_params, limit)
            )]
            for result in results:
                result["brand_hit"] = bool(result["brand_hit"])
                result["sources"] = [dict(x) for x in conn.execute(
                    "SELECT url, domain, title FROM sources WHERE result_id=?", (result["id"],)
                )]
                result["search_queries"] = [dict(x) for x in conn.execute(
                    """SELECT provider,query_text,query_rank,evidence_level
                       FROM search_queries WHERE result_id=? ORDER BY query_rank,id""", (result["id"],)
                )]
                result["source_observations"] = [dict(x) for x in conn.execute(
                    """SELECT provider,query_text,url,domain,title,search_rank,provider_score,
                       local_relevance,retrieved,selected,cited,citation_rank,snippet,published_at,evidence_level
                       FROM source_observations WHERE result_id=? ORDER BY cited DESC,selected DESC,search_rank,id LIMIT 100""",
                    (result["id"],),
                )]
            trend = [dict(x) for x in conn.execute(
                f"""SELECT substr(r.captured_at,1,10) day,
                   SUM(CASE WHEN r.status='success' THEN 1 ELSE 0 END) total,
                   SUM(CASE WHEN r.status='success' THEN r.brand_hit ELSE 0 END) hits,
                   SUM(CASE WHEN r.status='success' AND r.citation_count>0 THEN 1 ELSE 0 END) cited_results,
                   SUM(CASE WHEN r.status!='success' THEN 1 ELSE 0 END) errors
                   FROM results r {result_where} GROUP BY day ORDER BY day DESC LIMIT 30""",
                result_params,
            )]
            variability_rows = [dict(x) for x in conn.execute(
                f"""SELECT r.id result_id,p.name platform_name,p.slug platform_slug,p.color,
                   ru.prompt_text,r.captured_at,o.domain,o.citation_rank
                   FROM results r JOIN platforms p ON p.id=r.platform_id
                   JOIN runs ru ON ru.id=r.run_id
                   LEFT JOIN source_observations o ON o.result_id=r.id AND o.cited=1
                   {result_where} AND r.status='success'
                   ORDER BY r.id,o.citation_rank,o.id""" if result_where else
                """SELECT r.id result_id,p.name platform_name,p.slug platform_slug,p.color,
                   ru.prompt_text,r.captured_at,o.domain,o.citation_rank
                   FROM results r JOIN platforms p ON p.id=r.platform_id
                   JOIN runs ru ON ru.id=r.run_id
                   LEFT JOIN source_observations o ON o.result_id=r.id AND o.cited=1
                   WHERE r.status='success' ORDER BY r.id,o.citation_rank,o.id""",
                result_params,
            )]
        totals["hit_rate"] = round(totals["hits"] * 100 / totals["total"], 1) if totals["total"] else 0
        totals["citation_rate"] = round(totals["cited_results"] * 100 / totals["total"], 1) if totals["total"] else 0
        totals["success_rate"] = round(totals["total"] * 100 / totals["collected"], 1) if totals["collected"] else 0
        totals["unique_sources"] = unique_sources
        for stat in platform_stats:
            stat["hit_rate"] = round(stat["hits"] * 100 / stat["total"], 1) if stat["total"] else 0
            stat["citation_rate"] = round(stat["cited_results"] * 100 / stat["total"], 1) if stat["total"] else 0
        for stat in prompt_stats:
            stat["hit_rate"] = round(stat["hits"] * 100 / stat["total"], 1) if stat["total"] else 0
        prompt_stats.sort(key=lambda x: (x["hit_rate"], -x["total"], x["prompt_text"]))
        owned_domains = self.settings()["owned_domain_list"]
        def owned(domain):
            return any(domain == item or domain.endswith("." + item) for item in owned_domains)
        for row in domain_funnel:
            row["owned"] = owned(row["domain"])
            row["selection_rate"] = round(row["selected"] * 100 / row["retrieved"], 1) if row["retrieved"] else 0
            row["citation_rate"] = round(row["cited"] * 100 / row["retrieved"], 1) if row["retrieved"] else 0
        owned_rows = [row for row in domain_funnel if row["owned"]]
        funnel["owned_retrieved"] = sum(row["retrieved"] for row in owned_rows)
        funnel["owned_selected"] = sum(row["selected"] for row in owned_rows)
        funnel["owned_cited"] = sum(row["cited"] for row in owned_rows)
        funnel["selection_rate"] = round(funnel["selected"] * 100 / funnel["retrieved"], 1) if funnel["retrieved"] else 0
        funnel["citation_rate"] = round(funnel["cited"] * 100 / funnel["retrieved"], 1) if funnel["retrieved"] else 0
        opportunities = sorted(
            [row for row in domain_funnel if row["retrieved"] and not row["owned"]],
            key=lambda row: (-row["retrieved"], row["citation_rate"], -float(row["avg_relevance"] or 0)),
        )[:12]
        insights = []
        if not owned_domains:
            insights.append({"level": "setup", "title": "先配置品牌自有域名",
                             "detail": "配置官网及内容站域名后，系统才能计算自有内容在召回、选材和引用阶段的流失位置。"})
        elif not funnel["owned_retrieved"]:
            insights.append({"level": "high", "title": "自有内容尚未进入搜索召回",
                             "detail": "优先围绕实际搜索词建设可索引页面，并强化标题、问题表述、结构化答案和站点可抓取性。"})
        elif not funnel["owned_selected"]:
            insights.append({"level": "high", "title": "自有内容已被搜到，但没有进入模型选材",
                             "detail": "对比高相关度入选页面，补充直接回答、品牌证据、产品参数、更新时间和权威出处。"})
        elif not funnel["owned_cited"]:
            insights.append({"level": "medium", "title": "自有内容进入选材，但没有成为最终引用",
                             "detail": "检查核心结论是否清晰可引用，并增加独立数据、专家背书、对比表和稳定的原始来源链接。"})
        else:
            insights.append({"level": "good", "title": "自有内容已经形成引用",
                             "detail": "继续跟踪不同提示词和平台的引用稳定性，避免只依赖单一页面或单一搜索词。"})
        top_cited = sorted(domain_funnel, key=lambda row: (-row["cited"], -row["retrieved"]))[:3]
        if top_cited:
            insights.append({"level": "info", "title": "重点研究高引用信源",
                             "detail": "当前高引用网站：" + "、".join(row["domain"] for row in top_cited) + "。分析其内容结构、更新时间和可验证证据。"})
        repeated = defaultdict(dict)
        group_meta = {}
        for row in variability_rows:
            key = (row["platform_slug"], row["prompt_text"])
            group_meta[key] = row
            result = repeated[key].setdefault(row["result_id"], {
                "cited_domains": set(), "citation_ranks": {}, "captured_at": row["captured_at"]
            })
            if row["domain"]:
                result["cited_domains"].add(row["domain"])
                if row["citation_rank"]:
                    result["citation_ranks"][row["domain"]] = row["citation_rank"]
        variability = []
        for key, result_map in repeated.items():
            metrics = source_variability(list(result_map.values()))
            meta = group_meta[key]
            metrics.update({"platform_name": meta["platform_name"], "platform_slug": meta["platform_slug"],
                            "color": meta["color"], "prompt_text": meta["prompt_text"]})
            variability.append(metrics)
        variability.sort(key=lambda item: (-item["runs"], item["platform_name"], item["prompt_text"]))

        platform_groups = defaultdict(list)
        for item in variability:
            platform_groups[item["platform_slug"]].append(item)
        platform_strategies = []
        for slug, items in platform_groups.items():
            qualified = [item for item in items if item["pattern"] != "insufficient"]
            weighted_stability = round(sum(item["stability"] * item["runs"] for item in qualified) /
                                       sum(item["runs"] for item in qualified), 1) if qualified else 0
            platform_results = [result for key, result_map in repeated.items() if key[0] == slug
                                for result in result_map.values()]
            overall = source_variability(platform_results)
            prompt_profiles = []
            for key, result_map in repeated.items():
                if key[0] != slug:
                    continue
                domains = set().union(*(result["cited_domains"] for result in result_map.values()))
                if domains:
                    prompt_profiles.append({"prompt_text": key[1], "domains": domains})
            prompt_pairs = []
            for left, right in combinations(prompt_profiles, 2):
                union = left["domains"] | right["domains"]
                similarity = len(left["domains"] & right["domains"]) / len(union) if union else 1.0
                prompt_pairs.append({
                    "prompt_a": left["prompt_text"], "prompt_b": right["prompt_text"],
                    "similarity": round(similarity * 100, 1), "change": round((1 - similarity) * 100, 1),
                    "shared_domains": sorted(left["domains"] & right["domains"])[:5],
                })
            prompt_overlaps = [pair["similarity"] / 100 for pair in prompt_pairs]
            prompt_similarity = round(sum(prompt_overlaps) * 100 / len(prompt_overlaps), 1) if prompt_overlaps else None
            prompt_effect = round(100 - prompt_similarity, 1) if prompt_similarity is not None else None
            cross_prompt_counts = Counter(domain for profile in prompt_profiles for domain in profile["domains"])
            cross_prompt_core = [
                {"domain": domain, "prompt_count": count,
                 "rate": round(count * 100 / len(prompt_profiles), 1)}
                for domain, count in cross_prompt_counts.most_common()
                if len(prompt_profiles) and count / len(prompt_profiles) >= .7
            ][:5]
            if prompt_effect is None:
                prompt_effect_label = "待积累不同提示词"
            elif prompt_effect >= 60:
                prompt_effect_label = "提示词影响明显"
            elif prompt_effect >= 30:
                prompt_effect_label = "提示词有一定影响"
            else:
                prompt_effect_label = "跨提示词较稳定"
            domain_counts = Counter()
            for item in items:
                for domain in item["core_domains"]:
                    domain_counts[domain["domain"]] += domain["runs"]
                for domain in item["rotating_domains"]:
                    domain_counts[domain["domain"]] += domain["runs"]
            preferred = [domain for domain, _ in domain_counts.most_common(5)]
            baseline_stability = weighted_stability if qualified else overall["stability"]
            if overall["pattern"] == "insufficient":
                diagnosis = "至少完成 3 次品牌相关问题采集，才能判断平台整体信源池是否稳定。"
                action = "先持续运行不同品牌提示词；其中保留少量重复问法，用来区分提示词影响和时间波动。"
            elif not qualified:
                diagnosis = "已能观察品牌问题整体信源变化，但缺少重复问法，暂时不能把提示词差异与随机波动完全分开。"
                action = "继续覆盖不同问题簇，同时选择代表性提示词重复测试，建立随机波动基线。"
            elif baseline_stability >= 70:
                diagnosis = "引用来源高度稳定，平台倾向反复使用一组核心信源。"
                action = "优先进入核心信源：争取收录、品牌提及或建设结构与证据质量相近的权威页面。"
            elif baseline_stability >= 40:
                diagnosis = "来源存在核心集合，同时会在候选网站之间轮换。"
                action = "同时覆盖核心与轮换信源，并统一品牌名称、参数、结论和更新时间，提升多源一致性。"
            else:
                diagnosis = "引用组合波动较高，平台更可能受搜索时效、排序和候选供给影响。"
                action = "分散布局多个高质量第三方来源，持续更新内容，并按日期与时段复测而非押注单一网站。"
            if prompt_effect is not None and prompt_effect >= 60:
                diagnosis += " 不同品牌问题之间的信源差异也很明显，变化更可能由问题意图驱动，而不只是随机性。"
                action += " 按购买推荐、品牌排行、产品对比等问题簇分别建设内容和外部信源。"
            elif prompt_effect is not None and prompt_effect < 30:
                diagnosis += " 换用不同品牌相关问法后，核心信源仍较一致。"
            platform_strategies.append({
                "platform_name": items[0]["platform_name"], "platform_slug": slug, "color": items[0]["color"],
                "tested_prompts": len(items), "qualified_prompts": len(qualified),
                "stability": baseline_stability, "overall_stability": overall["stability"],
                "overall_pattern": overall["pattern"], "preferred_domains": preferred,
                "prompt_similarity": prompt_similarity, "prompt_effect": prompt_effect,
                "prompt_effect_label": prompt_effect_label, "prompt_pairs": prompt_pairs,
                "cross_prompt_core": cross_prompt_core,
                "diagnosis": diagnosis, "action": action,
            })
        return {"totals": totals, "platforms": platform_stats, "sources": source_stats,
                "prompts": prompt_stats, "results": results, "trend": list(reversed(trend)), "days": days,
                "retrieval": {"funnel": funnel, "stage_sources": stage_sources,
                              "domains": domain_funnel, "queries": query_stats,
                              "opportunities": opportunities, "owned_domains": owned_domains,
                              "insights": insights, "variability": variability,
                              "platform_strategies": platform_strategies}}

    def export_csv(self) -> str:
        data = self.dashboard(limit=100000, days=0)["results"]
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(["采集时间", "平台", "提示词", "任务模式", "实际采集链路", "状态", "失败原因", "品牌命中", "提及次数", "出现位次", "引用数", "来源网址", "回答"])
        for row in data:
            writer.writerow([
                row["captured_at"], row["platform_name"], row["prompt_text"], row["mode"], row["collection_method"], row["status"], row["error_message"],
                "是" if row["brand_hit"] else "否", row["mention_count"], row["rank_position"] or "",
                row["citation_count"], "\n".join(x["url"] for x in row["sources"]), row["answer_text"],
            ])
        return output.getvalue()

    def export_retrieval_csv(self) -> str:
        output = io.StringIO()
        output.write("\ufeff")
        writer = csv.writer(output)
        writer.writerow(["采集时间", "平台", "提示词", "采集链路", "搜索服务", "实际搜索词",
                         "搜索排名", "平台分数", "本地相关度", "已召回", "已选材", "已引用",
                         "引用位次", "网站", "标题", "网址", "发布时间", "证据级别", "摘要片段"])
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT r.captured_at,p.name,ru.prompt_text,r.collection_method,o.provider,o.query_text,
                   o.search_rank,o.provider_score,o.local_relevance,o.retrieved,o.selected,o.cited,
                   o.citation_rank,o.domain,o.title,o.url,o.published_at,o.evidence_level,o.snippet
                   FROM source_observations o JOIN results r ON r.id=o.result_id
                   JOIN runs ru ON ru.id=r.run_id JOIN platforms p ON p.id=r.platform_id
                   ORDER BY r.id DESC,o.search_rank,o.id"""
            ).fetchall()
        for row in rows:
            writer.writerow(list(row))
        return output.getvalue()
