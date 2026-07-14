# BrandScope — AI 品牌曝光监测

面向豆包、千问、文心一言、DeepSeek、元宝等 AI 问答平台的品牌提及与引用来源监测 MVP。默认目标品牌为“瑞思迈ResMed”。

## 桌面版下载

GitHub Releases 会提供 Electron 桌面安装包：

| 系统 | 架构 | 安装包 |
| --- | --- | --- |
| macOS | Apple Silicon | `.dmg`、`.zip` |
| macOS | Intel | `.dmg`、`.zip` |
| Windows | x64 | NSIS `.exe`、`.zip` |
| Linux | x64 | `.AppImage`、`.deb` |

打开项目的 [Releases 页面](https://github.com/thunder951413/brand_expo_monitor/releases) 下载最新版。桌面版会在系统应用数据目录中独立保存数据库、API 配置和浏览器登录资料，升级应用不会覆盖监测数据。

当前自动构建产物未进行 Apple Developer ID 公证或 Windows Authenticode 签名，macOS/Windows 首次运行时可能显示系统安全提醒。正式对外分发时，应在 GitHub Actions 中配置相应代码签名与公证凭证。

## 已实现

- 品牌名与多个中英文别名配置，英文匹配不区分大小写
- 独立“提示词实验”页：提示词新增、删除、启用/停用，以及从曝光目标反推自然用户问题
- AI 反推会结合历史品牌命中率、来源相关度和平台实际搜索词；未配置模型时使用可解释规则生成实验候选
- 五个平台启用/停用与登录页面快捷入口
- 回答中的品牌命中、提及次数、近似榜单位次分析
- 回答引用 URL 提取、去重、域名聚合及来源 Top 12
- 7/30/90 天及全部历史筛选，品牌可见度、平均位次、引用覆盖率、采集成功率
- 可见度/引用双趋势、平台双指标对比、提示词机会排序与采集健康面板
- 官方联网 API、API 优先/WebDriver 兜底、演示、人工回填、Webhook、WebDriver 六种模式
- 定时执行所有已启用提示词与平台
- SQLite 历史留存、汇总数据表、平台对比图、趋势图、CSV 导出
- 检索漏斗追踪：实际搜索词、召回排名、平台分数、本地相关度、选材、引用及证据片段
- 品牌自有域名在召回/选材/引用阶段的覆盖率与规则化策略建议
- 独立“相关度分析”页：集中展示阶段网站、来源转化、提示词敏感度、同问时间波动与平台信源策略

> 演示采集只用于验证系统闭环，界面与记录中会显示“演示”标签，不代表对应 AI 平台的实时结果。真实数据请使用人工回填或 Webhook 自动采集。

## 界面与工作流

应用按一次品牌曝光研究的实际决策路径组织，而不是按技术模块堆放功能：

1. **可见性概览**：回答“品牌是否出现、位置如何、哪个平台或问题最需要处理”，同时显示样本量、成功率和重复测试形成的数据可信度。
2. **AI 交流**：自动把产品方法论、当前指标、平台与提示词表现、信源轨迹、稳定性和近期回答节选整理为前置上下文，先生成默认评估，再连续追问。
3. **回答证据**：把汇总指标追溯到单次回答、引用网址、实际搜索词、检索轨迹和失败原因。
4. **信源研究**：解释网站从召回、选材到最终引用的转化，比较提示词差异和同条件时间波动，再生成信源策略。
5. **提示词实验**：管理真实用户问题、查看意图覆盖，并从品牌曝光目标反推新的实验问题。
6. **采集配置**：统一检查品牌识别、启用问题、目标平台、API/WebDriver 通道和定时任务是否准备完成。

时间范围、当前品牌、平台数、提示词数和数据新鲜度作为全局研究上下文，在所有报告页保持可见。平台没有公开的检索过程不会被 UI 推测；平台原始分数和本地相关度会明确区分。

## 启动

```bash
git clone https://github.com/thunder951413/brand_expo_monitor.git
cd brand_expo_monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

打开 <http://127.0.0.1:5000>。当前机器的系统 Python 已包含 Flask，也可以直接运行 `python3 app.py`。

数据默认保存在 `instance/monitor.db`，可通过环境变量 `BRAND_MONITOR_DB` 指定其他路径。

`instance/` 已加入 `.gitignore`，其中可能包含 API 密钥、SQLite 监测数据和浏览器登录态，请勿提交到 Git 仓库。首次启动时应用会自动创建所需目录和数据库。

## 官方联网 API（推荐）

配置页面可以录入各平台凭证。凭证只保存在本机 `instance/api_config.json`，文件权限自动设置为 `600`；后端只向页面返回是否已配置，不返回密钥内容。也可以通过同名环境变量注入凭证，环境变量优先于配置文件。

AI 交流使用 OpenAI Responses API。设置 `OPENAI_API_KEY` 后，页面会使用 `OPENAI_MODEL`（默认 `gpt-5.6-luna`）分析当前监测上下文。请求设置 `store: false`，聊天记录由当前页面在本地内存中维护；但发起分析时，监测汇总、近期回答节选和对话内容仍会发送给 OpenAI，请按公司的数据政策决定是否启用。

| 平台 | 凭证 | 联网与来源实现 |
| --- | --- | --- |
| AI 研究助理 | `OPENAI_API_KEY` | Responses API，用于默认曝光评估和基于监测上下文的多轮交流 |
| 豆包/火山方舟 | `DOUBAO_API_KEY` | Responses API `web_search`，解析 URL annotations |
| 千问/百炼 | `QWEN_API_KEY` | DashScope `enable_search`、`enable_source`、`enable_citation` |
| 文心/千帆 | `BAIDU_API_KEY` | `/v2/ai_search/chat/completions`，解析 `references` |
| 元宝能力/混元 | `TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY` | TC3 签名调用混元搜索增强，解析 `SearchInfo` |
| DeepSeek | `DEEPSEEK_API_KEY` + 百度或腾讯搜索凭证 | 先调用联网搜索 API，再把搜索摘要和 URL 交给 DeepSeek |

模型名称可在页面修改。也可以复制 `api_config.example.json` 为 `instance/api_config.json` 后填写。生产环境更建议使用环境变量或进程密钥管理器。

运行方式：

- `自动`：优先官方 API；某个平台未配置或 API 失败时，仅该平台回退到 WebDriver。
- `仅官方 API`：不启动浏览器；缺少凭证时记录为 `api_unconfigured`。
- `仅 WebDriver`：始终使用网页端。

API 与消费者网页端的模型版本、搜索策略可能不同，因此系统保留采集模式字段，便于把 API 结果和网页端基准分开比较。

### 检索漏斗与策略实验

每次采集会尽可能记录：

- 平台或搜索服务实际返回的搜索词
- 搜索结果的原始排名和平台分数（平台提供时）
- 标题、摘要片段、发布时间、网址和域名
- 本地可解释的词项重合相关度；该分数明确区别于平台内部相关度
- 来源是否处于“召回、进入选材、最终引用”阶段
- 回答中的引用编号和引用次序
- 数据证据级别：`observed` 表示 API 明确返回，`final_only` 表示网页端只能观察到最终引用

平台内部没有公开的数据不会被推测成真实过程。例如 WebDriver 只能看到最终引用时，系统不会伪造搜索排名。配置“品牌自有域名”后，“相关度分析”页会判断自有内容主要流失在召回、选材还是引用阶段，并给出对应的内容优化方向。

完整轨迹可从 `/api/export-retrieval.csv` 导出，用于跨时间、提示词和平台进行回测。

同一平台、同一提示词累计至少 3 次有效采集后，“相关度分析”页还会计算引用来源的随机性与规律：

- 多轮引用域名集合的平均 Jaccard 重合率（引用稳定度）及其反向指标（来源变化率）
- 来源多样性熵、每次平均引用数和引用覆盖率
- 单个域名的出现频率：70% 以上归为固定来源，30%–70% 归为轮换来源，其余为偶发来源
- 同一域名的引用位次波动
- 分为“来源稳定”“有规律轮换”“波动较高”或“样本不足”，并按平台生成信源布局建议
- 将所有品牌相关提示词合并计算平台整体信源稳定度，同时比较不同提示词的来源集合，生成“提示词敏感度”
- 提示词敏感度高时，按购买推荐、品牌排行、产品对比等问题簇分别反推信源策略；敏感度低时，则优先突破平台长期复用的核心信源

这些指标描述可观测结果的重复性，不会把变化直接断言为真正随机。系统允许跨提示词观察品牌曝光的总体规律，同时建议保留少量重复问法作为基线，才能区分“提示词导致的系统变化”和“相同条件下的随机波动”。

## 其他真实采集方式

### WebDriver（推荐用于需要网页登录的平台）

1. 在“监测配置 → AI 平台”中点击某个平台的“登录/检测”。
2. 点击“打开登录窗口”，系统会打开一个独立的可见 Chrome。
3. 在该窗口自行完成扫码、短信验证码或安全验证，然后点击“检测登录状态”。
4. 显示“可采集”后，点击右上角“立即监测”，选择“WebDriver 真实采集”。

每个平台使用独立的持久化资料目录：

```text
instance/browser_profiles/doubao
instance/browser_profiles/qwen
instance/browser_profiles/ernie
instance/browser_profiles/deepseek
instance/browser_profiles/yuanbao
```

这些资料目录不会读取或修改日常 Chrome 的 Cookie。关闭自动化窗口或重启服务后，登录态仍会保留。平台要求验证码时，系统会返回 `login_required`；页面结构变化导致无法识别输入框或回答时，返回 `selector_changed`。系统不会绕过验证码或平台安全机制。

采集采用“匿名优先”策略：页面存在可用输入框时会直接尝试提问，即使页面顶部仍显示登录按钮也不会阻止采集。只有提交后出现登录拦截、验证码、安全验证，或始终无法取得回答时，才将该平台记录为 `login_required` 并提示人工检查；其他平台会继续执行。

Selenium 4 会通过 Selenium Manager 自动查找与本机 Chrome 匹配的驱动，通常不需要单独安装 `chromedriver`。首次启动可能需要联网下载匹配驱动。

常见登录与运行状态：

| 状态 | 含义 | 处理方式 |
| --- | --- | --- |
| 可采集 | 已登录并识别到提问框 | 可以执行或定时采集 |
| 匿名可用 | 可提问，但页面仍显示登录入口 | 建议登录，否则联网引用和次数可能受限 |
| 需登录 | 扫码、短信或账号登录页 | 在可见 Chrome 中人工完成，再点“检测登录状态” |
| 需验证 | 验证码、安全验证或异常流量页 | 人工完成验证；系统不会尝试绕过 |
| 等待中 | 页面仍在加载，或需要点击“开始使用” | 在浏览器窗口继续操作后重新检测 |
| 页面结构变化 | 未找到输入框或回答节点 | 更新对应平台适配器选择器，或暂用人工回填 |

注意事项：

- 同一平台的资料目录一次只能由一个 Chrome/WebDriver 进程使用。不要同时启动两个本项目服务。
- 二维码过期时直接在自动化 Chrome 中刷新；不要把 Cookie、密码或二维码截图写入项目配置。
- Chrome 自动升级后若驱动不匹配，重启服务让 Selenium Manager 重新解析驱动。
- 建议定时间隔至少数小时，并使用合规的独立账号，避免高频提问触发平台风控。

### 人工回填

适合首次使用和平台要求扫码、验证码时：

1. 在“监测配置”中点击某个平台的“打开”。
2. 在已登录页面提交提示词并等待联网回答完成。
3. 点击应用右上角“人工回填”，粘贴完整回答及引用网址（每行一个）。
4. 保存后立即进入统计、图表和 CSV。

### Webhook 自动采集

适合连接企业 RPA、浏览器自动化服务或平台官方 API。在设置中填写 Webhook URL。应用会逐个平台发送：

```json
{
  "platform": {"slug": "doubao", "name": "豆包", "url": "https://www.doubao.com/chat/"},
  "prompt": "家用呼吸机品牌都有哪些？",
  "brand": {"name": "瑞思迈ResMed", "aliases": ["瑞思迈ResMed", "瑞思迈", "ResMed"]}
}
```

Webhook 应返回：

```json
{
  "answer": "完整回答文本",
  "sources": [
    {"url": "https://example.com/article", "title": "来源标题"}
  ]
}
```

自动化端应使用单独的合规账号与持久化浏览器登录态，并处理平台授权、频率限制、验证码和页面结构变化。不要尝试绕过平台安全机制。

## 定时任务说明

定时器运行在 Flask 服务进程内，适合本地单进程 MVP。启用后，第一次执行时间为“保存设置时间 + 执行间隔”。推荐定时模式选择“自动：API 优先”。选择 WebDriver 定时采集前，应逐个平台完成一次登录检测；登录失效或风控验证会作为失败状态落库，不会卡住后续平台。生产部署若使用多个 Web worker，应只启动一个调度实例，或将定时触发迁移到 cron/Celery 等独立调度器。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## Electron 多平台构建与发布

Electron 负责桌面窗口和生命周期，Flask 后端通过 PyInstaller 打包为各平台原生可执行文件。运行数据统一写入 Electron 的 `userData/data`，不会写入只读的应用安装目录。

本地构建当前系统对应版本：

```bash
npm ci
python3 -m pip install -r requirements.txt "pyinstaller>=6.21,<7"

# macOS Apple Silicon
npm run dist:mac:arm64

# 其他目标由对应操作系统执行
npm run dist:mac:x64
npm run dist:win:x64
npm run dist:linux:x64
```

推送 `v*` 标签时，[Release workflow](.github/workflows/release.yml) 会分别在 macOS arm64、macOS Intel、Windows x64 和 Linux x64 runner 上运行测试与构建，并把全部安装包合并发布到同一个 GitHub Release。
