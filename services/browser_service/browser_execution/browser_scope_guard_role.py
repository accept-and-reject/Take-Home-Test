"""Guard every browser navigation target and page-budget decision.

Design: ``doc/02_系統設計/03_內部架構圖.md`` — BrowserScopeGuardRole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit


BLOCKED_PATH_PARTS = {
    "account",
    "auth",
    "login",
    "register",
    "signin",
    "signup",
}
BLOCKED_TEXT_MARKERS = {"登入", "註冊"}
BLOCKED_SUFFIXES = {
    ".7z",
    ".doc",
    ".docx",
    ".pdf",
    ".ppt",
    ".pptx",
    ".rar",
    ".xls",
    ".xlsx",
    ".zip",
}


@dataclass(frozen=True)
class NavigationValidation:
    allowed: bool
    normalized_url: str | None
    reason: str


@dataclass(frozen=True)
class PageBudgetValidation:
    allowed: bool
    reason: str


def validate_navigation_target(
    candidate_url: str,
    allowed_domain: str,
    visited_urls: set[str],
) -> NavigationValidation:
    """Allow only new public HTML pages within the configured official domain."""

    try:
        parsed = urlsplit(candidate_url)
    except ValueError:
        return NavigationValidation(False, None, "URL could not be parsed")
    if parsed.scheme.casefold() not in {"http", "https"}:
        return NavigationValidation(False, None, "URL scheme is not HTTP or HTTPS")
    host = (parsed.hostname or "").rstrip(".").casefold()
    if not host or not _host_matches(host, allowed_domain):
        return NavigationValidation(False, None, "URL is outside the allowed domain")

    path = parsed.path or "/"
    decoded_path = unquote(path)
    decoded_query = unquote(parsed.query)
    target_tokens = {
        token
        for part in PurePosixPath(decoded_path).parts
        if part != "/"
        for token in re.split(
            r"[^a-z0-9]+",
            PurePosixPath(part).stem.casefold(),
        )
        if token
    }
    target_tokens.update(
        token
        for token in re.split(r"[^a-z0-9]+", decoded_query.casefold())
        if token
    )
    decoded_target = f"{decoded_path}?{decoded_query}".casefold()
    if target_tokens.intersection(BLOCKED_PATH_PARTS) or any(
        marker in decoded_target for marker in BLOCKED_TEXT_MARKERS
    ):
        return NavigationValidation(False, None, "login or account page is blocked")
    query_values = [
        value for _, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    if (
        PurePosixPath(decoded_path.casefold()).suffix in BLOCKED_SUFFIXES
        or any(
            PurePosixPath(value.casefold()).suffix in BLOCKED_SUFFIXES
            for value in query_values
        )
    ):
        return NavigationValidation(False, None, "attachment navigation is blocked")

    normalized = urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path.rstrip("/") or "/",
            parsed.query,
            "",
        )
    )
    if normalized in visited_urls:
        return NavigationValidation(False, normalized, "URL was already visited")
    return NavigationValidation(True, normalized, "allowed official-site page")


def enforce_page_budget(
    visited_page_count: int,
    max_pages: int,
) -> PageBudgetValidation:
    if visited_page_count < 0:
        return PageBudgetValidation(False, "visited page count is invalid")
    if max_pages < 1:
        return PageBudgetValidation(False, "page budget is invalid")
    if visited_page_count >= max_pages:
        return PageBudgetValidation(False, "page budget reached")
    return PageBudgetValidation(True, "page budget available")


def _host_matches(host: str, allowed_domain: str) -> bool:
    normalized_allowed = allowed_domain.strip().rstrip(".").casefold()
    if normalized_allowed.startswith("www."):
        normalized_allowed = normalized_allowed[4:]
    normalized_host = host[4:] if host.startswith("www.") else host
    return normalized_host == normalized_allowed or normalized_host.endswith(
        "." + normalized_allowed
    )
