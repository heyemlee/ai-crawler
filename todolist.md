# Bay Area ProjectIntel — TODO List

> 本地 CLI 工具：从湾区公开数据发现 B2B 项目线索，分类后导出按类别分 sheet 的 Excel。
> 核心原则：**合法公开数据 + 合规访问 + 浏览器辅助**，API-first，不绕反爬、不抓登录/付费墙。
> 管线：`fetch → classify → dedupe → enrich → export`，每步幂等、状态落 SQLite、可增量重跑。

已确认决策：多源并行 · 分类用 DeepSeek（provider 无关）· 补全先公开来源再浏览器辅助 · **联系方式 = 硬要求**（每行必须有邮箱或电话，以公司为主体）· **只要最新数据，不接冻结/过期源**。

## ★ 数据来源地图（已确认 2026-05-29）

> **硬规则：只要最新（活跃更新）的数据源，不接冻结/历史快照**（如 San Jose 那份 2024 学术 ArcGIS 快照——已排除）。
> **合规边界**：可放宽 robots/礼貌性抓公开政府记录，但**不绕访问控制**（CAPTCHA/登录/付费墙/反爬封锁）。

| 类别 | 源 | 覆盖 | 状态 | 费用/注册 |
|---|---|---|---|---|
| **A. 官方开放 API（主力，实时/增量）** | Socrata | SF、Marin 县 | ✅ 已用 | 免费、免注册 |
| | ArcGIS FeatureServer | Campbell（+ 其他可探的活跃城市） | 🔨 接入中 | 免费、免注册 |
| | SAM.gov | 全湾区联邦 RFP（含南湾联邦） | ✅ 已接（每日 00:00 UTC 配额重置，拟每日定时跑） | 免费、需注册（已有 key） |
| **B. CPRA 公开记录请求** | 向各市政府要导出 | 任意城市（含 San Jose 等无 API 大城） | 待用 | 免费、合法、手动、非实时 |
| **C. 逐城公开只读端点/报表** | 各市自有 open-data/CSV/公开 REST | 逐城探，活跃且有承包商字段才接 | 待探 | 免费、免注册 |

- 排除：付费聚合（BuildZoom/Shovels…）；任何冻结/过期快照。
- 南湾大城（San Jose/Sunnyvale/Cupertino…）无免费实时 API → 走 B（CPRA 兜底）或 C（逐城探，多数会落空）。
- 所有源统一过硬要求：每行须有公司邮箱/电话（A/C 拿公司名后 enrich 补；SAM 自带 POC）。

### 数据源待办（2026-05-29，按优先级）
1. [x] **撤掉 Campbell 源 + 清库**（2026-05-29 完成）：Campbell 无承包商字段（`ProjectName`=业主/申请人），不满足硬要求。已从 `config/sources.yaml` 注释掉 campbell 源（保留为 ArcGIS 配置示例），删掉 270 条 Campbell 记录 + 孤儿 company（库回到 SF 27 + Marin 12 = 39），重跑 dedupe（2 组真重复）。**ArcGIS 适配器 `sources/arcgis.py` 保留备用**（含 fixture 测试）。
2. [x] **C 类：逐城公开只读端点/报表 —— `cli discover` 已建并跑通南湾**（2026-05-29 完成）：`sources/discover.py` 扫 Socrata 全局 catalog（api.us.socrata.com）+ ArcGIS Online search（arcgis.com/sharing/rest/search），对每个候选 dataset 抽 1 行样本,按字段名启发把字段分桶 contractor vs owner/applicant（normalize 大小写 + 去下划线,`ProjectName` 算 owner）。噪声过滤：城市名必须出现在 title/description/domain（支持空格变体,`San Jose` ↔ `sanjoseca.gov`）+ 必须含 permit 关键词,否则丢弃（避免 NY 州 Saratoga 噪声、`DOC Recreational Hunting` 误匹配）。**南湾实跑结论(2026-05-29,12 城/29 findings)**:零个 contractor-shaped candidate,确认 Phase 1.5 结论 —— 南湾全要走 CPRA（B）或 Accela 浏览器（D）。新鲜的 owner-only 数据:Cupertino ArcGIS（已停更 2019）+ Campbell ArcGIS（2026-05-22,已知 owner-only）。SF+Marin sanity check 通过(各自命中 candidate)。报告写到 `data/discover-report.md`/`.json`。15 个单测覆盖含 Campbell/Saratoga/Mountain View 噪声 fixture。可周期重跑,捕获城市将来新发布的 dataset。
3. [x] **B 类：CPRA 公开记录请求落地完成**（2026-05-29）：① `config/cpra/portals.yaml` —— 12 个南湾城市骨架,每条带 platform_hint(NextRequest / GovQA / City Clerk)+ contact_fallback + per-city notes(Campbell 标注"only path to contractor record"、Cupertino 标注 stale ArcGIS 已失效);portal_url 留 null 由人工填(避免我捏失效链接)。② `docs/cpra-request-template.md` —— 双语请求信(英文正文 + 中文要点 + CSV 列映射 cheat-sheet),指定 9 个必需列(permit_number/permit_type/description/address/issued_date/contractor_name/contractor_license/contractor_phone/contractor_email)。③ `cli import-cpra --file ... --jurisdiction "San Jose"` —— `sources/cpra_import.py` 读 CSV(utf-8-sig 兼 Excel BOM),按 portals.yaml 里的 per-city field_map 重命名列,写入 raw_records(source=cpra-{slug}, source_record_id=permit_number 或 content hash),复用现有 `_normalize_socrata` 走管线(synthetic SourceConfig 模式)。同 CSV 内重复 permit 标 skipped,跨次重跑幂等(upsert by source+source_record_id)。7 个单测 + 端到端 smoke 跑通(San Jose CSV → 2 projects,公司名/邮箱/电话/license 全入库)。**人工流程**:从 portals.yaml 找 city → 贴 template → 收 CSV → `cli import-cpra` 一行接入。

