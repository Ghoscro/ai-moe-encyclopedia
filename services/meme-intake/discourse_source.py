"""Bounded public Discourse originals; never fetch pagination or image content."""
import datetime as dt
import hashlib
from html.parser import HTMLParser
import re

_URL = re.compile(r"https://(www\.nodeloc\.com|linux\.do)/t/topic/([1-9][0-9]*)(?:/([1-9][0-9]*))?/?")


def supported(url):
    return isinstance(url, str) and _URL.fullmatch(url) is not None


class _Body(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if self.skip:
            return
        if tag in ("p", "div", "br", "li"):
            self.parts.append("\n")
        if tag == "blockquote":
            self.parts.append("\n【本楼引用内容开始，非本楼作者原话】\n")
        # img/alt/title are not evidence of reading the attached image.

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1
        elif not self.skip and tag == "blockquote":
            self.parts.append("\n【本楼引用内容结束】\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _stamp(value, now):
    if not isinstance(value, str):
        raise ValueError("missing creation date")
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp > now:
        raise ValueError("unknown or future creation date")
    return stamp.astimezone(dt.timezone.utc).isoformat()


def evidence(url):
    import community, origin
    if not supported(url):
        return None
    host, topic, requested = _URL.fullmatch(url).groups()
    selected_number = int(requested or 1)
    try:
        raw = community.get("https://" + host + "/t/" + topic + ".json", True)
        if not isinstance(raw, dict) or type(raw.get("id")) is not int or raw["id"] != int(topic):
            return None
        title = raw.get("title")
        rows = raw.get("post_stream", {}).get("posts")
        if not isinstance(title, str) or not title.strip() or not isinstance(rows, list):
            return None
        now = dt.datetime.now(dt.timezone.utc)
        posts = []
        seen = set()
        for row in rows[:20]:
            if not isinstance(row, dict):
                return None
            number = row.get("post_number")
            if type(number) is not int or number < 1 or number in seen:
                return None
            seen.add(number)
            if "topic_id" in row and row["topic_id"] != int(topic):
                return None
            author, cooked = row.get("username"), row.get("cooked")
            if not isinstance(author, str) or not author.strip() or not isinstance(cooked, str):
                return None
            stamp = _stamp(row.get("created_at"), now)
            parser = _Body()
            parser.feed(cooked)
            body = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
            if not body:
                body = "【本楼无可读正文；附件图片未读取】"
            posts.append({"post_number": number, "author": author, "published_at": stamp,
                          "text": body[:6000], "url": "https://" + host + "/t/topic/" + topic + "/" + str(number)})
        selected = next((post for post in posts if post["post_number"] == selected_number), None)
        if selected is None:
            return None
        lines = ["原标题：" + title, "证据范围：公开响应前20楼以内的文本，未读取附件图片或额外分页。题名中的模型归属为发帖者陈述，未验证代理后端。"]
        for post in posts:
            lines.extend(["\n【第%d楼】" % post["post_number"], "作者：" + post["author"],
                          "原创建时间：" + post["published_at"], "原楼链接：" + post["url"], "正文：\n" + post["text"]])
        text = "\n".join(lines)
        return {"version": origin.VERSION, "url": url, "text": text,
                "published_at": selected["published_at"], "author": selected["author"],
                "posts": posts, "evidence_scope": "public_discourse_post_text_only",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
