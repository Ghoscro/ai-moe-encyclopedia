# AI萌娘梗百科

[访问百科](https://mickerbook.com/meme/) · [投稿新梗](https://mickerbook.com/meme/contribute/) · [投稿指南](CONTRIBUTING.md)

记录这个 AI 时代的可爱日常。角色图鉴、经典梗、创作趣事与社区二创。

本仓库包含百科公开静态页面与角色插画，以及通过 Pull Request 投稿的内容机制。初始内容包含 8 位角色、2 个专题、604 条梗记录；跨专题收录分别计数。

## 怎么投稿

最方便的方式是打开[投稿页面](https://mickerbook.com/meme/contribute/)，填好内容后去 GitHub 提交 PR。也可以复制 `examples/new-meme.json` 到 `content/memes/<id>.json`，修改内容后提交。

不会使用 Git？可以先[提交新梗线索](https://github.com/Ghoscro/ai-moe-encyclopedia/issues/new?template=new-meme.yml)。

PR 会自动检查格式、重复内容和构建。维护者检查出处、版权与内容后合并。合并到 `main` 后，Actions 生成 `site` 分支的静态页面，网站定期拉取发布结果。首次外部贡献可能需要维护者批准运行 CI。

## 本地预览

需要 Python 3.11 或更新版本，无需安装第三方依赖：

```sh
python tools/build.py --check
python -m unittest discover -s tests -v
python tools/build.py
python tools/check_links.py
python -m http.server 8000 --directory dist
```

然后打开 `http://localhost:8000/meme/`。这是独立静态站点，部署在域名根目录；目录外的 MickerBook 主站与 AI 测试链接需要前往正式网站访问。

## 目录

- `site/`：已公开页面与素材。历史条目保留现有 HTML，修订历史条目可直接修改对应页面。
- `content/memes/`：新增条目，一梗一个 JSON 文件。
- `examples/`：投稿示例，不会发布。
- `tools/`：校验、构建与站内链接检查。
- `.github/`：投稿模板、内容检查与发布流程。
- `dist/`：构建产物，不提交到 main。

## 内容与权利

角色与插画为本站非官方二创形象。条目区分有出处趣事、待补出处的社区梗、明确虚构的原创日常。历史梗的具体核查状态保留在条目中。

公开仓库便于协作投稿；尚未授予整站代码、原创插画与文案的通用再许可。第三方名称、引用与素材的权利归原权利人，字体许可见对应资源目录。投稿授权范围见 [CONTRIBUTING.md](CONTRIBUTING.md)。

Created by Micker / Ghoscro. This public repository contains only the encyclopedia's public pages, assets and contribution tooling. Private user data, credentials, private memory and production infrastructure are excluded.