## 产品形态决策：自动化管线 + 受控 Agent 能力

本项目**不做成全自动自由浏览 AI agent**。最终形态应是：

```text
定时任务 / 自动抓取
→ 结构化数据管线
→ AI 分类/补全
→ Excel/数据库更新
→ 智能提醒
```

核心原则：
- `fetch / normalize / dedupe / export` 保持确定性代码，稳定、可审计、可重复。
- AI 用在高价值判断点：模糊分类、项目价值评分、补全线索判断、每日/每周摘要。
- BrowserHarness / OpenCLI 只作为 `enrich --browser` 的**后期可选补全工具**，仅访问公开、无需登录、无 CAPTCHA、ToS 允许的页面。
- 默认运行模式不启动浏览器，不绕反爬、不抓登录/付费墙。
- 定时抓取和智能提醒是目标能力：自动跑增量、导出 Excel、提示新增高价值线索和待人工补全项。
- OpenClaw / 微信入口只做调度和展示，不执行任意 shell 命令；微信只允许白名单动作：
  - “跑数据”：触发固定命令 `projectintel run ...`
  - “要Excel文档”：返回最近一次生成的 Excel 文件
  - “一周通知一次 / 每周更新一次”：创建或更新每周定时任务，默认解释为**每周一早上 8:00** 自动跑数据并通知
- 定时任务运行前需要本机可用：
  - 如果电脑关机，任务不会运行。
  - 如果电脑休眠，任务可能不会准时运行；需要 OpenClaw / launchd / caffeinate 方案在任务前保持唤醒。
  - 默认策略：每周任务在 **7:55** 提醒“请保持电脑唤醒”，并尽量用本地唤醒/防休眠机制保证 8:00 运行。

## 当前进度（2026-05-29）

**已是多源闭环**（不再是单源 DataSF）+ **discover 扫描器** + **CPRA CSV 导入**。测试 `87 passed`。

已完成并验证：
- **多源 fetch**：① DataSF Building Permits（Socrata + contacts join）② Marin County Building Permits（Socrata，承包商在记录上）③ SAM.gov 联邦 RFP（路径 A，自带 POC，湾区过滤）。Socrata 适配器已抽象成 `SocrataFieldMap`，**加有 API 的城市只改 yaml**。
- **classify**：规则层 + DeepSeek fallback（需 key）。
- **enrich**：CSLB 批量 CSV 电话补全 + 基础官网 email/phone 抽取（httpx+正则）。
- **export**：精简列、按 category 分 sheet、新增行高亮、硬过滤、Pending sheet、Summary 覆盖率。
- **report**：新增数/覆盖率/高价值/RFP/Pending 摘要，`run` 末尾自动打印。
- 库里现有真实数据：DataSF 27 条 + Marin 12 条。

