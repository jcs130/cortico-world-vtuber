"""Punctuation boundaries shared by ordinary text and phonetic synthesis text."""

import re


_ATOMS = re.compile(
    r'<\|SPECIAL_TOKEN_\d+\|>.*?<\|SPECIAL_TOKEN_\d+\|>'
    r'|<[^<>\n]*>|`[^`\n]*`|https?://[^\s<>，。！？；]+|.', re.DOTALL)
_CLOSERS = '，。！？；,!?;.…"\'”’」』）》）)]}'
_HARD_END = re.compile(r'[。！？!?…\n.][，。！？；,!?;.…"\'”’」』）》）)\]}\s]*$')


def readable_length(text: str) -> int:
    text = re.sub(r'<\|SPECIAL_TOKEN_\d+\|>.*?<\|SPECIAL_TOKEN_\d+\|>', '字', text)
    text = re.sub(r'<([^<>|]+)\|[^<>]+>', r'\1', text)
    return len(re.sub(r'[\W_]', '', text))


def punctuation_segments(text: str, min_clause_chars: int = 6, *, max_chars: int = 40) -> list[str]:
    """Keep a sentence's prosody; split long sentences at complete clauses.

    Never split words, pronunciation atoms, decimals, quotes or punctuation runs.
    Text is preserved exactly, including spaces at a boundary. A caller with a
    hard model capacity must handle an oversized clause separately. max_chars
    is a soft readable-length target; short comma clauses share synthesis.
    """
    units = _ATOMS.findall(text)
    clauses, part, pending = [], '', False
    for index, unit in enumerate(units):
        if pending and unit not in _CLOSERS and not unit.isspace():
            clauses.append(part)
            part, pending = '', False
        part += unit
        decimal = (unit == '.' and index > 0 and index + 1 < len(units)
                   and units[index - 1].isdigit() and units[index + 1].isdigit())
        if unit in '，。！？；,!?;…\n' or (unit == '.' and not decimal):
            pending = True
    if part:
        clauses.append(part)
    segments, current = [], ''
    for clause in clauses:
        sentence_end = _HARD_END.search(current)
        clause_limit = (readable_length(current) >= min_clause_chars
                        and readable_length(current + clause) > max_chars)
        if current and (sentence_end or clause_limit):
            segments.append(current)
            current = clause
        else:
            current += clause
    if current:
        segments.append(current)
    return segments or [text]
