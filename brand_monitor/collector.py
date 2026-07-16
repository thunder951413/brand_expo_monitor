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


def normalize_brand_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def extract_brand_mentions(answer: str, target_name: str, aliases: str | Iterable[str]) -> list[dict]:
    """Extract explicit brand-list evidence without inventing semantic priority."""
    answer = str(answer or "")
    terms = brand_terms(target_name, aliases)
    target_keys = {normalize_brand_key(term) for term in terms}
    output: dict[str, dict] = {}

    def add(raw_name: str, priority_rank=None, confidence=.72, evidence=""):
        name = re.sub(r"\s+", " ", raw_name).strip(" \t\n，,、;；。.:：-—")
        name = re.sub(r"[（(](?:澳大利亚|美国|德国|中国|新西兰|荷兰|品牌|进口|国产)[^）)]*[）)]", "", name).strip()
        name = re.split(r"\s*[：:；;—]\s*", name, maxsplit=1)[0].strip()
        key = normalize_brand_key(name)
        if not key or len(name) < 2 or len(name) > 40:
            return
        if re.search(r"选购|建议|指标|参数|价格|预算|产品线|注意|用途|场景|服务|品牌|较多|进口|国产", name):
            return
        is_target = key in target_keys or any(normalize_brand_key(term) in key or key in normalize_brand_key(term) for term in terms)
        canonical = target_name if is_target else name
        normalized = normalize_brand_key(canonical)
        direct_count = max(1, len(re.findall(re.escape(name), answer, re.I)))
        existing = output.get(normalized)
        item = {
            "brand_name": canonical, "normalized_name": normalized, "mention_count": direct_count,
            "priority_rank": priority_rank, "sentiment": "neutral",
            "recommended": bool(re.search(r"推荐|首选|优先|值得|可关注", evidence)),
            "is_target": is_target, "extraction_method": "rule", "confidence": confidence,
            "evidence": evidence[:500],
        }
        if existing:
            existing["mention_count"] = max(existing["mention_count"], direct_count)
            ranks = [rank for rank in (existing.get("priority_rank"), priority_rank) if rank]
            existing["priority_rank"] = min(ranks) if ranks else None
            existing["is_target"] = existing["is_target"] or is_target
        else:
            output[normalized] = item

    hit, count, target_rank = analyze_answer(answer, terms)
    if hit:
        first_line = next((line.strip() for line in answer.splitlines() if any(term.casefold() in line.casefold() for term in terms)), "")
        add(target_name, target_rank, 1.0, first_line)
        output[normalize_brand_key(target_name)]["mention_count"] = count

    numbered = re.compile(r"(?:^|\n|[；;])\s*(\d{1,2})[.、)]\s*([^\n；;]{2,80})")
    for match in numbered.finditer(answer):
        segment = match.group(2)
        candidate = re.split(r"[：:；;，,。]", segment, maxsplit=1)[0]
        add(candidate, int(match.group(1)), .82, match.group(0).strip())

    for match in re.finditer(r"(?:品牌(?:包括|有|可关注)?|包括|例如|如)[：:]?([^。；;\n]{5,160})", answer):
        parts = re.split(r"[、，,]|以及|和", match.group(1))
        if len(parts) < 2:
            continue
        for index, part in enumerate(parts[:12], 1):
            candidate = re.split(r"(?:等|在|可|适合|不同|各有|主要)", part.strip(), maxsplit=1)[0]
            add(candidate, index, .58, match.group(0)[:500])
    return sorted(output.values(), key=lambda item: (item["priority_rank"] or 999, not item["is_target"], item["brand_name"]))


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