**下一步优先级（按价值重排，2026-05-28）**：
1. [x] **Scrapling 静态增强 `PublicWebEnricher`**（最高 ROI）：已用 Scrapling `Selector`（CSS/XPath）替换 `PublicWebEnricher` 的正则 HTML 解析——`a::attr(href)` 结构化抽 mailto/tel/contact 链接、`get_all_text(ignore_tags=script/style)` 取干净可见文本（脚本/样式里的假邮箱不再误命中）。**fetch 仍走 `PoliteHttpClient.get_text`，robots/限速/缓存语义不变**；不装浏览器。直接作用在 Marin 那批"有公司名、无联系方式"的 Pending 上。
2. [x] **`BrowserEnricher`（opt-in）已实现**：`enrichment/browser.py` 用 Scrapling `DynamicFetcher`（Playwright）渲染 JS contact 页，复用 web.py 的 CSS 抽取（mailto/tel/contact 链接）。**robots/限速仍走 `PoliteHttpClient`**（已把 `ensure_allowed`/`throttle` 提为公开方法供其调用）；不绕登录/付费墙/CAPTCHA。浏览器内核为可选依赖（`pip install 'scrapling[fetchers]' && scrapling install`）；未装时优雅返回 skipped + 安装提示，不崩。fetcher 可注入，单测用假 fetcher 覆盖。
3. [x] **Phase 4 跨源去重已实现** `pipeline/dedupe.py`：地址归一化（保留 suite/unit，避免同楼不同套号误并）+ rapidfuzz `token_set_ratio` 地址/标题双高阈值（addr≥92, title≥72，保守：宁可漏并不可误并）。非破坏式标记 `projects.duplicate_of`（可审计/可重跑/不删行），enrich/export/report 自动隐藏 duplicate。`cli dedupe` + 已插入 `run`（classify 后、enrich 前）。**真实数据验证**：39 项中标出 2 组真重复（353 Sacramento St 同许可号、50 Barbaree Way Tiburon 同业主），无误并。
4. **notify + 本地定时**：`cli notify` + cron/launchd + 微信白名单（OpenClaw）。
5. **Tier 2 来源门户（南湾 Accela 等）**：大工程、ToS 敏感，单独排期（见 Phase 1.5 调研结论）。

**小遗留**（不阻塞主线）：
- [x] SAM `responseDeadLine` → Project 加 `bid_deadline`（normalize 抽取、DB 列、含迁移）+ Excel 新增「投标截止」列并红色高亮（Phase 6）。
- [x] `llm/prompts.py` 拆分（`classification_prompt`，client 引用）。
- [x] CSLB address/status/classification 入库：`CslbEnricher` 匹配即返回 address/PrimaryStatus/Classifications（无电话也返回），pipeline 写入 companies 新列（address/license_status/license_classification，含迁移）。
- [x] **统一配置**：散落的硬编码可调参数收进 `RuntimeSettings`，可由 `PROJECTINTEL_*` env / `.env` 覆盖——politeness 限速、dedupe addr/title 阈值、web 发现候选/contact 链接/最短 token 长、browser 页数上限；CLI 在构造 `PoliteHttpClient`/`EnrichmentPipeline`/`dedupe_projects` 时注入；模块常量保留为默认值（直接调用/单测不变）。README + `.env.example` 已记录。
- DeepSeek 模糊分类 + LLM 缓存：代码在，缺 key 未实跑验证（Phase 3）。

### 运行模式（批量，不是逐项目跑）
> fetch 之前不知道有哪些项目，所以是**广撒网批量**，不是"指定一个项目跑一次"。
- `fetch`：**批量 + 增量** —— 按"数据源 + 起始日期"一次拉一批进 SQLite；重跑只拉新记录
- `classify`：**批量** —— 对所有未分类项统一分类
- `enrich`：**定向/过滤**（建议）—— 补全最费钱（浏览器/查询），只对关心的子集跑，如 `--category PUBLIC_WORKS` 或只补高置信度项
- `export`：**过滤** —— 按 category / county 筛
- 典型用法：广撒网 fetch → 全量 classify → 只对感兴趣类别 enrich → 过滤导出

---

