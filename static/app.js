const state = { config: null, browsers: [], apis: [], apiConfig: null, dashboard: null, reverse: null, view: 'dashboard', days: 30,
  ai: { messages: [], initialized: false, loading: false, context: null }, brandPrompt: '' };
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHTML = (value = '') => String(value).replace(/[&<>'"]/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
}[char]));

const modeNames = { auto: '自动', api: '官方 API', demo: '演示', webhook: 'Webhook', webdriver: 'WebDriver', manual: '人工' };
const statusNames = {
  success: '成功', login_required: '需登录', selector_changed: '页面变化',
  api_unconfigured: 'API 未配置', api_error: 'API 失败', error: '失败'
};
const browserStateNames = {
  closed: '未启动', ready: '可采集', anonymous: '匿名可用', login_required: '需登录',
  challenge: '需验证', waiting: '等待中', error: '异常'
};

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const payload = await response.json();
  if (!response.ok || !payload.ok) throw new Error(payload.error || '请求失败');
  return payload;
}

function toast(message, isError = false) {
  const element = $('#toast');
  element.textContent = message;
  element.className = `toast show${isError ? ' error' : ''}`;
  clearTimeout(element.timer);
  element.timer = setTimeout(() => { element.className = 'toast'; }, 2800);
}

function formatTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function switchView(view) {
  state.view = view;
  $$('.view').forEach(element => element.classList.remove('active'));
  $(`#${view}-view`).classList.add('active');
  $$('.nav-item').forEach(element => element.classList.toggle('active', element.dataset.view === view));
  $$('.workflow-step').forEach(element => element.classList.toggle('active', element.dataset.go === view));
  const names = {
    dashboard: ['AI 品牌可见性', '概览', '判断品牌是否被 AI 看见，以及最值得优先处理的问题。'],
    ai: ['AI 数据交流', '研究助理', '基于当前监测数据评估曝光表现、解释问题并讨论下一步策略。'],
    records: ['回答与引用证据', '证据记录', '从汇总指标下钻到每次回答、来源网址和采集失败原因。'],
    relevance: ['网站与信源研究', '信源分析', '解释网站如何从搜索召回进入选材，并最终成为回答引用。'],
    prompts: ['提示词实验', '问题策略', '设计覆盖真实用户意图的问题，并用跨平台实验验证曝光规律。'],
    config: ['采集与监测配置', '系统设置', '保证品牌识别、采集通道、登录状态和定时任务持续可用。']
  };
  $('#page-title').textContent = names[view][0];
  $('#breadcrumb-current').textContent = names[view][1];
  $('#page-purpose').textContent = names[view][2];
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (view === 'ai') ensureAiEvaluation().catch(error => showAiSetup(error.message));
}

function promptIntent(text = '') {
  if (/排行|排名|十大|榜/.test(text)) return '排行';
  if (/对比|区别|优缺点|还是/.test(text)) return '对比';
  if (/推荐|值得|靠谱/.test(text)) return '推荐';
  if (/预算|价格|多少钱|成本/.test(text)) return '预算';
  if (/怎么选|如何选|选购|指标/.test(text)) return '选购';
  if (/人群|患者|老人|儿童|睡眠/.test(text)) return '场景';
  return '发现';
}

async function loadConfig() {
  const response = await api('/api/config');
  state.config = response.data;
  state.browsers = response.browsers || [];
  state.apis = response.apis || [];
  state.apiConfig = response.api_config || { models: {}, secrets: {}, deepseek_search_provider: 'baidu' };
  const { settings, prompts, platforms } = state.config;
  $('#brand-pill').textContent = settings.brand_name;
  $('#ai-context-brand').textContent = settings.brand_name;
  $('#scope-summary').textContent = `${platforms.filter(item => item.active).length} 个平台 · ${prompts.filter(item => item.active).length} 个提示词`;
  $('#brand-name').value = settings.brand_name;
  $('#aliases').value = settings.aliases;
  $('#owned-domains').value = settings.owned_domains || '';
  $('#webhook-url').value = settings.webhook_url;
  $('#schedule-enabled').checked = settings.schedule_enabled;
  $('#schedule-minutes').value = settings.schedule_minutes;
  $('#schedule-mode').value = settings.schedule_mode;
  $('#ai-brand-analysis-enabled').checked = settings.ai_brand_analysis_enabled !== false;
  $('#next-run').textContent = response.scheduler.next_run ? `下次 ${formatTime(response.scheduler.next_run)}` : '定时任务未启用';
  const reverseGoal = $('#reverse-goal');
  if (reverseGoal && !reverseGoal.value) reverseGoal.value = `让目标用户在品牌推荐、排行和产品对比中看到${settings.brand_name}`;
  renderConfig();
  renderApiConfig();
  renderReadiness(response.scheduler || {});
}

function browserStateName(value) { return browserStateNames[value] || value; }

function renderConfig() {
  const { platforms, prompts } = state.config;
  $('#platform-list').innerHTML = platforms.map(platform => {
    const browser = state.browsers.find(item => item.slug === platform.slug) || { state: 'closed', message: '浏览器未启动' };
    const apiState = state.apis.find(item => item.slug === platform.slug) || { configured: false, message: 'API 未配置' };
    return `<div class="platform-item">
      <div class="platform-avatar" style="background:${platform.color}">${escapeHTML(platform.name.slice(0, 1))}</div>
      <div class="platform-meta"><b>${escapeHTML(platform.name)} <em class="api-state ${apiState.configured ? 'ready' : 'missing'}">${apiState.configured ? 'API 可用' : 'API 未配置'}</em> <em class="browser-state ${escapeHTML(browser.state)}">${browserStateName(browser.state)}</em></b><small title="${escapeHTML(apiState.message)} · ${escapeHTML(browser.message)}">${escapeHTML(apiState.message)} · WebDriver：${escapeHTML(browser.message)}</small></div>
      <button class="open-link" data-login="${platform.id}">登录/检测</button>
      <a class="open-link" href="${escapeHTML(platform.url)}" target="_blank" rel="noopener">普通打开 ↗</a>
      <label class="switch"><input type="checkbox" data-toggle="platforms" data-id="${platform.id}" ${platform.active ? 'checked' : ''}><span></span></label>
    </div>`;
  }).join('');
  $('#prompt-list').innerHTML = prompts.map(prompt => `<div class="prompt-item">
    <span class="intent-tag">${promptIntent(prompt.text)}</span><span class="prompt-text">${escapeHTML(prompt.text)}</span>
    <label class="switch"><input type="checkbox" data-toggle="prompts" data-id="${prompt.id}" ${prompt.active ? 'checked' : ''}><span></span></label>
    <button class="delete-btn" data-delete="${prompt.id}" title="删除">×</button>
  </div>`).join('');
  const activeCount = $('#active-prompt-count');
  if (activeCount) activeCount.textContent = prompts.filter(prompt => prompt.active).length;
  renderPromptPortfolio();
  $('#platform-filter').innerHTML = '<option value="">全部平台</option>' + platforms.map(platform => `<option value="${escapeHTML(platform.slug)}">${escapeHTML(platform.name)}</option>`).join('');
}

