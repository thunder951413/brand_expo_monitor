from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .collector import Collection, normalize_sources


class LoginRequired(RuntimeError):
    pass


class PageStructureChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class PlatformAdapter:
    input_selectors: tuple[str, ...]
    answer_selectors: tuple[str, ...]
    search_labels: tuple[str, ...] = ("联网搜索", "联网", "搜索")
    strict_answers: bool = False
    source_expand_selectors: tuple[str, ...] = ()
    source_link_selectors: tuple[str, ...] = ()


ADAPTERS = {
    "doubao": PlatformAdapter(
        ("textarea", "[data-testid*='chat_input'] textarea", "[contenteditable='true']"),
        ("[data-plugin-identifier^='block_type:10000']",),
        strict_answers=True,
        source_expand_selectors=("[data-plugin-identifier*='search_query_result_block']",),
        source_link_selectors=("a[data-thinking-box-tool-call='true'][href]",),
    ),
    "qwen": PlatformAdapter(
        ("textarea", "[contenteditable='true']"),
        (".qk-markdown",),
        strict_answers=True,
        source_expand_selectors=("[id^='reference-link-anchor-']",),
        source_link_selectors=("[data-click-extra]",),
    ),
    "ernie": PlatformAdapter(
        ("textarea", "[contenteditable='true']"),
        (".markdown-body", "[class*='answer'] [class*='content']", "[class*='message'] [class*='content']"),
    ),
    "deepseek": PlatformAdapter(
        ("textarea", "#chat-input", "[contenteditable='true']"),
        (".ds-markdown", "[data-message-author-role='assistant']", ".markdown-body"),
        ("联网搜索", "搜索"),
    ),
    "yuanbao": PlatformAdapter(
        ("textarea", "[contenteditable='true']"),
        (".hyc-content-md",),
        strict_answers=True,
    ),
}

GENERIC_INPUTS = ("textarea", "[contenteditable='true']")
GENERIC_ANSWERS = (
    "[data-message-author-role='assistant']",
    "[class*='assistant'] [class*='content']",
    "[class*='message'] .markdown-body",
    "[class*='message'] [class*='content']",
)
LOGIN_WORDS = ("扫码登录", "验证码登录", "手机号登录", "登录后继续", "请先登录")
BLOCK_WORDS = ("验证码", "安全验证", "verify that you're not a robot", "访问过于频繁", "异常流量")
UI_NOISE = {
    "发送", "停止", "停止生成", "重新生成", "复制", "点赞", "点踩", "分享",
    "联网", "联网搜索", "深度思考", "内容由AI生成，仅供参考", "免责声明",
}


def clean_answer_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if any(token in line for token in ("下载豆包", "下载客户端", "下载电脑版", "体验更强大的 AI")):
            lines = lines[:index]
            break
    trailing_questions = 0
    for line in reversed(lines):
        if line.endswith(("？", "?")):
            trailing_questions += 1
        else:
            break
    if trailing_questions >= 2:
        del lines[-trailing_questions:]
    return "\n".join(lines).strip()


def extract_body_delta(before: str, after: str, prompt: str) -> str:
    """Extract newly inserted answer lines when platform CSS selectors drift."""
    before_lines = [x.strip() for x in before.splitlines() if x.strip()]
    after_lines = [x.strip() for x in after.splitlines() if x.strip()]
    matcher = SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    additions: list[str] = []
    for op, _a1, _a2, b1, b2 in matcher.get_opcodes():
        if op not in {"insert", "replace"}:
            continue
        additions.extend(after_lines[b1:b2])
    cleaned: list[str] = []
    for line in additions:
        line = line.strip()
        if not line or line == prompt.strip() or line in UI_NOISE:
            continue
        if line.startswith(prompt.strip()):
            line = line[len(prompt.strip()):].strip()
        if len(line) <= 2 or (cleaned and line == cleaned[-1]):
            continue
        cleaned.append(line)
    text = clean_answer_text("\n".join(cleaned))
    return text if len(text) >= 15 else ""