## 最终交付：Excel 字段 & 联系方式硬要求（核心）

**硬要求**：主表每行**必须含邮箱或电话（≥1）**，否则无价值。**以公司为主体**（公司 info@ / 主机电话即可，能到人更好）。
政府原始数据几乎不带联系方式 → 靠 enrich 补全 → **enrich 是交付闸门，export 前必跑并硬过滤**。

**主表列集（精简，每个 category 一个 sheet）**：状态(🆕新增/已有) · 项目描述 · 日期 · 公司名 · 邮箱 · 电话 · 首次发现日期 · 来源链接
（category 用 sheet 区分；执照号/地址/置信度/联系人姓名等留在 SQLite，需要时再加列。）

**无联系方式的行** → 进单独 `待补全 (Pending)` sheet，不丢、日后可再补。

**两条拿联系方式的路径**：
- 路径 A — RFP/招标（SAM.gov 等）：数据**自带发标机构联系人**（姓名/邮箱/电话）→ 直接满足。
- 路径 B — Permit 类（remodel/motel/TI）：只有公司名 → CSLB 反查电话 + 官网抓 info@。含"公司名+CSLB执照号"的记录价值最高。

---

## 刷新 / 增量模型（每次更新表单怎么变）

> 组合"只要最近2个月新数据" + "累积全部 + 新增高亮"——两者管不同环节，不冲突：

- **摄入窗口**：`lookback_days`（默认 60）—— fetch 只在源头拉**最近2个月发布的新项目**，不翻很老的历史（窗口按 permit 签发日 / RFP 发布日，可配置）
- **保留策略**：**累积全部** —— 已进库线索不随窗口丢弃，表像一个不断长大的线索库
- **增量**：每个源记 `watermark`（上次拉到的最新时间）；下次 run 只拉比它新的；classify / enrich **只处理新增或变化项**（省 DeepSeek 与查询成本）
- **新增高亮**：每次标出"本次首次出现"的行
- **去重追踪**：`首次发现日期(first_seen)` + `状态(🆕新增 / 已有)`；可选在 SQLite 存 `已联系` 标记、跨次导出保留，避免重复联系同一线索
- **效果**：每次只并入最近2个月的新鲜项目，老线索保留、本次新增高亮、带首次发现日期

---

## 项目分类（category 枚举）

| Enum | 含义 |
|------|------|
| `PUBLIC_WORKS` | 公共工程 / 基建 |
| `COMMERCIAL_TI` | 商业开发 / tenant improvement |
| `GC_SUBCONTRACT` | 总包商分包机会 |
| `RESIDENTIAL_REMODEL` | 住宅装修 / remodeling |
| `HOSPITALITY_REMODEL` | motel / hotel remodeling |
| `RESTAURANT_RETAIL` | 餐厅 / retail |
| `OFFICE_LAB` | office / lab 项目 |
| `OTHER` | 其他可跟进项目 |

---

## ★ Phase 1.5 — 湾区行政区 & 数据源清单（city 覆盖）

> 这是"多源并行"的前置工作：把湾区每个 county + city 映射到它的数据源。
> 产物：`config/jurisdictions.yaml`，每条记录字段：
> `name, county, type(city/county), open_data_api, permit_system, procurement_portal, access(api/browser/none), urls`

### 9 个 County
- [x] **San Francisco** — DataSF Socrata（已接，含 contacts）
- [x] **Marin** — `data.marincounty.gov` Socrata `mkbn-caye`（已接，承包商在记录上）
- [ ] Alameda · Contra Costa · Napa · San Mateo · Santa Clara · Solano · Sonoma — 待补
- [ ] 每个 county 找：county-level permit / planning 数据、招标门户（很多 county 用 PlanetBids）

### 调研结论（2026-05-28，实测 Socrata catalog + ArcGIS）
> 重要：dataset 发现是真正的瓶颈，已实测的别再重复试。
- **Santa Clara County** `data.sccgov.org`：是 Socrata，但**只有 body art / 化粪车许可，无建筑许可**。
- **南湾各市**（San Jose / Santa Clara 市 / Sunnyvale / Cupertino / Palo Alto / Mountain View）：**都不是 Socrata**（404）。许可主要走 Accela / EnerGov 门户。
- **Sonoma County** `data.sonomacounty.ca.gov`：有 `88ms-k5e7` Construction Permits，但**无 contractor 字段**（全是无联系人线索，不满足硬要求，暂不接）。
- **San Mateo County `data.smcgov.org` / Berkeley / Oakland**：Socrata 在，但**无建筑许可数据集**。