function renderReadiness(scheduler = {}) {
  if (!state.config) return;
  const { settings, prompts, platforms } = state.config;
  const enabledPlatforms = platforms.filter(item => item.active);
  const collectionReady = enabledPlatforms.filter(platform => {
    const apiState = state.apis.find(item => item.slug === platform.slug);
    const browser = state.browsers.find(item => item.slug === platform.slug);
    return apiState?.configured || ['ready', 'anonymous'].includes(browser?.state);
  }).length;
  const checks = [
    { label: '品牌识别', ok: Boolean(settings.brand_name && settings.alias_list?.length), detail: settings.brand_name || '未设置品牌' },
    { label: '问题组合', ok: prompts.some(item => item.active), detail: `${prompts.filter(item => item.active).length} 个启用提示词` },
    { label: '目标平台', ok: enabledPlatforms.length > 0, detail: `${enabledPlatforms.length} 个启用平台` },
    { label: '采集通道', ok: collectionReady > 0, detail: `${collectionReady} / ${enabledPlatforms.length} 个平台已就绪` },
    { label: '持续监测', ok: settings.schedule_enabled, detail: settings.schedule_enabled ? `每 ${settings.schedule_minutes} 分钟` : '定时任务未启用' }
  ];
  const score = Math.round(checks.filter(item => item.ok).length * 100 / checks.length);
  $('#readiness-score').textContent = `${score}%`;
  $('#readiness-score').className = score >= 80 ? 'ready' : score >= 60 ? 'partial' : 'missing';
  $('#readiness-steps').innerHTML = checks.map((item, index) => `<div class="readiness-step ${item.ok ? 'ready' : 'missing'}"><span>${item.ok ? '✓' : index + 1}</span><div><b>${item.label}</b><small>${escapeHTML(item.detail)}</small></div></div>`).join('');
}

function renderPromptPortfolio() {
  const element = $('#prompt-portfolio');
  if (!element || !state.config) return;
  const prompts = state.config.prompts;
  const active = prompts.filter(item => item.active);
  const intents = [...new Set(active.map(item => promptIntent(item.text)))];
  const tested = new Set((state.dashboard?.prompts || []).filter(item => item.total).map(item => item.prompt_text));
  const weak = (state.dashboard?.prompts || []).filter(item => item.total && item.hit_rate < 50).length;
  element.innerHTML = [
    ['启用问题', active.length, `共 ${prompts.length} 个问题`],
    ['意图覆盖', intents.length, intents.join('、') || '尚未分类'],
    ['已有样本', active.filter(item => tested.has(item.text)).length, '至少完成过一次采集'],
    ['曝光缺口', weak, weak ? '优先扩展相近问法' : '当前没有低命中问题']
  ].map((item, index) => `<article class="portfolio-card"><span>0${index + 1}</span><div><strong>${item[1]}</strong><b>${item[0]}</b><small>${escapeHTML(item[2])}</small></div></article>`).join('');
}

function renderReverseSuggestions(data) {
  state.reverse = data;
  $('#reverse-provider').textContent = `${data.provider} · ${data.history_runs} 次历史样本`;
  $('#reverse-summary').innerHTML = `<b>识别品类：${escapeHTML(data.subject)}</b><span>目标：${escapeHTML(data.goal)}</span>${data.ai_error ? `<small>模型未启用：${escapeHTML(data.ai_error)}，已使用数据规则。</small>` : ''}`;
  const existing = new Set(state.config.prompts.map(item => item.text.toLocaleLowerCase()));
  const suggestions = data.suggestions || [];
  const element = $('#reverse-suggestions');
  element.className = suggestions.length ? 'reverse-suggestions' : 'reverse-suggestions empty-state';
  element.innerHTML = suggestions.map((item, index) => {
    const added = existing.has(item.text.toLocaleLowerCase());
    return `<article class="reverse-suggestion">
      <div class="suggestion-rank">${index + 1}</div>
      <div class="suggestion-main"><div><span>${escapeHTML(item.intent)}</span><strong>${escapeHTML(item.text)}</strong></div><p>${escapeHTML(item.reason)}</p><small>${escapeHTML(item.evidence)}</small></div>
      <div class="suggestion-score"><strong>${item.predicted_exposure}</strong><span>预测曝光</span><small>${escapeHTML(item.confidence)}置信度</small></div>
      <button class="button ghost" data-add-suggestion="${escapeHTML(item.text)}" ${added ? 'disabled' : ''}>${added ? '已加入' : '加入列表'}</button>
    </article>`;
  }).join('') || '没有生成新的提示词；可调整最终目标后重试。';
}

function renderApiConfig() {
  const config = state.apiConfig || { models: {}, secrets: {}, deepseek_search_provider: 'baidu' };
  const models = config.models || {};
  const modelValues = {
    'api-doubao-model': models.DOUBAO_MODEL, 'api-qwen-model': models.QWEN_MODEL,
    'api-baidu-model': models.BAIDU_MODEL, 'api-tencent-model': models.TENCENT_HUNYUAN_MODEL,
    'api-deepseek-model': models.DEEPSEEK_MODEL, 'api-openai-model': models.OPENAI_MODEL
  };
  Object.entries(modelValues).forEach(([id, value]) => { const element = $(`#${id}`); if (element) element.value = value || ''; });
  const provider = $('#api-deepseek-provider'); if (provider) provider.value = config.deepseek_search_provider || 'baidu';
  const secretFields = {
    'api-doubao-key': 'DOUBAO_API_KEY', 'api-qwen-key': 'QWEN_API_KEY', 'api-baidu-key': 'BAIDU_API_KEY',
    'api-tencent-id': 'TENCENT_SECRET_ID', 'api-tencent-secret': 'TENCENT_SECRET_KEY', 'api-deepseek-key': 'DEEPSEEK_API_KEY',
    'api-openai-key': 'OPENAI_API_KEY'
  };
  Object.entries(secretFields).forEach(([id, key]) => {
    const element = $(`#${id}`); if (!element) return;
    element.value = ''; element.placeholder = config.secrets?.[key] ? '已保存；留空保持不变' : '尚未配置';
  });
  const statusList = $('#api-status-list');
  if (statusList) statusList.innerHTML = state.apis.map(item => `<span class="api-summary ${item.configured ? 'ready' : 'missing'}"><b>${escapeHTML(item.name)}</b>${escapeHTML(item.configured ? item.model : item.message)}</span>`).join('');
  const openaiReady = Boolean(config.secrets?.OPENAI_API_KEY);
  $('#openai-config-status').className = `api-summary ${openaiReady ? 'ready' : 'missing'}`;
  $('#openai-config-status').textContent = openaiReady ? `已配置 · ${models.OPENAI_MODEL}` : '尚未配置';
  $('#ai-context-model').textContent = openaiReady ? models.OPENAI_MODEL : '未配置';
}

function aiRangeLabel() { return state.days ? `近 ${state.days} 天` : '全部历史'; }

