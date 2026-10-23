# 梗雷达 · meme-intake

AI萌娘梗百科（aiher.ai）的热门梗收集后端。参照麦式情报站（AI HOT）的架构哲学，
用 Python 标准库 + SQLite 实现的单机版：**采集 → 评分 → 候选队列 → 发布闸门 → 合规发布**。
发布闸门有两道：`approve`（人门）与 `auto`（机门），共用同一套校验。

与既有设施的关系：

- 复用 `tools/build.py` 的 `validate()` 作为条目 schema 的**唯一真源**——发布器产出的
  每个条目都先过它的校验，不存在第二套规则。
- 与 `services/pageviews.py`（浏览量统计）平级共存，互不依赖；数据库独立
  （`services/meme-intake/data/intake.db`）。

## 流水线

```
六路信源（AI HOT API / 百度热搜 / 抖音热点 / 机核 RSS / 少数派 RSS / editorial）
        │  每源独立 try，单源挂不掉整轮（receipt 逐源记录）
        ▼
   预筛（无 URL/标题丢弃）→ 评分（radar.py）→ 指纹去重（sha1(source+url)）
        │
        ▼
   candidates 表（status=pending）── 未过阈值直接丢弃，不入库
        │
        ▼
   发布闸门（两道，校验完全相同）：
     人门 approve <fp>  ── 线索卡/草稿皆可，人看过后放行
     机门 auto         ── 仅"写完了的梗草稿"且过全部检查才放行
        │
        ▼
   语料预查重（id / 同角色同标题，对照站点索引与已发布条目）
        │
        ▼
   content/memes/<id>.json（build.py validate 通过才落盘）
        │
        ▼
   published 表记指纹 → 后续轮次同一 URL 永不重复入队
```

机门合同（`auto`，按序执行，任一不过即跳过并记理由，绝不半写）：

1. **只放行"梗化完成"的条目**——草稿必须带 `llm_entry` 且有正文。规则模式的
   线索卡（系统自贴"待人工核实"标签的引用存根）**永远只能人工发布**；
2. `llm_entry` 角色必须在白名单（否则合并会退化成线索卡，同样不放行）；
3. `meme_score ≥ --threshold`（默认 70，比入队阈值 30 严）；
4. 过 `build.py validate`（schema 唯一真源）；
5. 过语料预查重（id / 同角色同标题，含站点历史索引）。

## 评分模型（radar.py，纯规则、可解释）

```
meme_score = 100 × 源分 × 角色权重 × 信号权重 × 新鲜度 × 噪音折损
```

- 角色权重：标题/摘要命中百科角色 1.0，未命中 0.25（泛 AI 梗不扔）；
- 信号权重：梗词每命中一个 +0.15（上限 ×1.6）；
- 新鲜度：7 天半衰期；
- 噪音折损：财报/裁员/诉讼类词汇 ×0.6（折损不封杀——人事稿也曾出过名梗）；
- Codex 归 GPT 家族（与 build.py 一致）。
- `explain` 子命令随时回答"这条为什么进/不进队列"。

## 发布纪律（不可绕）

1. **机门（`auto`）不放行任何"未梗化"内容**。规则模式产出的是线索卡
   （kind=community，出处待补），不是成品梗——系统自己给每条贴着"待人工核实"，
   机器无权把它转正；编造故事、虚构出处一律不做。
2. LLM 模式（可选）产草稿；草稿不过 `build.py validate` 就丢弃回退线索卡，
   绝不写坏数据。机门只放行过了全部检查的**写完了的**草稿。
3. 两道门共用同一套校验（语料预查重 + validate），不存在第二套规则。
4. 版权与内容核实责任最终在人工——与投稿 PR 的纪律一致。机门放行的条目
   仍然要走上线流程（见下），公开仓库的推送与合并不由机器人代劳。
5. 机门落盘后数据库记账失败 = 硬停止（文件在盘上而队列不知道，继续跑会掩盖
   这个不一致），与 `approve` 同语义。

## 用法

