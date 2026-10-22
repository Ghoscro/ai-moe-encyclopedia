# AIHER 页面浏览量

`pageviews.py` 是 Python 标准库实现的同源计数服务，通过 Unix socket 接入 Nginx。`aiher-stats.service` 提供受限运行配置；生产数据库存放在 `/var/lib/aiher-stats/`，独立于静态发布目录。

- `GET /api/stats?path=/claude/`：只读查询。
- `POST /api/stats`：JSON `{ "path": "/claude/", "event": "浏览器生成的 UUID" }`，要求 Origin 为 `https://aiher.ai`。返回累计、北京时间当日、本页浏览与起始日期。
- 仅接受实际发布的 HTML 路径，不保存 URL 查询串、IP、设备指纹或 cookie。一次可见文档打开计一次，重试复用 ID；已知爬虫 UA 拒绝，但公开计数不能保证完全排除机器人或蓄意刷量。
- 随机事件 ID 保留 24 小时，容量上限 50,000 条；达到容量时返回 503，静态网站照常可用。每日每页只保存聚合计数。
- Nginx 需设置请求体、频率、并发限制，仅将 `/api/stats` 代理到 `/run/aiher-stats/http.sock`；服务端口不向公网开放。
- 停用时移除统计脚本和代理、停止服务即可；保留数据库便于恢复。任何数据库恢复须先停止写入并备份现场，禁止静态发布脚本删除或重建数据库。
- 数据备份使用 SQLite 在线 backup API 生成独立一致快照后校验，不直接复制运行中的 WAL 主文件。运维配置、恢复回执与数据快照保存在本机私有交付目录，不进入公开仓库。

测试：`python -m unittest discover -s tests -v`。Windows 上通过隔离 HTTP 测试监听的操作系统临时端口验证请求；生产入口强制使用 Unix socket。