function formatAiText(content = '') {
  const lines = escapeHTML(content).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').split('\n');
  return lines.map(line => {
    const heading = line.match(/^#{1,3}\s+(.+)/);
    if (heading) return `<h3>${heading[1]}</h3>`;
    const bullet = line.match(/^[-*]\s+(.+)/);
    if (bullet) return `<p class="ai-bullet">${bullet[1]}</p>`;
    const ordered = line.match(/^(\d+)[.、]\s*(.+)/);
    if (ordered) return `<p class="ai-bullet"><b>${ordered[1]}.</b> ${ordered[2]}</p>`;
    return line ? `<p>${line}</p>` : '<br>';
  }).join('');
}

function renderAiMessages() {
  const element = $('#ai-messages');
  const visible = state.ai.messages.filter(message => !message.hidden);
  if (!visible.length) return;
  element.innerHTML = visible.map(message => `<div class="ai-message ${message.role}">
    <span>${message.role === 'assistant' ? 'AI' : '你'}</span><div>${formatAiText(message.content)}</div>
  </div>`).join('') + (state.ai.loading ? '<div class="ai-message assistant thinking"><span>AI</span><div><i></i><i></i><i></i> 正在结合最新监测数据分析…</div></div>' : '');
  element.scrollTop = element.scrollHeight;
}

function showAiSetup(message = '请先配置 OpenAI API Key') {
  state.ai.loading = false;
  $('#ai-chat-status').textContent = '需要完成 OpenAI 配置';
  $('#ai-messages').innerHTML = `<div class="ai-empty"><span>◇</span><b>${escapeHTML(message)}</b><p>完成配置后返回本页，系统会自动生成当前曝光评估。</p><button class="button primary" data-go="config">前往采集配置</button></div>`;
}

async function loadAiContext() {
  const response = await api(`/api/ai/context?days=${state.days}`);
  state.ai.context = response.data;
  $('#ai-context-range').textContent = aiRangeLabel();
  $('#ai-context-loaded').textContent = `${response.data.results} 条回答 · ${response.data.sources} 个信源`;
  return response.data;
}

async function sendAiMessage(content, isDefault = false) {
  if (state.ai.loading) return;
  if (!state.apiConfig?.secrets?.OPENAI_API_KEY) return showAiSetup();
  if (content) state.ai.messages.push({ role: 'user', content, hidden: isDefault });
  state.ai.loading = true;
  $('#ai-chat-status').textContent = isDefault ? '正在生成默认评估' : '正在分析';
  renderAiMessages();
  try {
    await loadAiContext();
    const response = await api('/api/ai/chat', { method: 'POST', body: JSON.stringify({ messages: state.ai.messages, days: state.days }) });
    state.ai.messages.push({ role: 'assistant', content: response.data.answer });
    state.ai.initialized = true;
    $('#ai-context-model').textContent = response.data.model;
    $('#ai-chat-status').textContent = `基于 ${response.data.context_meta.results} 条回答 · ${response.data.model}`;
  } finally {
    state.ai.loading = false;
    renderAiMessages();
  }
}

async function waitForAiIdle() {
  while (state.ai.loading) await new Promise(resolve => setTimeout(resolve, 100));
}

async function ensureAiEvaluation(force = false) {
  await loadAiContext();
  if (!state.apiConfig?.secrets?.OPENAI_API_KEY) return showAiSetup();
  if (state.ai.initialized && !force) return renderAiMessages();
  if (force) state.ai.messages = [];
  return sendAiMessage('请对当前监测数据做默认评估：先给出曝光结论和数据可信度，再指出最重要的问题，解释可能原因，并给出按优先级排序、可以继续验证的提升行动。', true);
}

async function loadDashboard() {
  const response = await api(`/api/dashboard?days=${state.days}`);
  state.dashboard = response.data;
  renderDashboard();
}

function renderDashboard() {
  const dashboard = state.dashboard;
  const totals = dashboard.totals;
  $('#metric-rate').textContent = `${totals.hit_rate}%`;
  $('#metric-hit').textContent = `${totals.hits} / ${totals.total} 次命中`;
  $('#metric-rate-bar').style.width = `${totals.hit_rate}%`;
  $('#metric-rank').textContent = totals.avg_rank ? `#${totals.avg_rank}` : '—';
  $('#metric-citation-rate').textContent = `${totals.citation_rate}%`;
  $('#metric-citations').textContent = `${totals.citations} 条引用`;
  $('#metric-domains').textContent = totals.unique_sources;
  $('#top-source-note').textContent = dashboard.sources.length ? `最常引用 ${dashboard.sources[0].domain}` : '暂无来源数据';
  $('#metric-success').textContent = `${totals.success_rate}%`;
  $('#metric-collected').textContent = `${totals.collected} 次采集`;
  $('#metric-errors').textContent = totals.errors ? `${totals.errors} 次需要处理` : '没有异常任务';
  $('#updated-at').textContent = totals.last_capture ? `更新于 ${formatTime(totals.last_capture)}` : '尚未采集';
  $('#scope-freshness').textContent = totals.last_capture ? `最近采集 ${formatTime(totals.last_capture)}` : '尚未采集';
  renderDecisionSummary(dashboard);
  renderTrend(dashboard.trend);
  renderHealth(dashboard.platforms, totals);
  renderPlatformChart(dashboard.platforms);
  renderSourceChart(dashboard.sources);
  renderPromptPerformance(dashboard.prompts);
  renderBrandLandscape(dashboard.brand_landscape || {});
  renderRetrieval(dashboard.retrieval || {});
  renderTables();
  renderPromptPortfolio();
}

function renderBrandLandscape(landscape) {
  const overall = landscape.overall || [];
  const promptGroups = landscape.by_prompt || [];
  const select = $('#brand-prompt-filter');
  select.innerHTML = '<option value="">全部提示词</option>' + promptGroups.map(group => `<option value="${escapeHTML(group.prompt_text)}">${escapeHTML(group.prompt_text)}</option>`).join('');
  select.value = promptGroups.some(group => group.prompt_text === state.brandPrompt) ? state.brandPrompt : '';
  state.brandPrompt = select.value;
  const group = state.brandPrompt ? promptGroups.find(item => item.prompt_text === state.brandPrompt) : null;
  const brands = group ? group.brands : overall;
  const target = brands.find(item => item.is_target);
  const leadingOther = brands.find(item => !item.is_target);
  $('#brand-analysis-method').textContent = landscape.method_note || '规则 / AI 混合识别';
  $('#brand-landscape-summary').innerHTML = `<div class="landscape-hero target"><span>目标品牌</span><b>${escapeHTML(target?.brand_name || state.config.settings.brand_name)}</b><strong>${target ? `${target.answer_coverage}%` : '暂无数据'}</strong><small>${target ? `竞争位次 #${target.competitive_rank} · 平均优先 #${target.avg_priority || '—'}` : '完成新一轮采集后生成'}</small></div>
    <div class="landscape-hero competitor"><span>领先其他品牌</span><b>${escapeHTML(leadingOther?.brand_name || '尚未识别')}</b><strong>${leadingOther ? `${leadingOther.answer_coverage}%` : '—'}</strong><small>${leadingOther ? `竞争位次 #${leadingOther.competitive_rank} · 综合曝光 ${leadingOther.exposure_score}` : '回答中暂无其他品牌数据'}</small></div>
    <div class="landscape-gap"><span>目标与领先品牌差距</span><b>${target && leadingOther ? `${Math.round((target.exposure_score - leadingOther.exposure_score) * 10) / 10}` : '—'}</b><small>综合曝光分差；正值代表目标品牌领先</small></div>`;
  $('#brand-landscape-body').innerHTML = brands.map(item => `<tr class="${item.is_target ? 'target-brand-row' : ''}">
    <td><b>#${item.competitive_rank}</b></td><td><span class="brand-name-cell">${escapeHTML(item.brand_name)}${item.is_target ? '<em>目标</em>' : ''}</span></td>
    <td><div class="metric-cell"><b>${item.answer_coverage}%</b><i><span style="width:${item.answer_coverage}%"></span></i><small>${item.result_mentions} 条回答</small></div></td>
    <td>${item.avg_priority ? `#${item.avg_priority}` : '—'}</td><td>${item.top1_rate}%</td><td>${item.recommendation_rate}%</td>
    <td><strong class="exposure-score">${item.exposure_score}</strong></td><td><span class="extraction-pill ${item.ai_coverage ? 'ai' : 'rule'}">${item.ai_coverage ? `AI ${item.ai_coverage}%` : '规则'}</span></td></tr>`).join('') || '<tr><td colspan="8">暂无品牌竞争数据；运行新一轮监测后开始统计。</td></tr>';
}

function renderDecisionSummary(dashboard) {
  const totals = dashboard.totals;
  const weakPrompt = [...dashboard.prompts].filter(item => item.total).sort((a, b) => a.hit_rate - b.hit_rate)[0];
  const weakPlatform = [...dashboard.platforms].filter(item => item.total || item.errors).sort((a, b) => a.hit_rate - b.hit_rate)[0];
  const actions = [];
  if (!totals.collected) actions.push(['建立基线', '先运行全部启用提示词，形成第一轮跨平台曝光基线。', 'prompts']);
  if (totals.errors) actions.push(['修复采集', `${totals.errors} 次采集异常会降低结论可信度，先检查 API 或登录状态。`, 'config']);
  if (weakPrompt && weakPrompt.hit_rate < 70) actions.push(['补足问题缺口', `“${weakPrompt.prompt_text}”可见度仅 ${weakPrompt.hit_rate}%，应扩展同意图问法并检查竞争信源。`, 'prompts']);
  if (weakPlatform && weakPlatform.hit_rate < 70) actions.push(['聚焦平台', `${weakPlatform.name} 当前可见度 ${weakPlatform.hit_rate}%，适合单独核查回答与来源。`, 'records']);
  if (totals.total && totals.citation_rate < 60) actions.push(['提升引用', `引用覆盖率 ${totals.citation_rate}%，需要查看高召回未引用网站和内容证据缺口。`, 'relevance']);
  if (!actions.length) actions.push(['保持验证', '当前曝光与采集较稳定，继续按固定提示词重复采样，验证规律能否持续。', 'relevance']);
  const displayed = actions.slice(0, 3);
  $('#decision-level').textContent = totals.errors || totals.hit_rate < 50 ? '高优先级' : totals.hit_rate < 80 ? '需要优化' : '保持观察';
  $('#decision-level').className = `decision-badge ${totals.errors || totals.hit_rate < 50 ? 'high' : totals.hit_rate < 80 ? 'medium' : 'good'}`;
  $('#decision-list').className = 'decision-list';
  $('#decision-list').innerHTML = displayed.map((item, index) => `<button data-go="${item[2]}"><span>${index + 1}</span><div><b>${escapeHTML(item[0])}</b><p>${escapeHTML(item[1])}</p></div><i>→</i></button>`).join('');

  const repeated = (dashboard.retrieval?.variability || []).filter(item => item.runs >= 3).length;
  const sampleScore = Math.min(100, totals.total * 5);
  const repeatScore = Math.min(100, repeated * 20);
  const confidence = Math.round(totals.success_rate * .45 + sampleScore * .35 + repeatScore * .2);
  const confidenceLabel = confidence >= 80 ? '较可靠' : confidence >= 55 ? '可参考' : '探索性';
  $('#confidence-summary').innerHTML = `<div class="confidence-score" style="--confidence:${confidence}"><strong>${confidence}</strong><span>${confidenceLabel}</span></div>
    <div class="confidence-factors"><span><b>${totals.total}</b>有效回答<small>${sampleScore >= 80 ? '样本较充分' : '建议继续积累'}</small></span><span><b>${totals.success_rate}%</b>采集成功<small>${totals.errors ? '含异常任务' : '链路正常'}</small></span><span><b>${repeated}</b>重复基线<small>${repeated ? '可判断波动' : '尚不能判断随机性'}</small></span></div>`;
}

function renderRetrieval(retrieval) {
  const funnel = retrieval.funnel || {};
  const element = $('#retrieval-funnel');
  if (!funnel.retrieved && !funnel.selected && !funnel.cited) {
    element.className = 'retrieval-funnel empty-state';
    element.textContent = '运行 API 采集后显示检索阶段';
  } else {
    element.className = 'retrieval-funnel';
    element.innerHTML = [
      ['召回资料', funnel.retrieved || 0, funnel.owned_retrieved || 0, 'retrieved'],
      ['进入选材', funnel.selected || 0, funnel.owned_selected || 0, 'selected'],
      ['最终引用', funnel.cited || 0, funnel.owned_cited || 0, 'cited']
    ].map((item, index) => `<div class="funnel-stage ${item[3]}"><small>${item[0]}</small><strong>${item[1]}</strong><span>自有域名 ${item[2]}</span>${index < 2 ? '<i>→</i>' : ''}</div>`).join('');
  }
  const queries = retrieval.queries || [];
  $('#retrieval-queries').innerHTML = queries.slice(0, 8).map(item => `<span title="${escapeHTML(item.provider)}">${escapeHTML(item.query_text)} <b>×${item.count}</b></span>`).join('') || '<small>平台未返回实际搜索词</small>';
  const stages = { retrieved: '召回', selected: '选材', cited: '引用' };
  const stageRows = retrieval.stage_sources || [];
  $('#stage-sources').className = stageRows.length ? 'stage-sources' : 'stage-sources empty-state';
  $('#stage-sources').innerHTML = stageRows.length ? Object.entries(stages).map(([key, name]) => {
    const rows = stageRows.filter(row => row.stage === key).slice(0, 6);
    return `<div><b>${name}</b>${rows.map(row => `<span><em>${escapeHTML(row.domain)}</em><strong>${row.count}</strong><small>相关度 ${Math.round((row.avg_relevance || 0) * 100)}%</small></span>`).join('') || '<small>无可观测数据</small>'}</div>`;
  }).join('') : '尚无检索轨迹';
  const opportunities = retrieval.opportunities || [];
  $('#strategy-insights').innerHTML = (retrieval.insights || []).map(item => `<div class="strategy-insight ${escapeHTML(item.level)}"><b>${escapeHTML(item.title)}</b><span>${escapeHTML(item.detail)}</span></div>`).join('');
  $('#retrieval-opportunities-body').innerHTML = opportunities.map(row => {
    const verdict = row.citation_rate >= 50 ? ['高引用信源', 'good'] : row.selected ? ['选材后流失', 'medium'] : ['召回未选用', 'high'];
    return `<tr><td>${escapeHTML(row.domain)}</td><td>${row.retrieved}</td><td>${row.selected}</td><td>${row.cited}</td><td>${row.citation_rate}%</td><td>${Math.round((row.avg_relevance || 0) * 100)}%</td><td><span class="opportunity-pill ${verdict[1]}">${verdict[0]}</span></td></tr>`;
  }).join('') || '<tr><td colspan="7">配置 API 并运行采集后生成策略线索</td></tr>';

  const strategies = retrieval.platform_strategies || [];
  $('#platform-strategies').innerHTML = strategies.map(item => `<div class="platform-strategy">
    <div><span class="platform-dot" style="background:${escapeHTML(item.color)}"></span><b>${escapeHTML(item.platform_name)}</b><strong>${item.overall_pattern !== 'insufficient' ? `${item.overall_stability}% 整体稳定` : '待采样'}</strong></div>
    <p>${escapeHTML(item.diagnosis)}</p><span>${escapeHTML(item.action)}</span>
    <small>提示词敏感度：${item.prompt_effect == null ? '待积累' : `${item.prompt_effect}% · ${escapeHTML(item.prompt_effect_label)}`}</small>
    ${(item.cross_prompt_core || []).length ? `<small>跨提示词稳定信源：${item.cross_prompt_core.map(source => `${escapeHTML(source.domain)} ${source.rate}%`).join('、')}</small>` : ''}
    ${item.preferred_domains.length ? `<small>偏好信源：${item.preferred_domains.map(escapeHTML).join('、')}</small>` : ''}
  </div>`).join('') || '<div class="empty-state">完成多轮品牌相关问题采集后生成平台策略</div>';

  const promptPairs = strategies.flatMap(item => (item.prompt_pairs || []).map(pair => ({...pair, platform_name: item.platform_name})));
  $('#prompt-comparison-body').innerHTML = promptPairs.sort((a, b) => b.change - a.change).map(pair => `<tr>
    <td>${escapeHTML(pair.platform_name)}</td><td>${escapeHTML(pair.prompt_a)}</td><td>${escapeHTML(pair.prompt_b)}</td>
    <td>${pair.similarity}%</td><td><span class="change-value ${pair.change >= 60 ? 'high' : pair.change >= 30 ? 'medium' : 'low'}">${pair.change}%</span></td>
    <td>${pair.shared_domains.length ? pair.shared_domains.map(escapeHTML).join('、') : '无共同来源'}</td></tr>`
  ).join('') || '<tr><td colspan="6">至少完成两种品牌相关提示词的有效引用采集后显示对比。</td></tr>';

  const variability = retrieval.variability || [];
  $('#variability-body').innerHTML = variability.map(item => {
    const measurable = item.pattern !== 'insufficient';
    const sources = [
      ...item.core_domains.map(source => `<b>${escapeHTML(source.domain)} ${source.rate}%</b>`),
      ...item.rotating_domains.map(source => `<span>${escapeHTML(source.domain)} ${source.rate}%</span>`)
    ].join('') || '<small>暂无重复引用来源</small>';
    return `<tr><td><div class="variability-name"><b>${escapeHTML(item.platform_name)}</b><span>${escapeHTML(item.prompt_text)}</span></div></td>
      <td>${item.citation_runs} / ${item.runs}</td>
      <td><div class="stability-meter"><i style="width:${measurable ? item.stability : 0}%"></i></div><small>${measurable ? `${item.stability}%` : '—'}</small></td>
      <td>${measurable ? `${item.change_rate}%` : '—'}<small>${measurable ? ` · 熵 ${item.entropy}%` : ''}</small></td>
      <td><span class="pattern-pill ${escapeHTML(item.pattern)}">${escapeHTML(item.pattern_label)}</span></td>
      <td><div class="domain-patterns">${sources}</div></td></tr>`;
  }).join('') || '<tr><td colspan="6">暂无重复测试数据；请保持平台和提示词相同，至少运行 3 次。</td></tr>';
}

function renderTrend(items) {
  const element = $('#trend-chart');
  if (!items.length) {
    element.className = 'trend-chart empty-state';
    element.textContent = '积累跨日数据后显示趋势';
    return;
  }
  const visibility = items.map(item => item.total ? item.hits * 100 / item.total : 0);
  const citation = items.map(item => item.total ? item.cited_results * 100 / item.total : 0);
  const width = 900, height = 202, left = 35, right = 12, top = 15, bottom = 27;
  const x = (index, offset = 0) => (items.length === 1 ? width / 2 + offset : left + index * (width - left - right) / (items.length - 1));
  const y = value => top + (100 - value) * (height - top - bottom) / 100;
  const points = (values, offset = 0) => values.map((value, index) => [x(index, offset), y(value)]);
  const line = (values, offset = 0) => points(values, offset).map((point, index) => `${index ? 'L' : 'M'} ${point[0]} ${point[1]}`).join(' ');
  const visibilityOffset = items.length === 1 ? -7 : 0;
  const citationOffset = items.length === 1 ? 7 : 0;
  const visibilityPoints = points(visibility, visibilityOffset);
  const visibilityLine = line(visibility, visibilityOffset);
  const citationLine = line(citation, citationOffset);
  const area = `${visibilityLine} L ${visibilityPoints.at(-1)[0]} ${height - bottom} L ${visibilityPoints[0][0]} ${height - bottom} Z`;
  const xLabels = items.map((item, index) => {
    if (items.length > 8 && index % Math.ceil(items.length / 7) !== 0 && index !== items.length - 1) return '';
    return `<text class="chart-axis-label" text-anchor="middle" x="${x(index)}" y="${height - 6}">${item.day.slice(5)}</text>`;
  }).join('');
  element.className = 'trend-chart';
  element.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="可见度和引用覆盖趋势">
    <defs><linearGradient id="visibilityGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5b5ce2" stop-opacity=".18"/><stop offset="1" stop-color="#5b5ce2" stop-opacity="0"/></linearGradient></defs>
    ${[0, 25, 50, 75, 100].map(value => `<line class="chart-grid-line" x1="${left}" y1="${y(value)}" x2="${width - right}" y2="${y(value)}"/><text class="chart-axis-label" x="0" y="${y(value) + 3}">${value}%</text>`).join('')}
    <path class="visibility-area" d="${area}"/><path class="visibility-line" d="${visibilityLine}"/><path class="citation-line" d="${citationLine}"/>
    ${visibility.map((value, index) => `<circle class="visibility-dot" cx="${x(index, visibilityOffset)}" cy="${y(value)}" r="3.5"><title>${items[index].day} 可见度 ${value.toFixed(1)}%</title></circle>`).join('')}
    ${citation.map((value, index) => `<circle class="citation-dot" cx="${x(index, citationOffset)}" cy="${y(value)}" r="3"><title>${items[index].day} 引用覆盖 ${value.toFixed(1)}%</title></circle>`).join('')}
    ${items.length === 1 ? `<text class="chart-axis-label" text-anchor="end" x="${width / 2 - 15}" y="${y(visibility[0]) - 8}">可见度 ${visibility[0].toFixed(0)}%</text><text class="chart-axis-label" text-anchor="start" x="${width / 2 + 15}" y="${y(citation[0]) + 15}">引用 ${citation[0].toFixed(0)}%</text>` : ''}
    ${xLabels}
  </svg>`;
}

function healthClass(stateName) {
  if (['ready', 'success'].includes(stateName)) return 'good';
  if (['error', 'challenge'].includes(stateName)) return 'bad';
  return 'warn';
}

function renderHealth(platforms, totals) {
  $('#health-ring').style.setProperty('--score', totals.success_rate);
  $('#health-score-value').textContent = `${totals.success_rate}%`;
  $('#health-summary').textContent = totals.collected ? `${totals.total} 次成功，${totals.errors} 次异常` : '尚无采集';
  $('#health-detail').textContent = totals.errors ? '优先检查登录失效或页面结构变化' : '当前采集链路运行正常';
  $('#platform-health').innerHTML = platforms.map(platform => {
    const browser = state.browsers.find(item => item.slug === platform.slug) || { state: 'closed' };
    let label = browserStateName(browser.state);
    let stateKey = browser.state;
    if (platform.errors) { label = `${platform.errors} 次异常`; stateKey = 'error'; }
    else if (platform.total) { label = `${platform.total} 次成功`; stateKey = 'success'; }
    return `<div class="health-row"><span class="health-avatar" style="background:${platform.color}">${escapeHTML(platform.name.slice(0, 1))}</span><b>${escapeHTML(platform.name)}</b><span class="health-status ${healthClass(stateKey)}">${escapeHTML(label)}</span></div>`;
  }).join('');
}

function renderPlatformChart(items) {
  const element = $('#platform-chart');
  if (!items.some(item => item.total || item.errors)) {
    element.className = 'platform-rows empty-state';
    element.textContent = '运行一次监测后显示';
    return;
  }
  element.className = 'platform-rows';
  element.innerHTML = items.map(item => {
    const status = item.errors ? ['存在异常', 'bad'] : item.total ? ['正常', ''] : ['待采集', 'warn'];
    return `<div class="platform-row">
      <div class="platform-name"><span class="platform-avatar" style="background:${item.color}">${escapeHTML(item.name.slice(0, 1))}</span>${escapeHTML(item.name)}</div>
      <div class="dual-bars">
        <div class="bar-line"><span>提及</span><div class="bar-track"><div class="bar-fill visibility" style="width:${item.hit_rate}%"></div></div><b>${item.hit_rate}%</b></div>
        <div class="bar-line"><span>引用</span><div class="bar-track"><div class="bar-fill citation" style="width:${item.citation_rate}%"></div></div><b>${item.citation_rate}%</b></div>
      </div>
      <div class="rank-value">${item.avg_rank ? `#${item.avg_rank}` : '—'}<small>${item.hits} 次命中</small></div>
      <div><span class="status-pill ${status[1]}">${status[0]}</span></div>
    </div>`;
  }).join('');
}

function renderSourceChart(items) {
  const element = $('#source-chart');
  if (!items.length) {
    element.className = 'source-chart empty-state';
    element.textContent = '尚无引用来源';
    return;
  }
  const max = Math.max(...items.map(item => item.count));
  element.className = 'source-chart';
  element.innerHTML = items.map((item, index) => `<div class="source-row" title="${escapeHTML(item.domain)}">
    <div class="source-meta"><span class="source-domain">${index + 1}. ${escapeHTML(item.domain)}</span><span class="source-count">${item.count} 次</span></div>
    <div class="source-track"><div class="source-fill" style="width:${item.count * 100 / max}%"></div></div>
  </div>`).join('');
}

function opportunityFor(prompt) {
  if (!prompt.total) return ['待采集', 'medium'];
  if (prompt.hit_rate < 50) return ['高优先机会', 'high'];
  if (!prompt.citations) return ['补强引用', 'medium'];
  return ['表现稳定', 'good'];
}

function renderPromptPerformance(items) {
  const body = $('#prompt-performance-body');
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="6">暂无提示词表现数据</td></tr>';
    return;
  }
  body.innerHTML = items.slice(0, 10).map(prompt => {
    const opportunity = opportunityFor(prompt);
    return `<tr><td title="${escapeHTML(prompt.prompt_text)}">${escapeHTML(prompt.prompt_text)}</td><td>${prompt.total}</td><td><b>${prompt.hit_rate}%</b></td><td>${prompt.avg_rank ? `#${prompt.avg_rank}` : '—'}</td><td>${prompt.citations}</td><td><span class="opportunity-pill ${opportunity[1]}">${opportunity[0]}</span></td></tr>`;
  }).join('');
}

function recordRow(result) {
  const actualMode = result.collection_method || result.mode;
  return `<tr data-result="${result.id}">
    <td>${formatTime(result.captured_at)}</td>
    <td><span class="platform-tag"><i style="background:${result.color}"></i>${escapeHTML(result.platform_name)}</span></td>
    <td title="${escapeHTML(result.prompt_text)}">${escapeHTML(result.prompt_text)}</td>
    <td><span class="mode-tag ${actualMode}">${modeNames[actualMode] || escapeHTML(actualMode)}</span>${result.mode === 'auto' ? '<small class="fallback-note">自动</small>' : ''}</td>
    <td><span class="hit ${result.brand_hit ? 'yes' : 'no'}">${result.brand_hit ? '已提及' : '未提及'}</span></td>
    <td>${result.rank_position ? `#${result.rank_position}` : '—'}</td>
    <td>${result.citation_count}</td>
    <td><span class="record-status ${result.status}">${statusNames[result.status] || escapeHTML(result.status)}</span></td>
  </tr>`;
}

function filteredResults() {
  const platform = $('#platform-filter').value;
  const hit = $('#hit-filter').value;
  const status = $('#status-filter').value;
  const query = $('#record-search').value.trim().toLocaleLowerCase();
  return state.dashboard.results.filter(result => {
    const text = `${result.platform_name} ${result.prompt_text} ${result.answer_text} ${result.error_message}`.toLocaleLowerCase();
    return (!platform || result.platform_slug === platform)
      && (!hit || (hit === 'yes') === result.brand_hit)
      && (!status || result.status === status)
      && (!query || text.includes(query));
  });
}

function renderTables() {
  const all = state.dashboard.results;
  $('#recent-body').innerHTML = all.slice(0, 7).map(recordRow).join('') || '<tr><td colspan="8">暂无数据，请先运行监测</td></tr>';
  const filtered = filteredResults();
  $('#record-count').textContent = `${filtered.length} 条记录`;
  const success = all.filter(item => item.status === 'success').length;
  const hit = all.filter(item => item.status === 'success' && item.brand_hit).length;
  const cited = all.filter(item => item.status === 'success' && item.citation_count).length;
  $('#record-scope-summary').innerHTML = `<span><b>${all.length}</b>总记录</span><span><b>${success}</b>成功</span><span><b>${hit}</b>品牌命中</span><span><b>${cited}</b>含引用</span>`;
  $('#records-body').innerHTML = filtered.map(recordRow).join('') || '<tr><td colspan="8">没有符合条件的记录</td></tr>';
}

function openModal(html) {
  $('#modal-content').innerHTML = html;
  $('#modal').classList.add('open');
  $('#modal').setAttribute('aria-hidden', 'false');
}
function closeModal() {
  $('#modal').classList.remove('open');
  $('#modal').setAttribute('aria-hidden', 'true');
}

function showResult(id) {
  const result = state.dashboard.results.find(item => item.id === Number(id));
  if (!result) return;
  openModal(`<h2>${escapeHTML(result.platform_name)} · 回答证据</h2>
    <p class="sub">${formatTime(result.captured_at)} · ${escapeHTML(result.prompt_text)} · ${modeNames[result.collection_method || result.mode] || result.collection_method || result.mode}${result.mode === 'auto' ? '（自动模式）' : ''}</p>
    <span class="hit ${result.brand_hit ? 'yes' : 'no'}">${result.brand_hit ? `已提及 · 位次 #${result.rank_position || '—'}` : '未提及目标品牌'}</span>
    <span class="record-status ${result.status}">${statusNames[result.status] || result.status}</span>
    <div class="answer-box">${escapeHTML(result.answer_text || result.error_message || '无回答')}</div>
    <h2>回答中的品牌优先级</h2>
    <div class="answer-brands">${(result.brand_mentions || []).map(item => `<span class="${item.is_target ? 'target' : ''}"><b>${escapeHTML(item.brand_name)}</b><em>${item.priority_rank ? `#${item.priority_rank}` : '未排序'}</em><small>${item.extraction_method === 'rule' ? '规则' : 'AI 语义'} · ${Math.round((item.confidence || 0) * 100)}%</small></span>`).join('') || '<span class="sub">本次回答尚无品牌竞争分析</span>'}</div>
    <h2>引用来源（${result.sources.length}）</h2>
    <div class="source-list">${result.sources.map(source => `<a class="source-link" href="${escapeHTML(source.url)}" target="_blank" rel="noopener">${escapeHTML(source.title || source.domain)} · ${escapeHTML(source.url)}</a>`).join('') || '<span class="sub">本次回答未识别到外部来源网址</span>'}</div>
    <h2 class="trace-title">检索过程</h2>
    <div class="query-chips">${(result.search_queries || []).map(query => `<span>${escapeHTML(query.query_text)} <b>${escapeHTML(query.provider)}</b></span>`).join('') || '<small>平台未暴露搜索词</small>'}</div>
    <div class="trace-list">${(result.source_observations || []).map(item => `<div><span class="trace-stage ${item.cited ? 'cited' : item.selected ? 'selected' : 'retrieved'}">${item.cited ? '引用' : item.selected ? '选材' : '召回'}</span><a href="${escapeHTML(item.url)}" target="_blank" rel="noopener">${escapeHTML(item.title || item.domain)}</a><small>#${item.search_rank || '—'} · 本地相关度 ${Math.round((item.local_relevance || 0) * 100)}% · ${escapeHTML(item.evidence_level)}</small></div>`).join('') || '<span class="sub">本次只能观察最终回答，暂无中间检索数据</span>'}</div>`);
}

function manualModal() {
  const config = state.config;
  openModal(`<h2>人工回填真实采集</h2><p class="sub">适合扫码、验证码或页面结构临时变化时使用。</p>
    <form id="manual-form">
      <label>AI 平台<select name="platform_id">${config.platforms.filter(item => item.active).map(item => `<option value="${item.id}">${escapeHTML(item.name)}</option>`).join('')}</select></label>
      <label>提示词<select name="prompt_id">${config.prompts.filter(item => item.active).map(item => `<option value="${item.id}">${escapeHTML(item.text)}</option>`).join('')}</select></label>
      <label>完整回答<textarea name="answer" required placeholder="粘贴 AI 的完整回答"></textarea></label>
      <label>引用来源网址（每行一个）<textarea name="sources" rows="4" placeholder="https://example.com/article"></textarea></label>
      <button class="button primary" type="submit">保存真实采集</button>
    </form>`);
}

function runModal() {
  const config = state.config;
  openModal(`<h2>立即监测</h2><p class="sub">自动模式优先调用官方联网 API；未配置或失败时再使用 WebDriver。</p>
    <form id="run-form">
      <label>提示词<select name="prompt_id">${config.prompts.filter(item => item.active).map(item => `<option value="${item.id}">${escapeHTML(item.text)}</option>`).join('')}</select></label>
      <label>采集方式<select name="mode"><option value="auto">自动：API 优先，WebDriver 兜底</option><option value="api">仅官方 API</option><option value="webdriver">仅 WebDriver</option><option value="webhook">Webhook</option><option value="demo">演示采集</option></select></label>
      <label><span><input type="checkbox" name="all" style="width:auto"> 运行全部启用提示词</span></label>
      <button class="button primary" type="submit">开始采集</button>
    </form>`);
}

function loginModal(platformId) {
  const platform = state.config.platforms.find(item => item.id === Number(platformId));
  const browser = state.browsers.find(item => item.slug === platform.slug) || { state: 'closed', message: '浏览器未启动' };
  openModal(`<h2>${escapeHTML(platform.name)} · WebDriver 登录</h2>
    <p class="sub">系统会打开独立的可见 Chrome。请自行完成扫码、短信或验证码，不读取你的日常 Chrome Cookie。</p>
    <div class="login-status"><span class="browser-state ${escapeHTML(browser.state)}">${browserStateName(browser.state)}</span><b>${escapeHTML(browser.message)}</b></div>
    <div class="login-actions"><button class="button primary" data-browser-open="${platform.id}">打开登录窗口</button><button class="button ghost" data-browser-check="${platform.id}">检测状态</button><button class="button ghost" data-browser-close="${platform.id}">关闭窗口</button></div>
    <p class="sub">登录态保存在本机 instance/browser_profiles/${escapeHTML(platform.slug)}，服务重启后仍可复用。</p>`);
}

document.addEventListener('click', async event => {
  const nav = event.target.closest('[data-view]');
  if (nav) switchView(nav.dataset.view);
  const go = event.target.closest('[data-go]');
  if (go) switchView(go.dataset.go);
  if (event.target.closest('[data-close]')) closeModal();
  const row = event.target.closest('[data-result]');
  if (row) showResult(row.dataset.result);
  if (event.target.closest('#manual-btn')) manualModal();
  if (event.target.closest('#run-btn')) runModal();
  if (event.target.closest('#run-prompt-set')) {
    runModal();
    const all = $('#run-form input[name="all"]');
    if (all) all.checked = true;
  }
  const aiQuestion = event.target.closest('[data-ai-question]');
  if (aiQuestion) {
    switchView('ai');
    if (!state.apiConfig?.secrets?.OPENAI_API_KEY) return showAiSetup();
    await waitForAiIdle();
    if (!state.ai.initialized) await ensureAiEvaluation();
    await waitForAiIdle();
    await sendAiMessage(aiQuestion.dataset.aiQuestion);
  }
  if (event.target.closest('#refresh-ai-evaluation')) {
    try { await ensureAiEvaluation(true); } catch (error) { showAiSetup(error.message); toast(error.message, true); }
  }
  if (event.target.closest('#clear-ai-chat')) {
    state.ai.messages = []; state.ai.initialized = false;
    $('#ai-messages').innerHTML = '<div class="ai-empty"><span>◇</span><b>对话已清空</b><p>点击“重新评估当前数据”开始新的分析。</p></div>';
    $('#ai-chat-status').textContent = '等待默认评估';
  }

  const range = event.target.closest('[data-days]');
  if (range) {
    state.days = Number(range.dataset.days);
    $$('.range-group button').forEach(button => button.classList.toggle('active', button === range));
    try { await loadDashboard(); } catch (error) { toast(error.message, true); }
  }

  const login = event.target.closest('[data-login]');
  if (login) loginModal(login.dataset.login);

  const browserOpen = event.target.closest('[data-browser-open]');
  if (browserOpen) {
    try {
      browserOpen.disabled = true; browserOpen.textContent = '正在打开…';
      await api('/api/browser/login', { method: 'POST', body: JSON.stringify({ platform_id: Number(browserOpen.dataset.browserOpen) }) });
      await loadConfig(); loginModal(browserOpen.dataset.browserOpen);
    } catch (error) { toast(error.message, true); browserOpen.disabled = false; browserOpen.textContent = '重试'; }
  }
  const browserCheck = event.target.closest('[data-browser-check]');
  if (browserCheck) {
    try {
      const response = await api('/api/browser/check', { method: 'POST', body: JSON.stringify({ platform_id: Number(browserCheck.dataset.browserCheck) }) });
      await loadConfig(); loginModal(browserCheck.dataset.browserCheck); toast(response.data.message);
    } catch (error) { toast(error.message, true); }
  }
  const browserClose = event.target.closest('[data-browser-close]');
  if (browserClose) {
    try {
      await api('/api/browser/close', { method: 'POST', body: JSON.stringify({ platform_id: Number(browserClose.dataset.browserClose) }) });
      closeModal(); await loadConfig(); toast('浏览器已关闭，登录态保留');
    } catch (error) { toast(error.message, true); }
  }

  const remove = event.target.closest('[data-delete]');
  if (remove && confirm('确认删除这个提示词？')) {
    try { await api(`/api/prompts/${remove.dataset.delete}`, { method: 'DELETE' }); await loadConfig(); toast('提示词已删除'); }
    catch (error) { toast(error.message, true); }
  }
  const suggestion = event.target.closest('[data-add-suggestion]');
  if (suggestion && !suggestion.disabled) {
    try {
      await api('/api/prompts', { method: 'POST', body: JSON.stringify({ text: suggestion.dataset.addSuggestion }) });
      suggestion.disabled = true; suggestion.textContent = '已加入';
      await loadConfig(); toast('反推提示词已加入实验列表');
    } catch (error) { toast(error.message, true); }
  }
});

document.addEventListener('change', async event => {
  if (event.target.dataset.toggle) {
    try {
      await api(`/api/${event.target.dataset.toggle}/${event.target.dataset.id}`, { method: 'PATCH', body: JSON.stringify({ active: event.target.checked }) });
      await loadConfig(); toast('启用状态已更新');
    } catch (error) { toast(error.message, true); }
  }
  if (event.target.matches('#platform-filter,#hit-filter,#status-filter')) renderTables();
  if (event.target.id === 'brand-prompt-filter') {
    state.brandPrompt = event.target.value;
    renderBrandLandscape(state.dashboard.brand_landscape || {});
  }
});
$('#record-search').addEventListener('input', renderTables);

document.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    if (event.target.id === 'prompt-form') {
      await api('/api/prompts', { method: 'POST', body: JSON.stringify({ text: $('#new-prompt').value }) });
      $('#new-prompt').value = ''; await loadConfig(); toast('提示词已添加');
    }
    if (event.target.id === 'reverse-prompt-form') {
      const button = event.target.querySelector('button');
      button.disabled = true; button.textContent = '正在分析历史数据…';
      const response = await api('/api/prompts/reverse', { method: 'POST', body: JSON.stringify({ goal: $('#reverse-goal').value, limit: 12 }) });
      renderReverseSuggestions(response.data);
      button.disabled = false; button.textContent = '重新反推';
      toast(`已由${response.data.provider}生成提示词实验建议`);
    }
    if (event.target.id === 'ai-chat-form') {
      const input = $('#ai-chat-input'); const content = input.value.trim();
      if (!content) return;
      input.value = '';
      await sendAiMessage(content);
    }
    if (event.target.id === 'run-form') {
      const form = new FormData(event.target); const button = event.target.querySelector('button');
      button.disabled = true; button.textContent = '采集中…';
      const response = await api('/api/runs', { method: 'POST', body: JSON.stringify({ prompt_id: Number(form.get('prompt_id')), mode: form.get('mode'), all: Boolean(form.get('all')) }) });
      const runIds = response.data.run_ids || [response.data.run_id];
      closeModal(); await Promise.all([loadDashboard(), loadConfig()]);
      const failed = state.dashboard.results.filter(result => runIds.includes(result.run_id) && result.status !== 'success');
      if (failed.length) {
        const platforms = [...new Set(failed.map(result => result.platform_name))].join('、');
        toast(`${platforms} 采集失败，请检查是否需要登录或验证`, true);
      } else {
        toast('监测完成，所有平台均成功返回回答');
      }
    }
    if (event.target.id === 'manual-form') {
      const form = Object.fromEntries(new FormData(event.target));
      await api('/api/manual-capture', { method: 'POST', body: JSON.stringify(form) });
      closeModal(); await loadDashboard(); toast('真实采集已保存');
    }
  } catch (error) {
    toast(error.message, true);
    const button = event.target.querySelector('button');
    if (button) { button.disabled = false; button.textContent = '重试'; }
  }
});

$('#save-config').addEventListener('click', async () => {
  try {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify({
      brand_name: $('#brand-name').value, aliases: $('#aliases').value, webhook_url: $('#webhook-url').value,
      owned_domains: $('#owned-domains').value,
      schedule_enabled: $('#schedule-enabled').checked, schedule_minutes: Number($('#schedule-minutes').value),
      schedule_mode: $('#schedule-mode').value, ai_brand_analysis_enabled: $('#ai-brand-analysis-enabled').checked
    }) });
    await loadConfig(); toast('配置已保存');
  } catch (error) { toast(error.message, true); }
});

$('#save-api-config').addEventListener('click', async () => {
  const values = {
    DOUBAO_API_KEY: $('#api-doubao-key').value, DOUBAO_MODEL: $('#api-doubao-model').value,
    QWEN_API_KEY: $('#api-qwen-key').value, QWEN_MODEL: $('#api-qwen-model').value,
    BAIDU_API_KEY: $('#api-baidu-key').value, BAIDU_MODEL: $('#api-baidu-model').value,
    TENCENT_SECRET_ID: $('#api-tencent-id').value, TENCENT_SECRET_KEY: $('#api-tencent-secret').value,
    TENCENT_HUNYUAN_MODEL: $('#api-tencent-model').value,
    DEEPSEEK_API_KEY: $('#api-deepseek-key').value, DEEPSEEK_MODEL: $('#api-deepseek-model').value,
    DEEPSEEK_SEARCH_PROVIDER: $('#api-deepseek-provider').value
  };
  try {
    await api('/api/api-config', { method: 'PUT', body: JSON.stringify({ values }) });
    await loadConfig(); toast('API 配置已安全保存到本机');
  } catch (error) { toast(error.message, true); }
});

$('#save-openai-config').addEventListener('click', async () => {
  const values = { OPENAI_API_KEY: $('#api-openai-key').value, OPENAI_MODEL: $('#api-openai-model').value };
  try {
    await api('/api/api-config', { method: 'PUT', body: JSON.stringify({ values }) });
    state.ai.initialized = false; state.ai.messages = [];
    await loadConfig(); toast('OpenAI 配置已安全保存到本机');
  } catch (error) { toast(error.message, true); }
});

$('#clear-api-config').addEventListener('click', async () => {
  if (!confirm('确认清除本机保存的全部 API 密钥？环境变量不会被修改。')) return;
  const clear = ['OPENAI_API_KEY', 'DOUBAO_API_KEY', 'QWEN_API_KEY', 'BAIDU_API_KEY', 'TENCENT_SECRET_ID', 'TENCENT_SECRET_KEY', 'DEEPSEEK_API_KEY'];
  try {
    await api('/api/api-config', { method: 'PUT', body: JSON.stringify({ values: {}, clear }) });
    await loadConfig(); toast('本地 API 密钥已清除');
  } catch (error) { toast(error.message, true); }
});

Promise.all([loadConfig(), loadDashboard()]).catch(error => toast(error.message, true));
