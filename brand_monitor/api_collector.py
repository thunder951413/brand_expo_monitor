from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .collector import Collection, normalize_sources, relevance_score


DEFAULTS = {
    "OPENAI_MODEL": "gpt-5.6-luna",
    "OPENAI_BASE_URL": "https://api.openai.com/v1",
    "DOUBAO_MODEL": "doubao-seed-1-6-250615",
    "DOUBAO_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
    "QWEN_MODEL": "qwen-plus",
    "QWEN_BASE_URL": "https://dashscope.aliyuncs.com",
    "BAIDU_MODEL": "ernie-3.5-8k",
    "BAIDU_BASE_URL": "https://qianfan.baidubce.com",
    "TENCENT_HUNYUAN_MODEL": "hunyuan-turbos-latest",
    "DEEPSEEK_MODEL": "deepseek-v4-flash",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "DEEPSEEK_SEARCH_PROVIDER": "baidu",
}

ALLOWED_KEYS = {
    "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
    "DOUBAO_API_KEY", "DOUBAO_MODEL", "DOUBAO_BASE_URL",
    "QWEN_API_KEY", "QWEN_MODEL", "QWEN_BASE_URL",
    "BAIDU_API_KEY", "BAIDU_MODEL", "BAIDU_BASE_URL",
    "TENCENT_SECRET_ID", "TENCENT_SECRET_KEY", "TENCENT_HUNYUAN_MODEL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL",
    "DEEPSEEK_SEARCH_PROVIDER",
}

SECRET_KEYS = {
    "OPENAI_API_KEY",
    "DOUBAO_API_KEY", "QWEN_API_KEY", "BAIDU_API_KEY",
    "TENCENT_SECRET_ID", "TENCENT_SECRET_KEY", "DEEPSEEK_API_KEY",
}