#### ★ ArcGIS 复查（2026-05-28，之前只查了 Socrata，遗漏了 ArcGIS FeatureServer）
> 用 `arcgis.com/sharing/rest/search` + 各市 GIS server 逐城实测。南湾**并非全部 Tier 2**——发现一个可用官方 API：
- ⚠️ **Campbell（南湾 Santa Clara 县城市）**：官方 ArcGIS FeatureServer `services7.arcgis.com/RDyUffIeciKdYmX2/.../CampbellPermits_ActiveBuilding`，约 3,972 条、实时（最新 2026-05-22）。**但实测（2026-05-29，270 条 60 天窗口）：无承包商字段。** `ProjectName` 其实是**业主/申请人**——承包商代拉的少数商业件才是公司，绝大多数是房主姓氏（"New Heat Pump-O NEILL"/"...- Chou"）。231 条里仅 18 条像公司,且多为**业主 LLC**（PRUNEYARD REGENCY LLC 等),非承包商。唯一人contact 是 CaseManager（市政府员工）。→ **不满足联系方式硬要求,与 Sonoma 同类,Campbell 暂不接为正式源。** **ArcGIS 适配器已建好且通用**（`sources/arcgis.py`，fixture 测试覆盖），留给将来「有承包商字段且活跃」的 ArcGIS 城市用,加城市只改 yaml。
- ⚠️ **San Jose**：仅找到 **SDSU 学术快照** `Parcels_with_Active_Building_Permits_in_San_Jose`（7,317 条，有 APPLICANT/OWNERNAME/CONTRACTOR 字段），但**冻结在 2024-07-04，不再更新**，不适合做持续线索。官方 San Jose 许可仍走 Accela（SJPermits），无干净 API。
- ❌ **Sunnyvale**：`gis.sunnyvale.ca.gov/.../EnerGov/PermitSystemMap_ArcMap/MapServer` 现已 404（下线/加密）。
- ❌ Santa Clara 市 / Cupertino（仅 EV_PV）/ Mountain View / Palo Alto / Milpitas / Los Gatos / Saratoga（仅 tree permits）/ Morgan Hill / Gilroy：未找到可用 building-permit FeatureServer。
- SCC Planning `PlanningOfficeDataService2`：全是 zoning/边界 GIS + 规划 file 号，无承包商联系，不出线索。
- 当前覆盖南湾的源：**SAM.gov 联邦 RFP**（Moffett 94035 等）+ **Campbell ArcGIS**（待接）。

### ~101 个 City（建制城市）—— 分批调研，先大后小
- [ ] **Tier 1（有 Open Data API）**：实测下来湾区**有建筑许可 API 的极少**（目前只确认 SF + Marin 县级）。加新源用 `SocrataFieldMap`，只改 yaml。
- [ ] **Tier 2（Accela / EnerGov / eTRAKiT permit 门户，浏览器或半结构化）**：南湾（San Jose/Sunnyvale/Cupertino/Santa Clara/Milpitas 多用 Accela）+ Fremont、Hayward、Concord、Richmond、Redwood City、Daly City、Santa Rosa、Vallejo、Fairfield… → 记录系统类型 + 入口 URL。**需新建 Accela/浏览器适配器（未做）**。
- [ ] **Tier 3（仅 PDF/HTML 公告，最后做）**：其余小城市 → 记录公告页 URL
- [ ] 对每个 city 标注 `access` 字段（api / browser / none），驱动后续 adapter 选择
- [ ] 招标平台单独建一份映射：哪些 city/county 用 PlanetBids / DemandStar / OpenGov / Bonfire / ProcureNow

> 提示：完整 101 城清单本身是一个调研任务，建议先把 Tier 1（有 API 的）跑通，再逐 county 补 Tier 2/3。

---

