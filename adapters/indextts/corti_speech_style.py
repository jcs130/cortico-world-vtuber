"""Pure text and emotion preparation for indextts_adapter.py."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable


MAX_EMOTION_MIX = 0.45
DEFAULT_EMOTION_MIX = 0.35
DEFAULT_CONFIDENCE = 0.65


def bounded_number(value, default: float, minimum: float, maximum: float) -> float:
    """Invalid preferences retain defaults; finite values stay inside safe bounds."""
    try:
        number = float(value)
        if isinstance(value, bool) or not math.isfinite(number):
            return default
        return min(maximum, max(minimum, number))
    except (TypeError, ValueError):
        return default


def emotion_mix_vector(
    mood: str | None,
    base_vector: Iterable[float] | None,
    *,
    source: str,
    confidence: float = 0.0,
    intensity: float | None = None,
    max_mix=DEFAULT_EMOTION_MIX,
    minimum_confidence=DEFAULT_CONFIDENCE,
) -> list[float] | None:
    """Retain reference prosody instead of filling unassigned weight with calm.

    IndexTTS blends the reference with coefficient ``1 - sum(emo_vector)``.
    A calm or uncertain automatic label therefore uses the reference itself.
    Explicit tags keep their mood but share the same bounded mixing budget.
    """
    if not mood or mood == "calm" or base_vector is None:
        return None
    threshold = bounded_number(minimum_confidence, DEFAULT_CONFIDENCE, 0.0, 1.0)
    certainty = bounded_number(confidence, 0.0, 0.0, 1.0)
    if source != "tag" and certainty < threshold:
        return None
    budget = bounded_number(max_mix, DEFAULT_EMOTION_MIX, 0.0, MAX_EMOTION_MIX)
    strength = budget if intensity is None else bounded_number(intensity, budget, 0.0, budget)
    if strength <= 0.0:
        return None
    try:
        base = [float(value) for value in base_vector]
    except (TypeError, ValueError):
        return None
    if len(base) != 8 or any(not math.isfinite(value) or value < 0.0 for value in base):
        return None
    total = sum(base)
    if total <= 0.0:
        return None
    vector = [round(value / total * strength, 6) for value in base]
    # Round down any tiny rounding overflow without changing the distribution.
    overflow = sum(vector) - strength
    if overflow > 0.0:
        largest = max(range(8), key=vector.__getitem__)
        vector[largest] -= overflow
    return vector


_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_CANONICAL = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\Z")
_ROMAN_TOKEN = r"[IVXLCDM\u2160-\u217f]+"
_ROMAN_EDGE = r"(?![A-Za-z0-9_\u2160-\u217f])"
_ENCHANTMENTS = (
    "火焰保护", "摔落保护", "爆炸保护", "弹射物保护", "水下呼吸", "水下速掘",
    "深海探索", "冰霜行者", "灵魂疾行", "迅捷潜行", "绑定诅咒", "消失诅咒",
    "亡灵杀手", "节肢杀手", "火焰附加", "横扫之刃", "精准采集", "海之眷顾",
    "多重射击", "快速装填", "荆棘", "保护", "锋利", "击退", "抢夺", "效率",
    "耐久", "时运", "力量", "冲击", "火矢", "无限", "饵钓", "忠诚", "穿刺",
    "激流", "引雷", "穿透", "风爆", "密度", "突破", "经验修补", "修补",
)
_ENCHANT_TOKEN = (
    r"(?P<label>" + "|".join(_ENCHANTMENTS) + r")\s*(?P<roman>" + _ROMAN_TOKEN + r")"
    + _ROMAN_EDGE + r"(?:[ \t]*级)?(?:[ \t]+(?=[\u4e00-\u9fff]))?"
)
_ENCHANT_LEVEL = re.compile(_ENCHANT_TOKEN)
# Check the identifier boundary once for the whole chain. A preceding Roman
# suffix belongs to the previous enchantment in e.g. 锋利IV耐久III.
_ENCHANT_CHAIN = re.compile(r"(?<![A-Za-z0-9_])(?:" + _ENCHANT_TOKEN + r")+")
_NAMED_LEVEL = re.compile(
    r"(?P<label>附魔等级|冒险者等级|技能等级|等级)\s*[:：]?\s*(?P<roman>"
    + _ROMAN_TOKEN + r")" + _ROMAN_EDGE + r"(?:[ \t]*级)?(?:[ \t]+(?=[\u4e00-\u9fff]))?"
)
_ORDINAL_LEVEL = re.compile(r"第\s*(?P<roman>" + _ROMAN_TOKEN + r")" + _ROMAN_EDGE + r"\s*(?P<unit>层|级)")
_TOWER_LEVEL = re.compile(r"(?P<label>试炼塔|试炼场|试炼)\s*(?P<roman>" + _ROMAN_TOKEN + r")" + _ROMAN_EDGE + r"\s*层")


def roman_level(token: str) -> int | None:
    """Only canonical standalone Roman levels; never normalize the whole utterance."""
    roman = unicodedata.normalize("NFKC", token).upper()
    if not roman or not _ROMAN_CANONICAL.fullmatch(roman):
        return None
    result = 0
    previous = 0
    for letter in reversed(roman):
        value = _ROMAN_VALUES[letter]
        result += -value if value < previous else value
        previous = max(previous, value)
    return result if 1 <= result <= 100 else None


def chinese_level(number: int) -> str:
    digits = "零一二三四五六七八九"
    if number < 10:
        return digits[number]
    if number == 100:
        return "一百"
    tens, ones = divmod(number, 10)
    return ("十" if tens == 1 else digits[tens] + "十") + (digits[ones] if ones else "")


def normalize_level_speech(text: str) -> str:
    """Read unambiguous Chinese enchantment/level labels, preserve IDs and English."""
    def named(match: re.Match) -> str:
        prefix = match.string[max(0, match.start() - 16):match.start()]
        if re.search(r"(?:玩家|账号|用户名|昵称|ID|id|名叫|我叫|叫作|叫做)\s*[:：]?\s*[\"'“‘「]?$", prefix):
            return match[0]
        number = roman_level(match["roman"])
        return match[0] if number is None else match["label"] + chinese_level(number) + "级"

    def ordinal(match: re.Match) -> str:
        number = roman_level(match["roman"])
        return match[0] if number is None else "第" + chinese_level(number) + match["unit"]

    def tower(match: re.Match) -> str:
        number = roman_level(match["roman"])
        return match[0] if number is None else match["label"] + "第" + chinese_level(number) + "层"

    def chain(match: re.Match) -> str:
        prefix = match.string[max(0, match.start() - 16):match.start()]
        if re.search(r"(?:玩家|账号|用户名|昵称|ID|id|名叫|我叫|叫作|叫做)\s*[:：]?\s*[\"'“‘「]?$", prefix):
            return match[0]
        return _ENCHANT_LEVEL.sub(named, match[0]).rstrip(' \t')

    text = _ENCHANT_CHAIN.sub(chain, text)
    text = _NAMED_LEVEL.sub(named, text)
    text = _ORDINAL_LEVEL.sub(ordinal, text)
    return _TOWER_LEVEL.sub(tower, text)


_DIRECTION_ACTION = re.compile(
    r"^(?:轻声|小声|低声|大声|耳语|压低声音|提高声音|放轻声音|放低声音|温柔地说|"
    r"挥手|挥挥手|招手|点头|摇头|眨眼|吐舌|歪头|微笑|笑|大笑|轻笑|苦笑|"
    r"叹气|深呼吸|清嗓|清清嗓子|咳嗽|挠头|摸头|拍手|鼓掌|停顿|沉默|"
    r"害羞|得意|惊讶|开心|愤怒|难过|紧张|生气|兴奋|平静|严肃|俏皮|温柔|"
    r"calm|happy|angry|sad|afraid|surprised|gentle|playful|warm|serious)"
    r"(?:地|着)?(?:@(?:\d+(?:\.\d+)?))?$",
    re.IGNORECASE,
)
_BRACKET = re.compile(r"[（(\[【]([^）)\]】\n]+)[）)\]】]|<([^<>\n]+)>|\*([^*\n]+)\*")
# Stage metadata has its own syntax; removing it must not depend on whether
# the local voice presets know that emotion. No spaces, IDs or factual clauses.
_EMOTION_TAG = r"(?:[\u4e00-\u9fff]{1,8}|[A-Za-z]{1,16})@[0-9]+(?:\.[0-9]+)?"
_EXPLICIT_EMOTION = re.compile(_EMOTION_TAG)
_BARE_EMOTION = re.compile(r"(?<![\w@|+\-])" + _EMOTION_TAG + r"(?![\w@/|+\-]|\.\d)")
# VoxCPM acoustic controls arrive inside complete brackets. IndexTTS accepts
# emotion vectors, but has no native decoder for these control tokens.
_ACOUSTIC_CUES = {
    "laughing": "happy",
    "sigh": "melancholic",
    "breath": None,
    "uhm": None,
    "shh": None,
    "question-ah": None,
    "question-ei": None,
    "question-en": None,
    "question-oh": None,
    "confirmation-en": None,
    "surprise-wa": "surprised",
    "surprise-yo": "surprised",
    "surprise-ah": "surprised",
    "surprise-oh": "surprised",
    "dissatisfaction-hnn": "disgusted",
}


def strip_stage_directions(text: str, mood_rules) -> tuple[str, list[str], str | None, float | None]:
    """Remove complete stage cues, preserving factual parentheses and pronunciations."""
    tags: list[str] = []
    mood = None
    intensity = None
    acoustic_mood = None

    def recognize(tag: str) -> bool:
        nonlocal mood, intensity, acoustic_mood
        candidate = tag.strip()
        acoustic = candidate.lower() in _ACOUSTIC_CUES
        recognized_mood = None
        if acoustic and acoustic_mood is None:
            acoustic_mood = _ACOUSTIC_CUES[candidate.lower()]
        # Match a complete known cue or explicit mood tag, not words embedded in facts.
        explicit = _EXPLICIT_EMOTION.fullmatch(candidate) is not None
        action = _DIRECTION_ACTION.fullmatch(candidate) is not None
        # The registry owns its aliases. A second allowlist had omitted valid
        # labels such as 无奈 and 温暖, sending them into both speech paths.
        if not acoustic:
            recognized_mood = next((mapped for regex, mapped in mood_rules
                                    if regex.fullmatch(candidate)), None)
        if (action or explicit) and recognized_mood is None:
            for regex, mapped_mood in mood_rules:
                if regex.search(candidate):
                    recognized_mood = mapped_mood
                    break
        if not action and not explicit and not acoustic and recognized_mood is None:
            return False
        tags.append(candidate)
        if mood is None and recognized_mood is not None:
            mood = recognized_mood
            match = re.search(r"@(\d+(?:\.\d+)?)", candidate)
            if match:
                intensity = float(match[1])
        return True

    def bracket(match: re.Match) -> str:
        tag = next(group for group in match.groups() if group is not None)
        return "" if recognize(tag) else match[0]

    def bare(fragment: str) -> str:
        return _BARE_EMOTION.sub(lambda match: "" if recognize(match[0]) else match[0], fragment)

    # A factual bracket is one preserved unit; do not strip a tag-looking word
    # from inside it in a later bare-tag pass.
    pieces: list[str] = []
    end = 0
    for match in _BRACKET.finditer(text):
        pieces.extend((bare(text[end:match.start()]), bracket(match)))
        end = match.end()
    pieces.append(bare(text[end:]))
    cleaned = "".join(pieces)
    # Do not manufacture pauses, text, or combine separate utterances.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    cleaned = re.sub(r"^[，,；;：:]+\s*", "", cleaned)
    cleaned = re.sub(r"([，,；;：:])\s*[，,；;：:]+", r"\1", cleaned)
    return cleaned, tags, mood or acoustic_mood, intensity