class WebDriverManager:
    """Visible Chrome sessions with an isolated persistent profile per platform."""

    def __init__(self, profile_root: str | Path, driver_factory: Callable | None = None):
        self.profile_root = Path(profile_root)
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.driver_factory = driver_factory
        self._drivers: dict[str, object] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.RLock()

    def _lock(self, slug: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(slug, threading.RLock())

    def _new_driver(self, slug: str):
        if self.driver_factory:
            return self.driver_factory(slug, self.profile_root / slug)
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ImportError as exc:
            raise RuntimeError("尚未安装 Selenium，请运行 pip install -r requirements.txt") from exc

        profile = (self.profile_root / slug).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        options = Options()
        options.add_argument(f"--user-data-dir={profile}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--lang=zh-CN")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--window-size=1320,900")
        options.page_load_strategy = "eager"
        driver = webdriver.Chrome(service=Service(), options=options)
        driver.set_page_load_timeout(45)
        driver.set_script_timeout(30)
        return driver

    def _alive(self, slug: str):
        driver = self._drivers.get(slug)
        if not driver:
            return None
        try:
            _ = driver.current_url
            return driver
        except Exception:
            self._drivers.pop(slug, None)
            return None

    def driver(self, slug: str):
        with self._lock(slug):
            return self._alive(slug) or self._remember(slug, self._new_driver(slug))

    def _remember(self, slug: str, driver):
        self._drivers[slug] = driver
        return driver

    def open_login(self, platform: dict) -> dict:
        slug = platform["slug"]
        with self._lock(slug):
            driver = self.driver(slug)
            driver.get(platform["url"])
            result = self.status(platform)
            deadline = time.time() + 15
            while result["state"] == "waiting" and time.time() < deadline:
                time.sleep(1)
                result = self.status(platform)
            return result

    @staticmethod
    def _visible_elements(driver, selectors: tuple[str, ...]):
        from selenium.webdriver.common.by import By

        found = []
        seen = set()
        for selector in selectors:
            try:
                for element in driver.find_elements(By.CSS_SELECTOR, selector):
                    if element.id not in seen and element.is_displayed() and element.is_enabled():
                        seen.add(element.id)
                        found.append(element)
            except Exception:
                continue
        return found

    @staticmethod
    def _has_login_button(driver) -> bool:
        try:
            return bool(driver.execute_script(
                """
                return Array.from(document.querySelectorAll('button,[role="button"],a')).some(el => {
                  const text=(el.innerText||el.getAttribute('aria-label')||'').trim();
                  const r=el.getBoundingClientRect();
                  return text === '登录' && r.width>0 && r.height>0;
                });
                """
            ))
        except Exception:
            return False

    @staticmethod
    def _login_gate_visible(driver) -> bool:
        """Detect an actual login/verification gate, not a harmless header login button."""
        try:
            return bool(driver.execute_script(
                """
                const patterns = ['扫码登录','验证码登录','手机号登录','登录后继续','请先登录','安全验证'];
                return Array.from(document.querySelectorAll('body *')).some(el => {
                  if (el.children.length > 8) return false;
                  const text=(el.innerText||el.getAttribute('aria-label')||'').trim();
                  const r=el.getBoundingClientRect();
                  return r.width>0 && r.height>0 && text.length<100 && patterns.some(p => text.includes(p));
                });
                """
            ))
        except Exception:
            return False

    def status(self, platform: dict) -> dict:
        slug = platform["slug"]
        with self._lock(slug):
            driver = self._alive(slug)
            if not driver:
                return {"slug": slug, "state": "closed", "message": "浏览器未启动；历史登录态会保留"}
            try:
                body = driver.execute_script("return document.body ? document.body.innerText.slice(0, 12000) : ''") or ""
                adapter = ADAPTERS.get(slug, PlatformAdapter(GENERIC_INPUTS, GENERIC_ANSWERS))
                has_input = bool(self._visible_elements(driver, adapter.input_selectors + GENERIC_INPUTS))
                has_login = self._has_login_button(driver)
                if has_input and has_login:
                    state, message = "anonymous", "可匿名提问但尚未登录；联网引用可能受限"
                elif has_input:
                    state, message = "ready", "已找到提问输入框，可以自动采集"
                elif any(word.casefold() in body.casefold() for word in BLOCK_WORDS):
                    state, message = "challenge", "页面要求安全验证，请在浏览器窗口人工完成"
                elif has_login or any(word in body for word in LOGIN_WORDS):
                    state, message = "login_required", "请在浏览器窗口完成扫码或账号登录"
                else:
                    state, message = "waiting", "页面已打开，尚未识别到提问输入框"
                return {"slug": slug, "state": state, "message": message, "url": driver.current_url}
            except Exception as exc:
                return {"slug": slug, "state": "error", "message": str(exc)}

    def statuses(self, platforms: list[dict]) -> list[dict]:
        return [self.status(platform) for platform in platforms]

    def close(self, slug: str):
        with self._lock(slug):
            driver = self._drivers.pop(slug, None)
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def close_all(self):
        for slug in list(self._drivers):
            self.close(slug)

    @staticmethod
    def _element_score(element) -> float:
        try:
            rect = element.rect
            return float(rect.get("y", 0)) + float(rect.get("height", 0)) + min(float(rect.get("width", 0)), 1000) / 1000
        except Exception:
            return 0

    def _input(self, driver, adapter: PlatformAdapter):
        elements = self._visible_elements(driver, adapter.input_selectors + GENERIC_INPUTS)
        if not elements:
            body = driver.execute_script("return document.body ? document.body.innerText.slice(0, 12000) : ''") or ""
            if self._has_login_button(driver) or any(word.casefold() in body.casefold() for word in BLOCK_WORDS + LOGIN_WORDS):
                raise LoginRequired("需要在可见浏览器窗口完成登录或安全验证")
            raise PageStructureChanged("未找到提问输入框，平台页面结构可能已变化")
        return max(elements, key=self._element_score)

    def _wait_for_input(self, driver, adapter: PlatformAdapter, timeout: int = 20):
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            try:
                return self._input(driver, adapter)
            except LoginRequired:
                raise
            except PageStructureChanged as exc:
                last_error = exc
                time.sleep(1)
        raise last_error or PageStructureChanged("未找到提问输入框")

    @staticmethod
    def _links(driver) -> set[str]:
        try:
            values = driver.execute_script(
                "return Array.from(document.querySelectorAll('a[href]')).map(a => a.href).filter(Boolean)"
            ) or []
            return {str(x) for x in values if str(x).startswith(("http://", "https://"))}
        except Exception:
            return set()

    @staticmethod
    def _source_items(driver, selectors: tuple[str, ...] = ()) -> list[dict]:
        try:
            selector = ",".join(selectors) if selectors else "a[href], [data-url], [data-href]"
            return driver.execute_script(
                """
                return Array.from(document.querySelectorAll(arguments[0])).map(el => {
                  let metadata={};
                  for (const raw of [el.dataset.clickExtra, el.dataset.exposureExtra]) {
                    if (!raw) continue;
                    try { metadata=JSON.parse(raw); break; } catch (_) {}
                  }
                  return {
                    url: el.href || el.dataset.url || el.dataset.href || metadata.ref_url || metadata.url || '',
                    title: metadata.title || (el.innerText || el.title || el.getAttribute('aria-label') || '').trim()
                  };
                }).filter(x => /^https?:\/\//.test(x.url));
                """,
                selector,
            ) or []
        except Exception:
            return []

    @staticmethod
    def _expand_sources(driver, adapter: PlatformAdapter) -> bool:
        try:
            return bool(driver.execute_script(
                """
                const explicit = arguments[0] || [];
                for (const selector of explicit) {
                  const nodes = Array.from(document.querySelectorAll(selector)).filter(el => {
                    const r=el.getBoundingClientRect();
                    return r.width>0 && r.height>0 && (el.innerText||'').trim();
                  });
                  if (nodes.length) {
                    const node=nodes[nodes.length-1];
                    const sourceLabels=Array.from(node.querySelectorAll('*')).filter(el => {
                      const text=(el.innerText||'').trim(), r=el.getBoundingClientRect();
                      return r.width>0 && r.height>0 && /^\d+\s*篇来源$/.test(text);
                    }).sort((a,b) => {
                      const ar=a.getBoundingClientRect(), br=b.getBoundingClientRect();
                      return ar.width*ar.height-br.width*br.height;
                    });
                    const target=sourceLabels[0] || node.querySelector('.cursor-pointer,[role="button"],button') || node;
                    target.click();
                    return true;
                  }
                }
                const pattern = /(参考.*资料|引用来源|参考来源|信息来源|查看来源)/;
                const nodes = Array.from(document.querySelectorAll('body *')).filter(el => {
                  const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
                  const r = el.getBoundingClientRect();
                  return text.length > 0 && text.length < 100 && pattern.test(text) && r.width > 0 && r.height > 0;
                }).sort((a,b) => {
                  const ar=a.getBoundingClientRect(), br=b.getBoundingClientRect();
                  return ar.width*ar.height - br.width*br.height;
                });
                if (!nodes.length) return false;
                nodes[0].click();
                return true;
                """,
                list(adapter.source_expand_selectors),
            ))
        except Exception:
            return False

    def source_diagnostics(self, platform: dict) -> dict:
        """Return source-related DOM metadata only; used to maintain platform adapters."""
        driver = self._alive(platform["slug"])
        if not driver:
            raise RuntimeError("该平台浏览器未启动")
        return driver.execute_script(
            """
            const visible = el => { const r=el.getBoundingClientRect(); return r.width>0 && r.height>0; };
            const attrs = el => Object.fromEntries(Array.from(el.attributes || []).filter(a => /url|href|source|reference|citation|data-/i.test(a.name)).map(a => [a.name, a.value.slice(0,500)]));
            const refPattern = /(参考.*资料|引用来源|参考来源|信息来源|查看来源|搜索.*关键词)/;
            const refs = Array.from(document.querySelectorAll('body *')).filter(el => {
              const text=(el.innerText||el.getAttribute('aria-label')||'').trim();
              return visible(el) && text.length>0 && text.length<180 && refPattern.test(text);
            }).map(el => ({tag:el.tagName.toLowerCase(), text:(el.innerText||'').trim().slice(0,180), cls:String(el.className||'').slice(0,240), attrs:attrs(el), parent:String(el.parentElement?.outerHTML||'').slice(0,1200)})).slice(-40);
            const links = Array.from(document.querySelectorAll('a[href],[data-url],[data-href],[data-source-url]')).filter(visible).map(el => ({text:(el.innerText||el.title||'').trim().slice(0,180), attrs:attrs(el)})).slice(-100);
            return {url:location.href, refs, links};
            """
        )

    @staticmethod
    def _body_text(driver) -> str:
        try:
            return driver.execute_script("return document.body ? document.body.innerText : ''") or ""
        except Exception:
            return ""

    @staticmethod
    def _enable_search(driver, adapter: PlatformAdapter):
        from selenium.webdriver.common.by import By

        for label in adapter.search_labels:
            xpath = (
                "//*[self::button or @role='button'][contains(normalize-space(.), "
                + repr(label)
                + ")]"
            )
            try:
                for button in driver.find_elements(By.XPATH, xpath):
                    if not button.is_displayed() or not button.is_enabled():
                        continue
                    pressed = (button.get_attribute("aria-pressed") or "").lower()
                    selected = (button.get_attribute("aria-selected") or "").lower()
                    classes = (button.get_attribute("class") or "").lower()
                    if pressed == "true" or selected == "true" or any(x in classes for x in ("active", "selected", "checked")):
                        return True
                    button.click()
                    time.sleep(0.7)
                    return True
            except Exception:
                continue
        return False

    def _answer_candidates(self, driver, adapter: PlatformAdapter, prompt: str):
        selectors = adapter.answer_selectors if adapter.strict_answers else adapter.answer_selectors + GENERIC_ANSWERS
        candidates = self._visible_elements(driver, selectors)
        useful = []
        for element in candidates:
            try:
                text = element.text.strip()
                if len(text) >= 15 and text != prompt.strip():
                    useful.append((element, text))
            except Exception:
                continue
        return useful

    def _latest_answer(
        self,
        driver,
        adapter: PlatformAdapter,
        prompt: str,
        baseline: set[str] | None = None,
    ):
        useful = self._answer_candidates(driver, adapter, prompt)
        if baseline:
            useful = [(element, text) for element, text in useful if text not in baseline]
        return useful[-1] if useful else (None, "")

    @staticmethod
    def _container_links(driver, element) -> list[dict]:
        if element is None:
            return []
        try:
            rows = driver.execute_script(
                """
                let node = arguments[0];
                for (let i=0; i<4 && node; i++, node=node.parentElement) {
                  const links = Array.from(node.querySelectorAll('a[href]'));
                  if (links.length) return links.map(a => ({url:a.href, title:(a.innerText || a.title || '').trim()}));
                }
                return [];
                """,
                element,
            )
            return rows or []
        except Exception:
            return []

    def collect(self, platform: dict, prompt: str, enable_web_search: bool = True) -> Collection:
        slug = platform["slug"]
        adapter = ADAPTERS.get(slug, PlatformAdapter(GENERIC_INPUTS, GENERIC_ANSWERS))
        with self._lock(slug):
            try:
                driver = self.driver(slug)
                driver.get(platform["url"])
                editor = self._wait_for_input(driver, adapter)
                if enable_web_search:
                    self._enable_search(driver, adapter)
                baseline_answers = {
                    text for _element, text in self._answer_candidates(driver, adapter, prompt)
                }
                before_text = self._body_text(driver)
                before_links = self._links(driver)
                before_source_urls = {x.get("url", "") for x in self._source_items(driver)}

                from selenium.webdriver.common.keys import Keys

                editor.click()
                editor.send_keys(Keys.COMMAND, "a")
                editor.send_keys(Keys.BACKSPACE)
                editor.send_keys(prompt)
                editor.send_keys(Keys.ENTER)

                started_at = time.time()
                deadline = started_at + 180
                last_text, stable_since, answer_element = "", None, None
                while time.time() < deadline:
                    time.sleep(2)
                    element, text = self._latest_answer(driver, adapter, prompt, baseline_answers)
                    if not text and not adapter.strict_answers:
                        text = extract_body_delta(before_text, self._body_text(driver), prompt)
                    if self._login_gate_visible(driver) and (
                        not text or any(word in text for word in LOGIN_WORDS)
                    ):
                        raise LoginRequired("匿名提问被平台拦截，请完成登录后重试")
                    if not text and time.time() - started_at >= 45 and self._has_login_button(driver):
                        raise LoginRequired("匿名提问 45 秒内未获得回答，请检查是否需要登录后重试")
                    if text and text == last_text:
                        stable_since = stable_since or time.time()
                        if time.time() - stable_since >= 8:
                            answer_element = element
                            break
                    elif text:
                        last_text, stable_since, answer_element = text, None, element
                    body = driver.execute_script("return document.body ? document.body.innerText.slice(0, 12000) : ''") or ""
                    if any(word.casefold() in body.casefold() for word in BLOCK_WORDS):
                        raise LoginRequired("采集过程中出现验证码或安全验证，请人工处理后重试")

                if not last_text:
                    if self._login_gate_visible(driver) or self._has_login_button(driver):
                        raise LoginRequired("未获得回答，页面提示需要登录或安全验证")
                    raise PageStructureChanged("等待 180 秒后仍未识别到回答，可能是登录失效或页面结构变化")
                if self._expand_sources(driver, adapter):
                    time.sleep(2)
                sources = self._container_links(driver, answer_element)
                sources.extend(self._source_items(driver, adapter.source_link_selectors))
                sources.extend(
                    item for item in self._source_items(driver)
                    if item.get("url") not in before_source_urls
                )
                after_links = self._links(driver)
                for url in sorted(after_links - before_links):
                    host = urlparse(url).netloc.lower()
                    platform_host = urlparse(platform["url"]).netloc.lower()
                    if host and (host != platform_host or urlparse(url).query):
                        sources.append({"url": url, "title": ""})
                last_text = clean_answer_text(last_text)
                normalized = normalize_sources(sources, last_text)
                platform_domain = urlparse(platform["url"]).netloc.lower().removeprefix("www.")
                normalized = [item for item in normalized if item["domain"] != platform_domain]
                return Collection(answer=last_text, sources=normalized)
            except LoginRequired as exc:
                return Collection(answer="", sources=[], status="login_required", error=str(exc))
            except PageStructureChanged as exc:
                return Collection(answer="", sources=[], status="selector_changed", error=str(exc))
            except Exception as exc:
                return Collection(answer="", sources=[], status="error", error=f"WebDriver 采集失败：{exc}")