class ApiConfigStore:
    """Local API configuration. Process environment always overrides the file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _file_values(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            return {str(k): str(v) for k, v in data.items() if k in ALLOWED_KEYS and v is not None}
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def values(self) -> dict[str, str]:
        values = {**DEFAULTS, **self._file_values()}
        for key in ALLOWED_KEYS:
            if key in os.environ:
                values[key] = os.environ[key]
        return values

    def update(self, changes: dict[str, Any], clear: Iterable[str] = ()) -> None:
        values = self._file_values()
        for key in clear:
            if key in ALLOWED_KEYS:
                values.pop(key, None)
        for key, value in changes.items():
            if key not in ALLOWED_KEYS or value is None or str(value).strip() == "":
                continue
            values[key] = str(value).strip()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", "utf-8")
        os.chmod(self.path, 0o600)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _sources_from(value: Any) -> list[dict]:
    sources = []
    for item in _walk(value):
        url = item.get("url") or item.get("URL") or item.get("Url") or item.get("ref_url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            title = item.get("title") or item.get("Title") or item.get("name") or item.get("site") or ""
            sources.append({"url": url, "title": str(title or "")})
    return normalize_sources(sources)


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts)
    return ""


def _citation_ids(answer: str) -> list[int]:
    values = re.findall(r"\[(?:ref[_-]?)?(\d+)\]", answer or "", re.I)
    return list(dict.fromkeys(int(x) for x in values))


def _json_object(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    candidate = fenced.group(1) if fenced else text
    if not candidate.startswith("{"):
        match = re.search(r"\{.*\}", candidate, re.S)
        candidate = match.group(0) if match else "{}"
    value = json.loads(candidate)
    return value if isinstance(value, dict) else {}


def _query_records(value: Any, prompt: str, provider: str, include_prompt: bool = False) -> list[dict]:
    queries = []
    if include_prompt:
        queries.append(prompt)
    for item in _walk(value):
        for key in ("query", "search_query", "keyword", "search_keyword"):
            candidate = item.get(key)
            if isinstance(candidate, str) and 1 < len(candidate.strip()) < 300:
                queries.append(candidate.strip())
    return [{"provider": provider, "query_text": text, "query_rank": index,
             "evidence_level": "observed"}
            for index, text in enumerate(dict.fromkeys(queries), 1)]


def _trace_items(prompt: str, answer: str, value: Any, provider: str, *,
                 final_only: bool = False, selected_ranks: set[int] | None = None) -> list[dict]:
    cited_ids = _citation_ids(answer)
    observations, seen = [], set()
    for item in _walk(value):
        url = item.get("url") or item.get("URL") or item.get("Url") or item.get("ref_url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        fallback_rank = len(observations) + 1
        raw_rank = item.get("index", item.get("id", item.get("refer_num", fallback_rank)))
        try:
            rank = int(raw_rank)
        except (TypeError, ValueError):
            rank = fallback_rank
        title = str(item.get("title") or item.get("Title") or item.get("name") or "")
        snippet = str(item.get("content") or item.get("summary") or item.get("passage") or
                      item.get("description") or item.get("desc") or "")
        score = item.get("score", item.get("relevance_score", item.get("rerank_score")))
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        cited = final_only or rank in cited_ids
        selected = final_only or cited or bool(selected_ranks and rank in selected_ranks)
        observations.append({
            "provider": provider, "query_text": prompt, "url": url, "title": title,
            "search_rank": rank, "provider_score": score,
            "local_relevance": relevance_score(prompt, f"{title} {snippet}"),
            "retrieved": not final_only, "selected": selected, "cited": cited,
            "citation_rank": cited_ids.index(rank) + 1 if rank in cited_ids else (rank if final_only else None),
            "snippet": snippet[:8000],
            "published_at": str(item.get("date") or item.get("publish_time") or item.get("published_at") or ""),
            "evidence_level": "final_only" if final_only else "observed",
            "metadata": {"provider_rank": raw_rank},
        })
    return observations


class OfficialApiCollector:
    def __init__(self, config: ApiConfigStore, session=None):
        self.config = config
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def statuses(self, platforms: list[dict] | None = None) -> list[dict]:
        values = self.config.values()
        definitions = {
            "doubao": (bool(values.get("DOUBAO_API_KEY")), "DOUBAO_API_KEY", values["DOUBAO_MODEL"]),
            "qwen": (bool(values.get("QWEN_API_KEY")), "QWEN_API_KEY", values["QWEN_MODEL"]),
            "ernie": (bool(values.get("BAIDU_API_KEY")), "BAIDU_API_KEY", values["BAIDU_MODEL"]),
            "yuanbao": (
                bool(values.get("TENCENT_SECRET_ID") and values.get("TENCENT_SECRET_KEY")),
                "TENCENT_SECRET_ID + TENCENT_SECRET_KEY", values["TENCENT_HUNYUAN_MODEL"],
            ),
        }
        provider = values.get("DEEPSEEK_SEARCH_PROVIDER", "baidu").lower()
        search_ready = bool(values.get("BAIDU_API_KEY")) if provider == "baidu" else bool(
            values.get("TENCENT_SECRET_ID") and values.get("TENCENT_SECRET_KEY")
        )
        definitions["deepseek"] = (
            bool(values.get("DEEPSEEK_API_KEY")) and search_ready,
            f"DEEPSEEK_API_KEY + {provider} 搜索凭证", values["DEEPSEEK_MODEL"],
        )
        names = {x["slug"]: x["name"] for x in platforms or []}
        order = [x["slug"] for x in platforms] if platforms else ["doubao", "qwen", "ernie", "deepseek", "yuanbao"]
        return [
            {"slug": slug, "name": names.get(slug, slug), "configured": ready,
             "message": "API 可采集" if ready else f"缺少 {required}", "model": model}
            for slug in order if slug in definitions for ready, required, model in [definitions[slug]]
        ]

    def configured(self, slug: str) -> bool:
        return next((x["configured"] for x in self.statuses() if x["slug"] == slug), False)

    def public_config(self) -> dict:
        values = self.config.values()
        return {
            "models": {key: values.get(key, "") for key in DEFAULTS if "MODEL" in key},
            "deepseek_search_provider": values.get("DEEPSEEK_SEARCH_PROVIDER", "baidu"),
            "secrets": {key: bool(values.get(key)) for key in SECRET_KEYS},
        }

    def openai_chat(self, instructions: str, messages: list[dict]) -> dict:
        values = self.config.values()
        if not values.get("OPENAI_API_KEY"):
            raise RuntimeError("请先在采集配置中设置 OpenAI API Key")
        safe_messages = []
        for message in messages[-20:]:
            role = str(message.get("role", ""))
            content = str(message.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                safe_messages.append({"role": role, "content": content[:12000]})
        data = self._post(
            f"{values['OPENAI_BASE_URL'].rstrip('/')}/responses",
            headers={"Authorization": f"Bearer {values['OPENAI_API_KEY']}", "Content-Type": "application/json"},
            body={
                "model": values["OPENAI_MODEL"], "instructions": instructions,
                "input": safe_messages, "store": False, "max_output_tokens": 2200,
            }, timeout=180,
        )
        texts = [item.get("text", "") for item in _walk(data.get("output", []))
                 if item.get("type") == "output_text" and isinstance(item.get("text"), str)]
        answer = "\n".join(texts).strip()
        if not answer:
            raise RuntimeError("OpenAI API 未返回文本内容")
        return {
            "answer": answer, "model": str(data.get("model") or values["OPENAI_MODEL"]),
            "response_id": str(data.get("id", "")), "usage": data.get("usage") or {},
        }

    def collect(self, platform: dict, prompt: str) -> Collection:
        slug = platform["slug"]
        if not self.configured(slug):
            status = next(x for x in self.statuses([platform]) if x["slug"] == slug)
            return Collection("", [], "api_unconfigured", status["message"])
        try:
            handler = getattr(self, f"_{slug}")
            collection = handler(prompt)
            if not collection.answer.strip():
                return Collection("", collection.sources, "api_error", "API 未返回回答内容")
            return collection
        except Exception as exc:
            return Collection("", [], "api_error", f"官方 API 调用失败：{exc}")

    def reverse_prompts(self, instruction: str) -> dict:
        """Use the first configured general model to expand goal-driven user questions."""
        c = self.config.values()
        system = (
            "你是品牌可见性研究员。根据目标和历史证据，反推出真实用户可能提出、且可能引出目标品牌的自然问题。"
            "不要在问题中直接写品牌名，不要写营销口号。覆盖发现、推荐、排名、对比、场景、痛点、预算和人群意图。"
            "只返回 JSON：{\"prompts\":[{\"text\":\"...\",\"intent\":\"...\",\"reason\":\"...\"}]}。"
        )
        if c.get("DEEPSEEK_API_KEY"):
            data = self._post(
                f"{c['DEEPSEEK_BASE_URL'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {c['DEEPSEEK_API_KEY']}", "Content-Type": "application/json"},
                body={"model": c["DEEPSEEK_MODEL"], "messages": [
                    {"role": "system", "content": system}, {"role": "user", "content": instruction}
                ], "response_format": {"type": "json_object"}, "temperature": 0.7},
            )
            text = _message_text((data.get("choices") or [{}])[0].get("message", {}).get("content", ""))
            return {"provider": "DeepSeek", "items": _json_object(text).get("prompts", [])}
        if c.get("QWEN_API_KEY"):
            data = self._post(
                f"{c['QWEN_BASE_URL'].rstrip('/')}/api/v1/services/aigc/text-generation/generation",
                headers={"Authorization": f"Bearer {c['QWEN_API_KEY']}", "Content-Type": "application/json"},
                body={"model": c["QWEN_MODEL"], "input": {"messages": [
                    {"role": "system", "content": system}, {"role": "user", "content": instruction}
                ]}, "parameters": {"result_format": "message", "temperature": 0.7}},
            )
            choices = data.get("output", {}).get("choices", [])
            text = _message_text(choices[0].get("message", {}).get("content", "")) if choices else ""
            return {"provider": "千问", "items": _json_object(text).get("prompts", [])}
        if c.get("DOUBAO_API_KEY"):
            data = self._post(
                f"{c['DOUBAO_BASE_URL'].rstrip('/')}/responses",
                headers={"Authorization": f"Bearer {c['DOUBAO_API_KEY']}", "Content-Type": "application/json"},
                body={"model": c["DOUBAO_MODEL"], "input": f"{system}\n\n{instruction}"},
            )
            texts = [item["text"] for item in _walk(data.get("output", []))
                     if item.get("type") in {"output_text", "text"} and isinstance(item.get("text"), str)]
            return {"provider": "豆包", "items": _json_object("\n".join(texts)).get("prompts", [])}
        raise RuntimeError("未配置可用于反推提示词的 DeepSeek、千问或豆包 API")

    def _post(self, url: str, *, headers: dict, body: dict, timeout: int = 180) -> dict:
        response = self.session.post(url, headers=headers, json=body, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _doubao(self, prompt: str) -> Collection:
        c = self.config.values()
        data = self._post(
            f"{c['DOUBAO_BASE_URL'].rstrip('/')}/responses",
            headers={"Authorization": f"Bearer {c['DOUBAO_API_KEY']}", "Content-Type": "application/json"},
            body={"model": c["DOUBAO_MODEL"], "input": prompt,
                  "tools": [{"type": "web_search", "limit": 20}]},
        )
        texts = []
        for item in _walk(data.get("output", [])):
            if item.get("type") in {"output_text", "text"} and isinstance(item.get("text"), str):
                texts.append(item["text"])
        answer = "\n".join(dict.fromkeys(texts)).strip() or str(data.get("output_text", ""))
        return Collection(answer, _sources_from(data),
                          search_queries=_query_records(data, prompt, "volcengine"),
                          source_observations=_trace_items(prompt, answer, data, "volcengine", final_only=True))

    def _qwen(self, prompt: str) -> Collection:
        c = self.config.values()
        data = self._post(
            f"{c['QWEN_BASE_URL'].rstrip('/')}/api/v1/services/aigc/text-generation/generation",
            headers={"Authorization": f"Bearer {c['QWEN_API_KEY']}", "Content-Type": "application/json"},
            body={"model": c["QWEN_MODEL"], "input": {"messages": [{"role": "user", "content": prompt}]},
                  "parameters": {"result_format": "message", "enable_search": True,
                                 "search_options": {"forced_search": True, "enable_source": True,
                                                    "enable_citation": True}}},
        )
        choices = data.get("output", {}).get("choices", [])
        answer = _message_text(choices[0].get("message", {}).get("content", "")) if choices else ""
        search_info = data.get("output", {}).get("search_info", {})
        return Collection(answer, _sources_from(search_info),
                          search_queries=_query_records(search_info, prompt, "dashscope", include_prompt=True),
                          source_observations=_trace_items(prompt, answer, search_info, "dashscope"))

    def _ernie(self, prompt: str) -> Collection:
        c = self.config.values()
        data = self._post(
            f"{c['BAIDU_BASE_URL'].rstrip('/')}/v2/ai_search/chat/completions",
            headers={"Authorization": f"Bearer {c['BAIDU_API_KEY']}", "Content-Type": "application/json"},
            body={"model": c["BAIDU_MODEL"], "messages": [{"role": "user", "content": prompt}],
                  "stream": False, "enable_corner_markers": True, "enable_deep_search": True},
        )
        choices = data.get("choices", [])
        answer = _message_text(choices[0].get("message", {}).get("content", "")) if choices else str(data.get("result", ""))
        references = data.get("references", data)
        return Collection(answer, _sources_from(references),
                          search_queries=_query_records(data, prompt, "baidu_ai_search", include_prompt=True),
                          source_observations=_trace_items(prompt, answer, references, "baidu_ai_search"))

    @staticmethod
    def _tc3_headers(secret_id: str, secret_key: str, service: str, host: str,
                     action: str, version: str, payload: str, timestamp: int | None = None) -> dict:
        timestamp = timestamp or int(time.time())
        date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
        canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\nx-tc-action:{action.lower()}\n"
        signed_headers = "content-type;host;x-tc-action"
        canonical_request = "\n".join([
            "POST", "/", "", canonical_headers, signed_headers,
            hashlib.sha256(payload.encode()).hexdigest(),
        ])
        scope = f"{date}/{service}/tc3_request"
        string_to_sign = "\n".join([
            "TC3-HMAC-SHA256", str(timestamp), scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])
        sign = lambda key, msg: hmac.new(key, msg.encode(), hashlib.sha256).digest()
        secret_date = sign(("TC3" + secret_key).encode(), date)
        secret_service = sign(secret_date, service)
        secret_signing = sign(secret_service, "tc3_request")
        signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {"Authorization": authorization, "Content-Type": "application/json; charset=utf-8",
                "Host": host, "X-TC-Action": action, "X-TC-Version": version,
                "X-TC-Timestamp": str(timestamp)}

    def _tencent_post(self, service: str, host: str, action: str, version: str, body: dict) -> dict:
        c = self.config.values()
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        headers = self._tc3_headers(c["TENCENT_SECRET_ID"], c["TENCENT_SECRET_KEY"], service,
                                    host, action, version, payload)
        response = self.session.post(f"https://{host}", headers=headers, data=payload.encode(), timeout=180)
        response.raise_for_status()
        data = response.json()
        response_data = data.get("Response", data)
        if response_data.get("Error"):
            raise RuntimeError(str(response_data["Error"]))
        return response_data

    def _yuanbao(self, prompt: str) -> Collection:
        c = self.config.values()
        data = self._tencent_post(
            "hunyuan", "hunyuan.tencentcloudapi.com", "ChatCompletions", "2023-09-01",
            {"Model": c["TENCENT_HUNYUAN_MODEL"], "Messages": [{"Role": "user", "Content": prompt}],
             "Stream": False, "EnableEnhancement": True, "ForceSearchEnhancement": True,
             "SearchInfo": True, "Citation": True},
        )
        choices = data.get("Choices", [])
        answer = str(choices[0].get("Message", {}).get("Content", "")) if choices else ""
        search_info = data.get("SearchInfo", data)
        if isinstance(search_info, str):
            try:
                search_info = json.loads(search_info)
            except ValueError:
                pass
        return Collection(answer, _sources_from(search_info),
                          search_queries=_query_records(search_info, prompt, "tencent_hunyuan", include_prompt=True),
                          source_observations=_trace_items(prompt, answer, search_info, "tencent_hunyuan"))

    def _baidu_search(self, prompt: str) -> list[dict]:
        c = self.config.values()
        data = self._post(
            f"{c['BAIDU_BASE_URL'].rstrip('/')}/v2/ai_search/web_search",
            headers={"Authorization": f"Bearer {c['BAIDU_API_KEY']}", "Content-Type": "application/json"},
            body={"messages": [{"role": "user", "content": prompt}], "search_source": "baidu_search_v2",
                  "resource_type_filter": [{"type": "web", "top_k": 20}]},
        )
        return [x for x in data.get("references", []) if isinstance(x, dict)]

    def _tencent_search(self, prompt: str) -> list[dict]:
        data = self._tencent_post(
            "wsa", "wsa.tencentcloudapi.com", "SearchPro", "2025-05-08",
            {"Query": prompt, "Cnt": 20},
        )
        candidates = data.get("Pages") or data.get("Results") or data
        if isinstance(candidates, str):
            try:
                candidates = json.loads(candidates)
            except ValueError:
                candidates = []
        return [x for x in _walk(candidates) if any(k in x for k in ("url", "Url", "URL"))]

    def _deepseek(self, prompt: str) -> Collection:
        c = self.config.values()
        provider = c.get("DEEPSEEK_SEARCH_PROVIDER", "baidu").lower()
        results = self._tencent_search(prompt) if provider == "tencent" else self._baidu_search(prompt)
        sources = _sources_from(results)
        context = []
        for index, result in enumerate(results[:20], 1):
            title = result.get("title") or result.get("Title") or ""
            url = result.get("url") or result.get("Url") or result.get("URL") or ""
            content = result.get("content") or result.get("summary") or result.get("passage") or ""
            context.append(f"[{index}] {title}\nURL: {url}\n{str(content)[:1800]}")
        data = self._post(
            f"{c['DEEPSEEK_BASE_URL'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {c['DEEPSEEK_API_KEY']}", "Content-Type": "application/json"},
            body={"model": c["DEEPSEEK_MODEL"], "messages": [
                {"role": "system", "content": "请仅根据提供的联网搜索资料回答，并用 [数字] 标注引用。不要编造来源。"},
                {"role": "user", "content": f"问题：{prompt}\n\n联网搜索资料：\n" + "\n\n".join(context)},
            ], "stream": False},
        )
        choices = data.get("choices", [])
        answer = _message_text(choices[0].get("message", {}).get("content", "")) if choices else ""
        return Collection(answer, sources,
                          search_queries=_query_records(results, prompt, f"{provider}_search", include_prompt=True),
                          source_observations=_trace_items(
                              prompt, answer, results, f"{provider}_search",
                              selected_ranks=set(range(1, min(20, len(results)) + 1)),
                          ))