```bash
cd services/meme-intake
python intake.py run                                  # 一轮增量采集
python intake.py list [--status pending] [--min-score 40]
python intake.py show <fp前10位>                       # 详情 + 为什么
python intake.py explain <fp前10位>                    # 只要评分解释
python intake.py approve <fp前10位> [--id 自定义id]     # 人工批准 → 写条目
python intake.py auto [--threshold 70] [--limit 50] [--dry-run]
                                                  # 机门：只放行写完了的草稿
python intake.py reject <fp前10位> --reason "理由"
python intake.py stats
```

`auto` 输出 JSON（`dry_run/threshold/scanned/below_threshold/published/skipped`，
每条跳过都带理由），可直接追加进日志 greppe。`--dry-run` 评估完整合同但不落盘、
不动队列——包括"同一轮内同角色同标题"这种只有真写才会暴露的冲突（靠内存计划
名单兜住）。自动 id 带信源前缀（`aihot-xxxxxxxxxx` / `gcores-xxxxxxxxxx`），
出处在公开 URL 里可见。

批准后跑：

```bash
python tools/build.py --check && python tools/build.py
```

## 持续更新

完整计划任务使用下方「完整自动发布」入口。`run` 仅采集，`auto/approve` 仅写本地内容。
任务注册需要明确的私密环境文件与数据库位置；`-WhatIf` 预览，`uninstall-task.ps1` 删除任务。

## LLM 梗化（可选）

设置三个环境变量即启用；任一缺失自动保持规则模式（线索卡）：

```
MEME_LLM_BASE_URL   OpenAI 兼容 base（如 https://api.minimax.chat/v1）
MEME_LLM_API_KEY    bearer key
MEME_LLM_MODEL      模型名
```

LLM 只生成草稿；草稿不过 `build.py validate` 就丢弃回退线索卡，绝不写坏数据。

**配了 key 之后机门才真正有东西可放**：`run` 会把 LLM 草稿存进候选的
`llm_entry`，`auto` 于是可以放行高分写完了的草稿——这才是"不用等点头"的
完整形态。没配 key 时机门放行数为 0（全部候选都是规则模式线索卡），
这是设计使然：机器不把"刚捞到的引用"当成"写好了的梗"。

链路行为已被测试锁死（无真 key，urllib 全 mock）：正常 JSON 草稿被提取、
markdown 代码块围栏被剥离、`{"skip": true}` 返回 None、垃圾响应/超时/畸形
payload 返回 None 且不炸、三个环境变量不全时 `is_configured()` 为 False 且
`draft()` 不发起网络请求。`approve` 合并 LLM 草稿后仍必须过
`build.py validate`——合并出非法条目时拒绝发布，坏数据不落盘；`codex` 在
合并时归并到 `gpt`（与规则模式一致）；角色不在白名单的草稿整份忽略，
回退线索卡；LLM body 顶掉规则模式的正文后，原始线索链接会重新挂回
（body 里没有该 URL 时）。

## 测试

```bash
python -m unittest tests.test_meme_intake -v     # 70 项，网络全部 mock
```

覆盖：角色门控、噪音折损、Codex 归并、退化响应停页、全分页、指纹去重、
发布门（schema 校验 / 坏 id / 重复批准 / 保留设备名 id / --force 备份 /
损坏草稿降级）、语料预查重（同角色同标题拒绝）、RSS 解析（RSS 2.0 / Atom /
坏条目跳过 / 空 feed / 不可解析降级 / 取文失败降级 / 默认分级校准 /
per-source 分级可配置 / 具名实例分级）、热搜榜适配器（百度置顶与榜尾折算 /
坏卡跳过 / 降级；抖音 position 折算 / event_time 转 ISO / URL 编码 /
脏 position 回退 / 降级）、热搜折算函数（榜首 95 / 榜尾 40 / 置顶 100 /
单条榜 / rank 越界钳位 / 线性衰减）、cmd_run 六源同跑 / 单源挂不掉整轮 /
崩溃留 ABORTED 回执 / 非有限阈值拒绝、敌意 payload（epoch 日期 / 非数字
score / None 标题不炸）、WAL 与 busy_timeout、DB 落盘后锁失败的告警、
LLM 草稿链路（JSON 提取 / 围栏剥离 / skip / 垃圾 / 超时 / 畸形 payload /
缺 key / 三变量不全不发请求）、LLM 合并后的 schema 门（非法合并拒绝 /
codex 归并 / 未知角色回退 / 原始线索重新挂回）、机门合同（写完了的草稿
高分放行 / 线索卡永不自动发布 / 低于阈值不入报告 / 非法合并拒绝 /
同角色同标题跳过 / 干跑不落盘且不动队列 / 干跑内存计划兜住同轮重复 /
未知角色草稿忽略 / 信源前缀 id / 落盘后记账失败硬停止）。测试用
`MEME_INTAKE_DB` 指向临时库、`MEMES_DIR` 指向临时目录，不碰生产数据。