## Phase 0 — 脚手架
- [x] `pyproject.toml` + venv + 依赖（typer / httpx / pydantic / openpyxl / pandas / openai / rapidfuzz / pydantic-settings / rich）
- [x] `.env.example`（`DEEPSEEK_API_KEY`、`SAM_API_KEY`…）；`README.md`
- [x] 项目骨架 + `config.py`（读 `.env` + `config/sources.yaml` + `config/jurisdictions.yaml`）+ 日志
- [x] `compliance/politeness.py`：robots.txt 检查、每域名限速、本地 HTTP 缓存、可识别 UA

## Phase 1 — 核心模型 + 存储
- [x] `models.py`：`RawRecord` / `Project` / `Company` / `Contact`（含 category 枚举；Project 含 `first_seen` / `last_seen` / `exported_at` 支撑增量与新增高亮）
- [x] `db.py`：SQLite 建表、`content_hash`、去重键、upsert

## Phase 2 — 数据源 adapter（API-first，多源并行）
- [x] `sources/base.py`：`BaseSource` 接口 `fetch(since) -> Iterable[RawRecord]`
- [x] `sources/socrata.py`：通用 Socrata 适配器，按 `jurisdictions.yaml` 里 Tier 1 城市的 dataset 拉 building permits / planning applications
  - [x] 已支持 DataSF Building Permits + Building Permits Contacts join。
  - [x] **已抽象字段映射**：`SocrataFieldMap`（record_id/permit_number/description/address/project_date/city/company_*），类改名 `SocrataPermitsSource`，加城市改 yaml 不改代码。支持两种承包商来源：① 独立 contacts dataset join（SF）② 许可记录自带 contractor 字段（县级）。fixture 测试覆盖两种。
  - [x] **已接入并实时验证 Marin County**（`data.marincounty.gov` / `mkbn-caye`）：县级数据集，contractor + license 在许可记录上（路径 B）；`date_field=most_recent_issued_received_date`（issued_date 常为空），`city` 映射 `city_town`。实跑拉到 Novato/Tiburon/San Rafael/Mill Valley 等真实湾区线索。
  - [ ] 继续补更多有 API 的城市/县。**调研发现**：data.smcgov.org、data.cityofberkeley.info、data.oaklandca.gov 无建筑许可数据集；Sonoma `88ms-k5e7` 有许可但**无 contractor 字段**（全是无联系人线索，不满足硬要求，暂不接）。dataset 发现本身是 todolist 标注的渐进调研任务。
- [x] `sources/samgov.py`：SAM.gov opportunities v2 API（需**免费**注册拿 `SAM_API_KEY`）—— **保留自带的发标机构 POC（姓名/邮箱/电话）**，路径 A 直接出联系方式；官方 API 跳过 robots，保留限速+缓存；fixture 测试已覆盖。无 key 批量 CSV 下载暂未做。
- [x] 用配置驱动：新增一个有 API 的城市只改 yaml、不改代码（验证可配置性）
- [x] `cli fetch --source ... --since ...` 打通：原始记录入库 + 缓存

## Phase 3 — 归一化 + 分类（DeepSeek）
- [x] `pipeline/normalize.py`：各源字段 → `Project`
- [x] `pipeline/classify.py` 规则层：按 category 的关键词/正则先判明显项
- [x] `llm/client.py`：OpenAI 兼容客户端指向 DeepSeek（env 可切 provider），`llm/prompts.py`
  - [x] 已实现 `llm/client.py`。
  - [x] 已拆 `llm/prompts.py`（`classification_prompt`）。
- [ ] 模糊项交给 DeepSeek 分类 + 抽 subtags；LLM 结果缓存避免重复计费
- [x] `cli classify` 打通：写回 category + confidence

## Phase 4 — 跨源去重
- [x] `pipeline/dedupe.py`：地址归一化（canonicalize suite/unit，保留套号）+ 标题模糊匹配（rapidfuzz `token_set_ratio`）合并重复项目。保守双阈值（addr≥92, title≥72）：宁可漏并不可误并（丢线索比错并更糟）；同楼不同 suite 不并、同址不同 scope 不并。非破坏式 `projects.duplicate_of` 标记（不删行，可审计、可幂等重跑），enrich/export/report 过滤 `duplicate_of IS NULL`。`cli dedupe`，已串入 `run`（classify→dedupe→enrich→export）。单测 10 个 + 真实数据验证（2 组真重复，无误并）。

