"""The meme radar: scoring and rule-based understanding.

Pure rules, no network, no API key. An item enters the review queue when
its meme_score clears the threshold; `explain()` always shows why.

Scoring model (deliberately simple and auditable):

    meme_score = 100 * source_norm * w_character * w_signal * freshness * noise

- source_norm: the station's own 0-100 score, normalised.
- w_character: 1.0 when a encyclopedia character is named in the title,
  0.25 otherwise. Character-less items are general AI memes, not dead.
- w_signal:   1.0 + 0.15 * min(hits, 4) - meme vocabulary in title/summary.
- freshness:  exponential decay, half-life 7 days (the fetch window).
- noise:      0.6 when corporate/finance/legal vocabulary dominates -
               those stories rarely become memes, but are not excluded.

Gating lessons carried over from the dashboard pipeline: alias matching
runs on title AND summary, context words are checked, and every rule is
explainable. Model names require the model context; broad words need
company context. Chinese phrases are matched as plain substrings.
"""
import datetime
import re

# Encyclopedia characters (tools/build.py CHARACTERS). `codex` is excluded
# there and folded into the GPT family; same rule here.
CHARACTER_ALIASES = {
    'claude':    ['claude', '克劳德'],
    'doubao':    ['豆包', 'doubao'],
    'gpt':       ['gpt', 'chatgpt', 'openai', 'codex', '聊天gpt'],
    'deepseek':  ['deepseek', '深度求索'],
    'kimi':      ['kimi', '月之暗面', 'moonshot'],
    'glm':       ['glm', '智谱', 'chatglm'],
    'grok':      ['grok'],
    'qwen':      ['qwen', '千问', '通义'],
    'gemini':    ['gemini'],
    'mistral':   ['mistral'],
    'minimax':   ['minimax'],
    'ernie':     ['文心', 'ernie'],
    'muse-spark': ['muse spark', 'musespark'],
    'hy4-preview': ['hy4'],
    'sudo':      ['sudo'],
    'waifu':     ['ai 陪伴', 'ai陪伴'],
}

MEME_SIGNALS = [
    '梗', '玩梗', '破防', '名场面', '整活', '二创', '鬼畜', '表情包',
    '段子', '吐槽', '戏称', '外号', '拟人', '娘化', '爆火', '出圈',
    '热传', '迷因', 'meme', '上头', '抽象', '嘴替', '发疯', '精神状态',
    '电子宠物', '萌', '可爱', '翻车', '名场面', '玩坏', '整活',
]

# Corporate/finance/legal vocabulary: meme-poor territory (penalty, not ban).
NOISE_WORDS = [
    '招聘', '财报', '融资', '上市', '股价', 'ipo', '高管', '离职', '入职',
    '诉讼', '监管', '许可', '收购', '裁员', '纳税', '牌照',
]

DEFAULT_THRESHOLD = 30.0


def _norm(text):
    """Lower-cased text for matching; hostile payloads read as empty.

    Sources are third-party APIs: a title that arrives as None or a number
    must not explode scoring - it simply matches nothing.
    """
    return text.lower() if isinstance(text, str) else ''


def _item_text(item):
    return _norm(item.get('title', '')) + ' ' + _norm(item.get('summary', ''))


def match_character(item):
    """Return the character slug named in title/summary, else ''."""
    text = _item_text(item)
    for slug, aliases in CHARACTER_ALIASES.items():
        for alias in aliases:
            if alias.lower() in text:
                return slug
    return ''


def match_signals(item):
    text = _item_text(item)
    return sorted({s for s in MEME_SIGNALS if s in text})


def match_noise(item):
    text = _item_text(item)
    return sorted({s for s in NOISE_WORDS if s in text})


def _freshness(published_at):
    if not published_at or not isinstance(published_at, str):
        return 0.8
    try:
        when = datetime.datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        age_days = max(0.0, (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds() / 86400.0)
    except (ValueError, TypeError, AttributeError):
        return 0.8
    return 0.5 ** (age_days / 7.0)


def score_item(item):
    """meme_score plus the explanation of every factor."""
    character = match_character(item)
    signals = match_signals(item)
    noise = match_noise(item)
    try:
        source_norm = min(1.0, max(0.0, float(item.get('source_score', 0)) / 100.0))
    except (TypeError, ValueError):
        source_norm = 0.0  # a non-numeric score is a zero score, not a crash
    w_character = 1.0 if character else 0.25
    w_signal = 1.0 + 0.15 * min(len(signals), 4)
    fresh = _freshness(item.get('published_at', ''))
    w_noise = 0.6 if noise else 1.0
    total = 100.0 * source_norm * w_character * w_signal * fresh * w_noise
    return {
        'character': character,
        'signals': signals,
        'noise': noise,
        'meme_score': round(total, 2),
        'factors': {
            'source_norm': round(source_norm, 3),
            'w_character': w_character,
            'w_signal': round(w_signal, 3),
            'freshness': round(fresh, 3),
            'noise': w_noise,
        },
    }


def explain(item, scored=None):
    """Why this item is (or is not) queue-worthy. Kept in the shipped code:
    the dictionaries drift with the ecosystem, the next maintainer needs to
    see the reason, not just the number."""
    s = scored or score_item(item)
    bits = []
    if s['character']:
        bits.append('命中角色 ' + s['character'])
    else:
        bits.append('未命中角色（按泛 AI 梗处理，权重 0.25）')
    if s['signals']:
        bits.append('梗信号: ' + ', '.join(s['signals']))
    if s['noise']:
        bits.append('商业噪音折损: ' + ', '.join(s['noise']))
    bits.append('源分 {source_score} · 新鲜度 {freshness}'.format(
        source_score=item.get('source_score', 0), freshness=s['factors']['freshness']))
    return f"{s['meme_score']} 分 | " + '；'.join(bits)


def make_lead_card(item, scored=None):
    """Rule-mode understanding: a lead card, NOT a finished meme.

    The card keeps the original title/summary and records what a human
    editor needs to turn it into an entry. It never fabricates a story:
    `kind` defaults to 'community' (source pending), and sources stay empty
    until an editor attaches the original link.
    """
    s = scored or score_item(item)
    return {
        'suggested_character': s['character'],
        'signals': s['signals'],
        'why': explain(item, s),
        'suggested_kind': 'community',
        'source_title': item.get('title', ''),
        'source_url': item.get('url', ''),
        'note': '自动线索卡：待人工核实出处、版权与内容后方可转正发布。',
    }