## 信源适配器（sources.py）

| 适配器 | 输入 | 状态 |
|--------|------|------|
| `AIHotSource` | 情报站公开 API（顺序分页 + 退化校验 + 退避） | 默认启用 |
| `EditorialSource` | `content/editorial/*.json`（自家角色页） | 默认启用，源分 0 |
| `BaiduHotSource` | 百度实时热搜 JSON（`data.cards[].content[].content[]`，content 为组列表） | 默认启用，rank 折算 |
| `DouyinHotSource` | 抖音热点榜 JSON（`data.word_list[]`） | 默认启用，position 折算 |
| `GcoresRSSSource` | 机核 RSS（`gcores.com/rss`） | 默认启用，tier 60 |
| `SspaiRSSSource` | 少数派 RSS（`sspai.com/feed`） | 默认启用，tier 50 |
| `RSSSource(url, name, source_score=…)` | 任意 RSS 2.0 / Atom | 协议层，按需实例化 |
| 备选：IT之家 RSS（tier 45）/ 阮一峰 atom（tier 40） | ithome.com/rss、ruanyifeng.com/blog/atom.xml | 未接入，仅登记 |

所有适配器返回同构 RawItem（`source/url/title/summary/category/
source_score/published_at/discovered_at`）。容错纪律：单项坏跳过或留空、
不拖垮整批；整个源不可用按退化处理（receipt `degraded=1`）；`cmd_run`
里每个源独立 try——单源挂不掉整轮，receipt 逐源记录状态。

**source_score 分级**（radar 会把 score/100 钳进 [0,1]，故必须预归一化
到 0–100，平台原始计数直传会被钳成满分）：

| 源 | source_score | 理由 |
|----|--------------|------|
| 机核 RSS | 60 | 二次元/游戏文化社区，梗密度最高 |
| 少数派 RSS | 50 | 中性，对齐情报站实测均值 ≈47 |
| IT之家 RSS（备选） | 45 | AI 新闻密度高但梗浓度低 |
| 阮一峰 atom（备选） | 40 | 技术周刊，梗信号弱，保底 |
| editorial | 0 | 有意设计：只作角色覆盖参考，不产候选 |
| 百度/抖音热搜 | `hotsearch_score(rank, n, is_top)` | 榜首 95 线性衰减到榜尾 40、置顶 100、单条榜 95、rank 越界钳位 |
| 情报站 API | 服务端自带 0–100 分 | 实测分布 32–62 |

## 已知边界（如实说明）

- AI HOT `window` 上限 7 天；更深历史不在本后端范围。
- 候选队列质量取决于规则词典，词典会随生态漂移——`explain` 就是为了让下一个人
  能看见"这条为什么进来"而保留的。
- **设计取舍（P2-12）：被 reject 的候选永不重新入队。** `add_candidate` 按
  source+url 指纹 INSERT，rejected 行仍在表内，同 URL 再采集会撞主键被跳过——
  即"人工否过的线索不会因下轮采集复活"。好处是人工闸门权威，代价是热点变质
  （当时不该收、一周后该收）时需要人工清 reject 记录或换 URL 重新发现。
- 百度/抖音热搜词多为纯社会热点，多数会被 0.25 角色权重自然滤掉；这是特性
  不是缺陷——队列宽而不洪水，过关主力是"标题命中百科角色名"。
- 抖音 `trending_list`（编辑置顶）未接入：它不是排序榜，接入需另定折算口径。
- **未配 LLM key 时，机门放行数恒为 0**：队列里全是规则模式线索卡，全部会被
  "仅人工可发布"跳过。这是设计使然不是故障——配 key 后 `run` 产出带
  `llm_entry` 的草稿，机门才开始有东西可放。