## Phase 5 — 补全（交付闸门：拿到公司邮箱或电话）
> 目标：尽量多的项目"以公司为主体"补到邮箱或电话；建议按 `--category` 定向跑省成本。
- [x] 路径 A：normalize 阶段就从 RFP/招标记录抽发标机构 POC → 写入 `Company.email/phone`（`_normalize_samgov` / `_company_from_poc`）
- [ ] `enrichment/cslb.py`：加州 CSLB 承包商执照查询 —— **无需账户**。无官方 API，走 ① 浏览器/AI agent 查公开页面 或 ② 下载 CSLB 公开批量执照文件（拿公司电话/地址）
  - [x] 已建 provider 框架。
  - [x] 已支持下载/复用 CSLB License Master CSV，并按纯数字 license 写回公开电话。
  - [x] address/status/classification 入库：匹配即写 companies.address/license_status/license_classification（无电话也写）。
- [ ] `enrichment/web.py`：公司官网联系方式抽取（info@ / 联系电话，遵守 robots.txt）
  - [x] 已建 provider 框架和 email/phone 提取函数。
  - [x] 已支持已知官网抓取：首页、常见 contact/about 页面、站内 contact/about 链接，带 robots.txt 检查、限速和缓存。
  - [x] 已支持保守的公司名 → 候选官网发现：生成少量候选域名，抓首页验证页面文本匹配后再抽联系方式。
  - [x] 引入 Scrapling `Selector` 作为解析增强：用 CSS（`a::attr(href)`、`get_all_text(ignore_tags=...)`）替代正则 HTML 解析抽 mailto/tel/contact 链接与可见文本；fetch 仍走 `PoliteHttpClient`，robots.txt、限速、本地缓存语义不变。
  - [ ] 更可靠的官网发现来源待评估（公开搜索/API/白名单目录），避免误匹配。
    - 实测发现：单 token 短缩写名（如 "GCI"）会误匹配到无关公司（gci.com=阿拉斯加电信）。已加保守护栏：单 token 且长度 < 4 不做域名猜测发现（`website_candidates`）。多 token / 长名不受影响。更彻底的发现来源仍待评估。
  - [x] **已用真实数据验证**（2026-05-28 live run）：Marin Pending 3/11 补到联系方式（Semper Solaris / Rocky Hill Electric / USA Bath），DataSF 另发现 Architects SF / Wilko Builders；遵守 robots/限速/缓存。
- [x] `enrichment/browser.py`：从官网抓**公开**信息（`--browser` 开启，ToS 敏感）
  - [x] 已建 opt-in provider 框架。
  - [x] 用 Scrapling `DynamicFetcher` 实现 opt-in browser enrichment：渲染 JS contact 页、复用 web.py 的 CSS 抽取；robots/限速走 `PoliteHttpClient`；不绕登录/付费墙/CAPTCHA。浏览器内核为可选依赖（pyproject `[browser]` extra = `scrapling[fetchers]`，再 `scrapling install`），未装时优雅 skipped。fetcher 可注入，单测覆盖（无需真实浏览器）。
- [x] `cli enrich [--browser] [--category]` 打通；记录每条联系方式来源 + 覆盖率统计
  - 当前会记录 enrichment attempts；真实补全 provider 仍待接入。

## Phase 6 — Excel 导出
- [x] `export/excel.py`：精简列（状态/项目描述/日期/公司名/邮箱/电话/首次发现日期/来源链接），按 category 分 sheet，来源链接超链接、**本次新增行高亮**、bid 截止日期高亮
  - [x] 已完成精简列、category sheet、来源链接、新增行高亮。
  - [x] bid 截止日期：新增「投标截止」列 + 红色高亮（来自 SAM `responseDeadLine`→`bid_deadline`）。
- [x] **硬过滤**：有邮箱或电话的进各 category sheet；无的进单独 `待补全 (Pending)` sheet
- [x] Summary sheet：各类别计数 + **联系方式覆盖率**（按县/类别）
- [x] `cli export --out leads.xlsx [--category ...]` 打通

