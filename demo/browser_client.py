"""Submit one direct, self-contained Browser Task for the demo client.

Design source: ``doc/05_交付系統/02_使用者手冊.md``（展示操作）.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from shared.settings import (
    SUPPORTED_ALLOWED_DOMAIN,
    SUPPORTED_COMPANY_NAME,
    SUPPORTED_START_URL,
)


TOPIC_LABELS = {
    "company_overview": "公司介紹",
    "products": "主要產品",
    "benefits": "福利制度",
}
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


@dataclass(frozen=True)
class BrowserDemoResult:
    """Raw developer result and concise supervisor-facing text."""

    payload: dict[str, Any]
    output_text: str
    outcome: str


def run_browser_scenario(
    scenario: dict[str, Any],
    *,
    browser_base_url: str,
    browser_token: str,
    timeout_seconds: int,
) -> BrowserDemoResult:
    """Build and execute a direct BrowserTask without Knowledge MCP."""

    user_input = str(scenario["input"])
    requested_url = _requested_url(user_input, SUPPORTED_START_URL)
    requested_domain = _url_domain(requested_url)
    outside_allowed_scope = _normalized_domain(
        requested_domain
    ) != _normalized_domain(SUPPORTED_ALLOWED_DOMAIN)

    task = {
        "browser_task_id": str(uuid4()),
        "task_source": "direct",
        "source_action_item_id": None,
        "target_company": SUPPORTED_COMPANY_NAME,
        "start_url": requested_url,
        "allowed_domain": requested_domain,
        "requested_topics": list(TOPIC_LABELS),
        "max_pages": 5,
        "created_at": datetime.now(UTC).isoformat(),
    }
    request_payload = {
        "user_instruction": user_input,
        "browser_task": task,
    }
    endpoint = browser_base_url.rstrip("/") + "/browser-tasks"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {browser_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = _json_object(response.read())
    except urllib.error.HTTPError as exc:
        payload = _safe_error_payload(exc)
        if (
            exc.code == 422
            and outside_allowed_scope
            and _is_safe_scope_rejection(payload)
        ):
            return BrowserDemoResult(
                payload={
                    "request": request_payload,
                    "http_status": exc.code,
                    "response": payload,
                },
                output_text=_format_rejection(payload),
                outcome="rejected",
            )
        raise RuntimeError(
            f"Browser Task API 回傳 HTTP {exc.code}："
            f"{json.dumps(payload, ensure_ascii=False)}"
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError("Browser Task 在限制時間內未完成。") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("無法連接 Browser Task API。") from exc

    if outside_allowed_scope:
        raise RuntimeError("非法網域未被 Scope Validation 拒絕。")

    return BrowserDemoResult(
        payload={
            "request": request_payload,
            "response": payload,
        },
        output_text=_format_result(payload),
        outcome=str(payload.get("status") or "unknown"),
    )


def _format_result(payload: dict[str, Any]) -> str:
    run = payload.get("browser_run") or {}
    status = str(payload.get("status") or run.get("status") or "unknown")
    visited = run.get("visited_page_count")
    lines = [
        f"執行結果：{status}",
        f"已檢查頁面：{visited if visited is not None else '無法取得'} 頁",
        "",
    ]
    findings = payload.get("findings") or []
    if not findings:
        error = payload.get("error") or {}
        message = error.get("message") or "沒有研究結果可顯示。"
        lines.append(str(message))
        return "\n".join(lines).strip()

    for finding in findings:
        topic = str(finding.get("topic") or "unknown")
        label = TOPIC_LABELS.get(topic, topic)
        finding_status = str(finding.get("status") or "unknown")
        lines.append(f"【{label}】{finding_status}")
        if finding_status == "found":
            lines.append(f"摘要：{finding.get('summary')}")
            lines.append(f"證據：{finding.get('evidence_quote')}")
            lines.append(f"判斷理由：{finding.get('evidence_reason')}")
            for source_url in finding.get("source_urls") or []:
                lines.append(f"官方來源：{source_url}")
        else:
            lines.append(
                "說明：" + str(finding.get("message") or "未找到足夠證據。")
            )
        lines.append("")
    return "\n".join(lines).strip()


def _json_object(raw: bytes) -> dict[str, Any]:
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Browser Task API 未回傳 JSON Object。")
    return payload


def _safe_error_payload(error: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        return _json_object(error.read())
    except (UnicodeDecodeError, json.JSONDecodeError, RuntimeError):
        return {
            "status": "unreadable_error",
            "reasons": ["無法解析錯誤內容"],
        }


def _format_rejection(payload: dict[str, Any]) -> str:
    reasons = payload.get("reasons") or []
    if not reasons:
        detail = payload.get("detail")
        if isinstance(detail, dict):
            reasons = detail.get("reasons") or []
        elif detail:
            reasons = [str(detail)]
    if not reasons:
        execution_request = payload.get("execution_request") or {}
        rejection_reason = execution_request.get("rejection_reason")
        if rejection_reason:
            reasons = [str(rejection_reason)]
    lines = [
        "執行結果：rejected",
        "原因：" + ("；".join(str(reason) for reason in reasons) or "請求超出允許範圍"),
        "Browser Run：未建立",
        "造訪頁數：0",
    ]
    return "\n".join(lines)


def _is_safe_scope_rejection(payload: dict[str, Any]) -> bool:
    execution_request = payload.get("execution_request") or {}
    rejection_reason = str(execution_request.get("rejection_reason") or "")
    return all(
        (
            payload.get("status") == "rejected",
            execution_request.get("validation_status") == "rejected",
            payload.get("browser_run") is None,
            not payload.get("findings"),
            not payload.get("action_logs"),
            "start_url" in rejection_reason,
            "allowed_domain" in rejection_reason,
        )
    )


def _requested_url(user_input: str, default_start_url: str) -> str:
    match = URL_PATTERN.search(user_input)
    if match is None:
        return default_start_url
    return match.group(0).rstrip(".,;:!?)]}，。；：！？）】」』")


def _url_domain(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("使用者輸入的 URL 必須是有效的 HTTP(S) 網址。")
    return parsed.hostname


def _normalized_domain(value: str) -> str:
    return value.strip().rstrip(".").casefold()
