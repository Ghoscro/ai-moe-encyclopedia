# 每个基座每日内容计划

北京时间每天每个基座目标1篇；14个独立槽位：Claude、豆包、GPT龙娘、DeepSeek、Kimi、GLM、Grok、千问、Gemini、Mistral、MiniMax、文心、Muse Spark、混元。Codex并入GPT；Sudo与AI陪伴不是基座配额。

只由stage=live且verified_at的回执计数，去重并扣除撤回。PR、草稿不算完成。当日已完成角色让出审核位置，批次上限3篇、每批1篇/角色，原有20小时全站发布冷却改为1小时。新闻采集仍20小时一次；作者实验档案每天按角色补充，单轮最多3家、每家最多查看2个未入库原文，避免一次抓取拖垮小服务器。

作者档案收录分30只是进入审核的发现优先级，不代表趣味或事实认证。仍逐一通过精确原文引用、原出处判断、独立四格审核、内容测试、PR检查与公网卡片核验。未取得来源、没有笑点、角色不匹配均不得上线。

原文日期写入经典新编条目，禁止伪装今日事件。来源URL去重，已有文章不换标题重复发布。候选以数据库持久保存作为待审素材储备；未审核不称成稿库存。

runtime/daily-coverage.json逐家列出live/target/pending_candidates/status；runtime/archive-discovery.json记录每家来源补充结果。complete、awaiting_review、source_gap分别表示完成、有待审素材、缺来源。状态不保证每天都有合格产出；目标缺口必须如实报告，不用普通新闻或编造故事补数。

回滚：保留旧Git提交与私有数据库/流水线回执备份；原有Docker runner和同步timer不变，不增新定时器。代码失败保持发布状态可恢复，禁止重置已有发布。
