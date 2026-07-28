"""Balanced, punctuation-aware line layout for Tk tooltip text."""

from __future__ import annotations

import math
import re


_CLOSING_PUNCTUATION = frozenset("，。；：！？、,.；:!?)]}）》】」』")
_OPENING_PUNCTUATION = frozenset("([{（《【「『")
_NO_BREAK_AROUND = frozenset("=～~／/")
_SENTENCE_END = frozenset("。；！？;!?")
_CLAUSE_END = frozenset("，、,")
_NUMERIC_TOKEN = re.compile(
    r"[+\-]?(?:\d+(?:\.\d+)?|\.\d+)"
    r"(?:\s*(?:Hz|FPS|ms|µs|us|bit|px|%|°|秒|毫秒|分鐘|小時|段|倍|個|位|點))?",
    re.IGNORECASE,
)
_ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_./+\-]*")


def _protected_spans(text):
    spans = [match.span() for match in _NUMERIC_TOKEN.finditer(text)]
    spans.extend(match.span() for match in _ASCII_TOKEN.finditer(text))
    spans.sort()
    return spans


def _is_inside_span(index, spans):
    return any(start < index < end for start, end in spans)


def _numeric_ends(spans, text):
    return {
        end
        for start, end in spans
        if _NUMERIC_TOKEN.fullmatch(text[start:end])
    }


def _balanced_line_breaks(text, maximum_width, measure):
    text = text.strip()
    if not text or measure(text) <= maximum_width:
        return [text]

    spans = _protected_spans(text)
    numeric_ends = _numeric_ends(spans, text)
    candidates = [0]
    for index in range(1, len(text)):
        if _is_inside_span(index, spans):
            continue
        left = text[index - 1]
        right = text[index]
        if right in _CLOSING_PUNCTUATION:
            continue
        if left in _OPENING_PUNCTUATION:
            continue
        if left in _NO_BREAK_AROUND or right in _NO_BREAK_AROUND:
            continue
        candidates.append(index)
    candidates.append(len(text))

    character_widths = [max(0, measure(char)) for char in text]
    prefix_width = [0]
    for width in character_widths:
        prefix_width.append(prefix_width[-1] + width)

    best = {0: (0.0, None)}
    for end in candidates[1:]:
        best_score = math.inf
        best_start = None
        for start in reversed(candidates):
            if start >= end or start not in best:
                continue
            line_start = start
            line_end = end
            while line_start < line_end and text[line_start].isspace():
                line_start += 1
            while line_end > line_start and text[line_end - 1].isspace():
                line_end -= 1
            width = prefix_width[line_end] - prefix_width[line_start]
            if width > maximum_width:
                continue
            fullness = width / maximum_width
            slack_cost = (1.0 - fullness) ** 2 * 100.0
            if end == len(text):
                slack_cost *= 0.25
                if start and fullness < 0.38:
                    slack_cost += 120.0
            elif fullness < 0.52:
                slack_cost += 80.0

            boundary_cost = 0.0
            previous = text[line_end - 1] if line_end else ""
            following = text[end] if end < len(text) else ""
            if previous in _SENTENCE_END:
                boundary_cost -= 24.0
            elif previous in _CLAUSE_END:
                boundary_cost -= 10.0
            elif previous.isspace():
                boundary_cost -= 5.0
            if (
                end in numeric_ends
                and following
                and not following.isspace()
                and following not in _CLOSING_PUNCTUATION
            ):
                boundary_cost += 90.0

            score = best[start][0] + slack_cost + boundary_cost
            if score < best_score:
                best_score = score
                best_start = start
        if best_start is not None:
            best[end] = (best_score, best_start)

    if len(text) not in best:
        return [text]

    lines = []
    end = len(text)
    while end:
        start = best[end][1]
        if start is None:
            return [text]
        lines.append(text[start:end].strip())
        end = start
    lines.reverse()
    return lines


def wrap_tooltip_text(text, maximum_width, measure):
    """Return explicitly wrapped text without inserting visible control marks."""
    maximum_width = max(1, int(maximum_width))
    output = []
    for explicit_line in str(text).split("\n"):
        if not explicit_line.strip():
            output.append("")
            continue
        output.extend(
            _balanced_line_breaks(
                explicit_line,
                maximum_width,
                measure,
            )
        )
    return "\n".join(output)
