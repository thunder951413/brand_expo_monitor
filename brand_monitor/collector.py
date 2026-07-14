from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlparse

URL_RE = re.compile(r"https?://[^\s<>'\"\]\)）]+", re.I)


@dataclass
class Collection:
    answer: str
    sources: list[dict]
    status: str = "success"
    error: str = ""
    method: str = ""
    search_queries: list[dict] = field(default_factory=list)
    source_observations: list[dict] = field(default_factory=list)


def relevance_score(query: str, text: str) -> float:
    """Transparent local lexical relevance score; never presented as a provider score."""
    def tokens(value: str) -> set[str]:
        value = value.casefold()
        latin = set(re.findall(r"[a-z0-9][a-z0-9._-]+", value))
        chinese_runs = re.findall(r"[\u4e00-\u9fff]+", value)
        chinese = {run[i:i + 2] for run in chinese_runs for i in range(max(1, len(run) - 1))}
        return latin | chinese

    left, right = tokens(query), tokens(text)
    if not left or not right:
        return 0.0
    return round(len(left & right) / len(left | right), 4)


def brand_terms(brand_name: str, aliases: str | Iterable[str]) -> list[str]:
    if isinstance(aliases, str):
        aliases = re.split(r"[,，\n]", aliases)
    terms = [brand_name, *aliases]
    cleaned: list[str] = []
    for term in terms:
        term = term.strip()
        if term and term.casefold() not in [x.casefold() for x in cleaned]:
            cleaned.append(term)
    return cleaned


def analyze_answer(answer: str, terms: list[str]) -> tuple[bool, int, int | None]:
    lower = answer.casefold()
    spans = sorted(
        (match.start(), match.end())
        for term in terms if term
        for match in re.finditer(re.escape(term.casefold()), lower)
    )
    if not spans:
        return False, 0, None
    # Full brand names often contain aliases (e.g. 瑞思迈ResMed contains both
    # 瑞思迈 and ResMed). Merge overlapping spans so one occurrence is not
    # counted several times.
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    count = len(merged)
    first = merged[0][0]
    # Approximate list position by counting numbered/bulleted lines before first mention.
    preceding = answer[:first]
    list_items = re.findall(r"(?:^|\n)\s*(?:\d+[.、)]|[-•])\s*", preceding)
    return True, count, max(1, len(list_items))


def normalize_sources(items: Iterable[dict | str], answer: str = "") -> list[dict]:
    raw_items: list[dict | str] = list(items or [])
    known = {item.get("url", "") if isinstance(item, dict) else item for item in raw_items}
    raw_items.extend(url for url in URL_RE.findall(answer) if url not in known)
    output: list[dict] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, str):
            url, title = item.strip(), ""
        else:
            url, title = str(item.get("url", "")).strip(), str(item.get("title", "")).strip()
        url = url.rstrip(".,;:!?，。；：！？、")
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("url", "target", "target_url", "redirect_url", "source_url"):
            candidate = unquote((query.get(key) or [""])[0])
            if candidate.startswith(("http://", "https://")):
                url = candidate
                break
        if not url or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        seen.add(url)
        output.append({"url": url, "domain": parsed.netloc.lower().removeprefix("www."), "title": title})
    return output


DEMO_ANSWERS = {
    "doubao": "家用呼吸机常见品牌包括：\n1. 瑞思迈 ResMed：产品线完善，常见于睡眠呼吸暂停治疗。\n2. 飞利浦伟康。\n3. 费雪派克。\n4. 鱼跃。选购时建议结合医生建议、压力模式和售后服务。",
    "qwen": "常见家用呼吸机品牌有飞利浦伟康、瑞思迈（ResMed）、费雪派克、万曼、鱼跃等。不同型号在算法、湿化、噪音和数据管理方面各有侧重。",
    "ernie": "家用呼吸机品牌较多，进口品牌可关注瑞思迈ResMed、飞利浦伟康、费雪派克；国产品牌包括鱼跃、瑞迈特等。请在专业人员指导下选型。",
    "deepseek": "家用呼吸机可按用途与预算筛选。常见品牌包括：飞利浦伟康、费雪派克、万曼、鱼跃。具体选择需结合睡眠监测结果和医生处方。",
    "yuanbao": "常见家用睡眠呼吸机品牌：1. 瑞思迈（ResMed）；2. 飞利浦伟康；3. 费雪派克；4. 万曼；5. 鱼跃。重点比较治疗模式、压力范围、噪声与售后。",
}

DEMO_SOURCES = {
    "doubao": [
        {"url": "https://www.resmed.com.cn/", "title": "瑞思迈中国"},
        {"url": "https://www.douyin.com/search/%E5%91%BC%E5%90%B8%E6%9C%BA", "title": "呼吸机相关内容"},
    ],
    "qwen": [{"url": "https://baike.baidu.com/item/%E5%91%BC%E5%90%B8%E6%9C%BA", "title": "呼吸机 - 百度百科"}],
    "ernie": [{"url": "https://www.resmed.com/en-us/", "title": "ResMed"}],
    "deepseek": [],
    "yuanbao": [
        {"url": "https://www.resmed.com.cn/healthcare-professional/", "title": "医疗专业人士"},
        {"url": "https://baike.baidu.com/item/%E7%9D%A1%E7%9C%A0%E5%91%BC%E5%90%B8%E6%9A%82%E5%81%9C", "title": "睡眠呼吸暂停"},
    ],
}


def demo_collect(platform_slug: str, prompt: str) -> Collection:
    answer = DEMO_ANSWERS.get(platform_slug, f"关于“{prompt}”暂无演示回答。")
    return Collection(answer=answer, sources=normalize_sources(DEMO_SOURCES.get(platform_slug, [])))


def webhook_collect(webhook_url: str, platform: dict, prompt: str, brand: dict) -> Collection:
    try:
        import requests

        response = requests.post(
            webhook_url,
            json={"platform": platform, "prompt": prompt, "brand": brand},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        answer = str(data.get("answer", ""))
        return Collection(answer=answer, sources=normalize_sources(data.get("sources", []), answer))
    except Exception as exc:  # external integration boundary
        return Collection(answer="", sources=[], status="error", error=str(exc))
