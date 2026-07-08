"""Stateless pagination-link classifiers for the crawler engine.

See .docs/crawler.md#link-discovery--pagination for the design rationale.
"""

from urllib.parse import parse_qs, urlparse


def is_plain_int(value: str) -> bool:
    """Strict base-10 integer check. `str.isdigit()` alone accepts
    Unicode digit-look-alikes (e.g. superscripts) that aren't valid
    `int()` input — `isascii()` closes that gap without loosening the
    check to a substring/regex match."""
    return bool(value) and value.isascii() and value.isdigit()


def is_pagination_link(pag_cfg: dict, url: str, anchor_text: str, rel: list[str]) -> bool:
    """Conservative pagination classifier — a pure rule (the
    `pagination.enabled` gate lives at the call site). A paging query param,
    when present, is the deciding signal: a plain-integer value = pagination,
    anything else = not (fail closed; this is the firewall against session-URL
    traps). Only with no configured param present do we fall back to
    rel="next" / anchor-text. Never loosen the numeric check to a substring
    match."""
    param_signals = pag_cfg.get("param_signals", [])
    if isinstance(param_signals, str):
        param_signals = [param_signals]
    if param_signals:
        query = parse_qs(urlparse(url).query, keep_blank_values=True)
        query_lower = {k.lower(): v for k, v in query.items()}
        matched_values = [
            query_lower[p.lower()][0]
            for p in param_signals
            if p.lower() in query_lower
        ]
        if matched_values:
            return all(is_plain_int(v) for v in matched_values)

    if "next" in rel:
        return True

    text = (anchor_text or "").strip().lower()
    text_signals = pag_cfg.get("text_signals", [])
    if isinstance(text_signals, str):
        text_signals = [text_signals]
    return bool(text) and (text in text_signals or is_plain_int(text))


def safe_int(value, default: int) -> int:
    """Defensive coercion for pagination config values — a YAML typo
    (a string where an int belongs) must fall back safely, not crash
    the worker's blanket except-and-swallow handler mid-crawl."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def elect_pagination_target(pag_cfg: dict, raw_links, pagination_on: bool) -> str | None:
    """Pick AT MOST ONE pagination link per page — prefer an explicit
    rel="next" signal, else the first classifier-accepted link in
    document order. Without this, a normal numbered pager bar
    ("1 2 3 4 5 Next Last") would classify EVERY matching link as
    pagination independently, each bypassing the per-page cap and
    minting its own fresh chain_budget — multiplying the intended
    amplification bound by however many links the pager shows (code
    review finding, 2026-07-02). Real "next page" progression is one
    hop per page, matching how `page_hops` is defined as a linear counter.
    """
    if not pagination_on:
        return None
    first_match = None
    for absolute, text, rel in raw_links:
        if not is_pagination_link(pag_cfg, absolute, text, rel):
            continue
        if "next" in rel:
            return absolute
        if first_match is None:
            first_match = absolute
    return first_match
