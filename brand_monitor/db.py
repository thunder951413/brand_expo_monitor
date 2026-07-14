from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    brand_name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    owned_domains TEXT NOT NULL DEFAULT '',
    webhook_url TEXT NOT NULL DEFAULT '',
    schedule_enabled INTEGER NOT NULL DEFAULT 0,
    schedule_minutes INTEGER NOT NULL DEFAULT 1440,
    schedule_mode TEXT NOT NULL DEFAULT 'demo',
    ai_brand_analysis_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platforms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    color TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    prompt_id INTEGER,
    prompt_text TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    platform_id INTEGER NOT NULL,
    answer_text TEXT NOT NULL DEFAULT '',
    brand_hit INTEGER NOT NULL DEFAULT 0,
    mention_count INTEGER NOT NULL DEFAULT 0,
    rank_position INTEGER,
    citation_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT NOT NULL DEFAULT '',
    collection_method TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
    FOREIGN KEY(platform_id) REFERENCES platforms(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(result_id) REFERENCES results(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS search_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    query_text TEXT NOT NULL,
    query_rank INTEGER,
    evidence_level TEXT NOT NULL DEFAULT 'observed',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(result_id) REFERENCES results(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    query_text TEXT NOT NULL DEFAULT '',
    query_rank INTEGER,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    search_rank INTEGER,
    provider_score REAL,
    local_relevance REAL,
    retrieved INTEGER NOT NULL DEFAULT 0,
    selected INTEGER NOT NULL DEFAULT 0,
    cited INTEGER NOT NULL DEFAULT 0,
    citation_rank INTEGER,
    snippet TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    evidence_level TEXT NOT NULL DEFAULT 'observed',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(result_id) REFERENCES results(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS brand_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER NOT NULL,
    brand_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 1,
    priority_rank INTEGER,
    sentiment TEXT NOT NULL DEFAULT 'neutral',
    recommended INTEGER NOT NULL DEFAULT 0,
    is_target INTEGER NOT NULL DEFAULT 0,
    extraction_method TEXT NOT NULL DEFAULT 'rule',
    confidence REAL NOT NULL DEFAULT 0,
    evidence TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(result_id) REFERENCES results(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_results_run ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_results_platform ON results(platform_id);
CREATE INDEX IF NOT EXISTS idx_sources_result ON sources(result_id);
CREATE INDEX IF NOT EXISTS idx_search_queries_result ON search_queries(result_id);
CREATE INDEX IF NOT EXISTS idx_observations_result ON source_observations(result_id);
CREATE INDEX IF NOT EXISTS idx_observations_domain ON source_observations(domain);
CREATE INDEX IF NOT EXISTS idx_brand_mentions_result ON brand_mentions(result_id);
CREATE INDEX IF NOT EXISTS idx_brand_mentions_name ON brand_mentions(normalized_name);
"""


PLATFORMS = [
    ("doubao", "豆包", "https://www.doubao.com/chat/", "#5B6CF9"),
    ("qwen", "千问", "https://www.qianwen.com/", "#7559E8"),
    ("ernie", "文心一言", "https://yiyan.baidu.com/", "#3478F6"),
    ("deepseek", "DeepSeek", "https://chat.deepseek.com/", "#4D6BFE"),
    ("yuanbao", "元宝", "https://yuanbao.tencent.com/", "#16A07A"),
]

PROMPTS = [
    "家用呼吸机品牌都有哪些？",
    "家用呼吸机品牌推荐",
    "进口家用呼吸机品牌排名",
    "睡眠呼吸机十大品牌对比与选购建议",
]


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def initialize(path: str | Path, now: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        result_columns = {row[1] for row in conn.execute("PRAGMA table_info(results)")}
        if "collection_method" not in result_columns:
            conn.execute("ALTER TABLE results ADD COLUMN collection_method TEXT NOT NULL DEFAULT ''")
        settings_columns = {row[1] for row in conn.execute("PRAGMA table_info(settings)")}
        if "owned_domains" not in settings_columns:
            conn.execute("ALTER TABLE settings ADD COLUMN owned_domains TEXT NOT NULL DEFAULT ''")
        if "ai_brand_analysis_enabled" not in settings_columns:
            conn.execute("ALTER TABLE settings ADD COLUMN ai_brand_analysis_enabled INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            """INSERT OR IGNORE INTO settings
               (id, brand_name, aliases, webhook_url, schedule_enabled,
                schedule_minutes, schedule_mode, updated_at)
               VALUES (1, ?, ?, '', 0, 1440, 'demo', ?)""",
            ("瑞思迈ResMed", "瑞思迈,ResMed,resmed", now),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO prompts (text, active, created_at) VALUES (?, 1, ?)",
            [(prompt, now) for prompt in PROMPTS],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO platforms (slug, name, url, active, color) VALUES (?, ?, ?, 1, ?)",
            PLATFORMS,
        )