- 机门放行 ≠ 上线：条目落盘 `content/memes/` 后，仍需 `tools/build.py` 构建
  与 Git 推送/PR 合并才到公网站点（aiher.ai）。经站点所有者授权后，可运行下述完整流水线；原始素材必须保留出处，无法确认的内容留在队列。


## 完整自动发布（2026-10-23）

使用独立 automation checkout，避免修改编辑者的工作目录。环境文件只保存在本机，
`MEME_LLM_MODEL` 支持 OpenAI 兼容接口上的 MiniMax-M3.1-Flash。

```powershell
python services/meme-intake/pipeline.py --env-file <private-env-file> --database <private-data-dir>/intake.db
```

每轮采集六类来源；先对已有 URL（包括 pending/rejected、跨来源和常见跟踪参数）去重。
最多处理 3 条高分候选（`--limit` 上限 5），模型写稿后另发一次审稿请求。无通过项就不发布。
外部文本被标记为不可信资料，模型没有工具权限，来源链接由程序保留。审稿不能证明事实正确；
自动稿明确标注虚构二创，争议或无法确认内容应拒绝。对严重内容争议可关闭计划任务并人工处理。

流水线写入持久化计划、原子写文件、完整测试/构建/链接检查，创建受保护 PR，
仅 `Content validation` 成功且提交未变化时合并。沿用站点既有 `site` 分支部署。
公网 `release.json` 与合并提交匹配，且目标文章完整 HTML 与本地一致，才记录 `live`。
失败重跑从 receipt 继续，不重新写稿；无需 `--force`。`published` 是旧数据库字段，
只代表内容文件已写出，**实际公网上线状态以 `pipeline.json.stage=live` 为准**。

`--prepare-only` 仅产生本地内容，用于首次部署的人工预览。不能在日常自动任务中使用。
普通 `intake.py auto/approve` 也是本地内容操作，不提交 Git。

Windows 注册入口（需已授权且 `gh` 登录有仓库权限）：

```powershell
./services/meme-intake/install-task.ps1 -EnvFile <private-env-file> -Database <private-data-dir>/intake.db
```

任务每 10 分钟接续尚未完成的 PR/部署；完成或没有合格候选后，20 小时内不再采集。
任务使用当前已登录的 Windows 用户，离线/退出登录期间无法运行；恢复登录后继续。
日志、数据库和 receipt 均不进 Git。关闭任务可使用 Disable-ScheduledTask；旧版本回滚走 Git revert，
保留数据库及 receipt 便于恢复。测试默认封锁真实网络与本机模型配置。

### 管理员合并的显式授权

仓库启用人工 review 时，默认合并仍受其约束。只有站点所有者明确授权自动任务跳过人工 review 后，
才在本机私密环境文件设置 `MEME_ALLOW_ADMIN_MERGE=1`。该配置使用当前管理员权限合并，
不修改分支保护；仍要求 Content validation 成功、PR head 未变化、main 没有分叉或领先。
撤销时删除该变量即可恢复普通合并。此配置不会把模型审稿变成人类事实核验。


## 趣味编辑门（2026-10-23）

热度只影响素材召回，不能直接触发发布。候选评分30以上进入选题池，优先查看含梗线索的素材；
每轮最多新审8条，最多写/审3篇，不设最低发布数量。

1. 原素材须已有具体笑点：真实社区玩笑、意外行为、反差原话等。普通发布、参数、排名、
   融资、登记新闻即使热度很高也留在线索池。模型必须逐字引用原素材中的证据，并说明反差。
2. 成稿须有落点、角色反应和短阅读节奏。目标120—350字，自动发布正文上限800字。
   禁止只给资讯换人物名字、硬套小剧场、重复对白或末尾解释为什么好笑。
3. 独立审稿同时返回事实边界、笑点、角色契合、精简、非新闻换皮五项判断，
   并从正文逐字引用笑点。缺字段、空返回、虚构引用、任何一项不成立，都不发布。
4. 选题与成稿意见保存在本机候选draft中；策略版本变化自动使旧审核缓存失效。
   请求失败不缓存为永久拒绝。没有合格内容的轮次保持零发布。

编辑判断存在主观性；这些检查是保守筛选与可追溯依据，不保证每位读者都会觉得好笑。
合成校准用例只用于测试，不进入公开内容。