## Phase 7 — 编排 + 收尾
- [x] `cli run --sources ... --lookback-days 60`：串联整条管线 + 增量运行（Socrata 用 per-source watermark；SAM 无升序排序 → 不存 watermark，靠 lookback 窗口 + DB 去重）
- [x] `cli report`：生成新增项目、联系方式覆盖率（总+分类别）、高价值线索、RFP路径A、Pending 待补全摘要（`report.py`）；`run` 末尾自动打印。
- [x] **本地基础（不依赖 OpenClaw）已建**（2026-05-28，OpenClaw 尚未连接，仅做本地半）：
  - [x] **最新 Excel 固定指针**：`export`/`run` 额外复制到 `data/latest-leads.xlsx`（config `latest_excel_path`），供将来「要Excel文档」固定返回。
  - [x] **`cli notify`**：`notify.py` 通知抽象层（`Notification` + 可插拔 `NotificationChannel`）+ 本地 channel（`StdoutChannel`/`FileChannel`），Summary 复用 `report.py`，含失败模式文案。`dispatch` 单 channel 失败不阻断其他。OpenClaw 以后只是再加「微信 channel」。
  - [x] **launchd 周定时生成器** `cli install-schedule`：写出 `scripts/weekly-run.sh`（`caffeinate -i` 防休眠 + run→notify）+ `com.projectintel.weekly.plist`（默认周一 8:00，`plutil` 校验通过）。**不自动加载**（改系统需用户确认），打印 `launchctl load` 与 `pmset repeat wakeorpoweron MON 07:55` 唤醒指令。
- [ ] **OpenClaw 集成（待 OpenClaw 连接后做）**：白名单命令「跑数据」「要Excel文档」「每周更新一次」+ 微信 channel
  - “跑数据”：OpenClaw 调用固定 `projectintel run`，不透传用户输入到 shell
  - “要Excel文档”：返回 `data/latest-leads.xlsx`
  - 运行中/完成/失败状态通过微信简短提醒（接 `notify` 的微信 channel）
  - “一周通知一次/每周更新一次”：默认周一 8:00（已有 launchd 生成器；微信侧设置/暂停待接）
- [x] pytest（录制 API fixture，避免真实网络）
  - [x] 已有基础单元测试：分类、SQLite 幂等、Excel 硬过滤。
  - [x] DataSF/Socrata adapter fixture（`tests/test_sources_socrata.py`）。
  - [x] SAM.gov adapter + normalize + 湾区过滤 + 429 优雅停止 fixture（`tests/test_samgov.py`）。
  - [x] 湾区地域过滤单测（`tests/test_geo.py`）。
  - [x] normalize 公司选择 + report 摘要单测（`tests/test_normalize.py`、`tests/test_report.py`）。
- [x] README 使用文档 + 合规说明；本地定时运行（launchd）
  - [x] README 已更新：完整管线（fetch→classify→dedupe→enrich→export）、多源/run、CSLB、官网+Scrapling 抽取、opt-in 浏览器（含安装）、去重、notify + launchd 定时、合规说明、增量/backfill 说明。

---

## 合规要点（贯穿全程）
- API-first：能用官方 API / 开放数据就不爬页面
- robots.txt 必查；保守限速；UA 带可联系信息；全程缓存
- 浏览器自动化只用于无 API 且 ToS 允许的页面；不绕 CAPTCHA、不抓登录/付费墙
- 联系方式只取已公开发布来源；DeepSeek 只收项目描述文本做分类

## 验证（end-to-end）
1. [x] Phase 2 后：`fetch --source datasf` → SQLite 有原始 permit 记录
2. [x] Phase 3 后：`classify` → 每条 Project 有 category + confidence，抽查核对
3. [x] Phase 5 后：`enrich` → 主表候选行拿到公司邮箱或电话；打印联系方式覆盖率
   - CSLB 电话补全已可用；官网 email/phone 抽取已用 Scrapling `Selector` 增强；浏览器补全仍待实现。
4. [x] Phase 6 后：`export` → 主表每行都有邮箱或电话、按 category 分 sheet、来源链接可点；无联系方式的在「待补全」sheet
5. [x] 全流程：`run` → 一条命令从抓取到 Excel；重跑验证增量与去重

## 风险 / 待定
- 各 Socrata 门户 dataset id 与字段不一致 → 用 yaml 字段映射
- 多数中小 city 无 API（Accela/EnerGov）→ 归到 browser 阶段，别阻塞 Tier 1
- SAM.gov 需免费注册拿 API key（或走无 key 批量下载）；CSLB 无需账户、无官方 API（浏览器/批量文件）
- 完整 101 城清单是渐进任务 → 先 Tier 1 跑通再逐 county 补全
