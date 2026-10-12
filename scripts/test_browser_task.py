"""Manually verify scoped Browser execution, persistence, and rejection."""

from __future__ import annotations

import argparse
import json
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2

from services.browser_service.store import BrowserRunStore
from shared.settings import (
    BrowserSettings,
    SUPPORTED_ALLOWED_DOMAIN,
    SUPPORTED_COMPANY_NAME,
    SUPPORTED_START_URL,
)


FINAL_RUN_STATUSES = {"completed", "partial"}
EXPECTED_TOPICS = {"company_overview", "products", "benefits"}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify independent official-site Browser Task execution."
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Browser Service base URL",
    )
    parser.add_argument(
        "--case",
        dest="case_name",
        choices=("flow3-case1", "flow3-case2", "flow3-case3"),
        help="Run one formal Browser Task case instead of the legacy combined check.",
    )
    return parser.parse_args()


def _task(
    settings: BrowserSettings,
    *,
    max_pages: int,
) -> dict[str, Any]:
    return {
        "browser_task_id": str(uuid4()),
        "task_source": "direct",
        "source_action_item_id": None,
        "target_company": settings.company_name,
        "start_url": str(settings.start_url),
        "allowed_domain": settings.allowed_domain,
        "requested_topics": [
            "company_overview",
            "products",
            "benefits",
        ],
        "max_pages": max_pages,
        "created_at": datetime.now(UTC).isoformat(),
    }


def _submit(
    client: httpx2.Client,
    url: str,
    token: str | None,
    task: dict[str, Any],
) -> httpx2.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        f"{url}/browser-tasks",
        headers=headers,
        json={
            "user_instruction": "請只在指定官網查詢三類公司資訊。",
            "browser_task": task,
        },
    )


def _result_is_traceable(
    payload: dict[str, Any],
    *,
    allowed_domain: str,
    max_pages: int,
    require_budget_stop: bool = False,
    required_found_topics: set[str] | None = None,
) -> bool:
    run = payload.get("browser_run") or {}
    findings = payload.get("findings") or []
    logs = payload.get("action_logs") or []
    topics = {finding.get("topic") for finding in findings}
    sequences = [log.get("sequence") for log in logs]
    visited_urls = [
        log.get("target_url")
        for log in logs
        if log.get("action_type") == "open" and log.get("target_url")
    ]
    source_urls = [
        source_url
        for finding in findings
        for source_url in finding.get("source_urls", [])
    ]
    action_types = {log.get("action_type") for log in logs}
    accepted_capture_evidence = _accepted_capture_evidence(logs)
    found_topics = {
        finding.get("topic")
        for finding in findings
        if finding.get("status") == "found"
    }
    all_topics_found = all(
        finding.get("status") == "found" for finding in findings
    )
    budget_stop_recorded = any(
        log.get("action_type") == "skip"
        and log.get("detail") == "page budget reached"
        for log in logs
    )
    return all(
        (
            run.get("status") in FINAL_RUN_STATUSES,
            run.get("visited_page_count", max_pages + 1) <= max_pages,
            topics == EXPECTED_TOPICS,
            len(findings) == len(EXPECTED_TOPICS),
            run.get("visited_page_count") == len(visited_urls),
            sequences == list(range(1, len(sequences) + 1)),
            bool(logs) and logs[-1].get("action_type") == "finish",
            {"open", "inspect", "capture", "finish"}.issubset(action_types),
            all(_host_allowed(url, allowed_domain) for url in visited_urls),
            all(_host_allowed(url, allowed_domain) for url in source_urls),
            all(
                finding.get("status") != "found"
                or (
                    finding.get("topic") in accepted_capture_evidence
                    and _contains_cjk_text(finding.get("summary"))
                    and _contains_cjk_text(finding.get("evidence_reason"))
                    and finding.get("evidence_quote")
                    == accepted_capture_evidence[finding.get("topic")][0]
                    and finding.get("evidence_reason")
                    == accepted_capture_evidence[finding.get("topic")][1]
                )
                for finding in findings
            ),
            (required_found_topics or set()).issubset(found_topics),
            _navigation_stop_is_consistent(
                payload,
                max_pages=max_pages,
            ),
            not require_budget_stop
            or all_topics_found
            or budget_stop_recorded,
        )
    )


def _navigation_stop_is_consistent(
    payload: dict[str, Any],
    *,
    max_pages: int,
) -> bool:
    run = payload.get("browser_run") or {}
    findings = payload.get("findings") or []
    logs = payload.get("action_logs") or []
    all_topics_found = all(
        finding.get("status") == "found" for finding in findings
    )
    if any(
        log.get("reason") == "no useful unvisited internal link was selected"
        for log in logs
    ):
        return False

    retry_sequences = [
        log.get("sequence")
        for log in logs
        if log.get("reason")
        == "agent selected no link while allowed candidates remained"
    ]
    follow_sequences = [
        log.get("sequence")
        for log in logs
        if log.get("action_type") == "follow_link"
        and log.get("status") == "success"
    ]
    if not all_topics_found and any(
        not any(
            isinstance(follow_sequence, int)
            and isinstance(retry_sequence, int)
            and follow_sequence > retry_sequence
            for follow_sequence in follow_sequences
        )
        for retry_sequence in retry_sequences
    ):
        return False

    unresolved = [
        finding
        for finding in findings
        if finding.get("status") != "found"
    ]
    if not unresolved:
        return True

    visited_page_count = run.get("visited_page_count")
    if visited_page_count == max_pages:
        stopped_for_budget = any(
            log.get("action_type") == "skip"
            and log.get("reason")
            == "page budget stopped additional navigation"
            and log.get("detail") == "page budget reached"
            for log in logs
        )
        return stopped_for_budget and all(
            "page budget was reached" in str(finding.get("message"))
            for finding in unresolved
        )

    stopped_for_exhaustion = any(
        log.get("action_type") == "skip"
        and log.get("reason")
        == "allowed internal link candidates were exhausted"
        for log in logs
    )
    return stopped_for_exhaustion and all(
        "allowed internal links were exhausted" in str(finding.get("message"))
        for finding in unresolved
    )


def _accepted_capture_evidence(
    logs: list[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    accepted: dict[str, tuple[str, str]] = {}
    for log in logs:
        if (
            log.get("action_type") != "capture"
            or log.get("status") != "success"
            or not log.get("detail")
        ):
            continue
        try:
            detail = json.loads(log["detail"])
            topic = str(detail["topic"])
            evidence_quote = str(detail["evidence_quote"])
            evidence_reason = str(detail["evidence_reason"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            topic in EXPECTED_TOPICS
            and evidence_quote.strip()
            and evidence_reason.strip()
        ):
            accepted[topic] = (evidence_quote, evidence_reason)
    return accepted


def _contains_cjk_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return any(
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        for character in value
    )


def _host_allowed(url: str, allowed_domain: str) -> bool:
    host = (urlsplit(url).hostname or "").rstrip(".").casefold()
    expected = allowed_domain.strip().rstrip(".").casefold()
    if host.startswith("www."):
        host = host[4:]
    if expected.startswith("www."):
        expected = expected[4:]
    return host == expected or host.endswith("." + expected)


def _remaining_run_slots(
    store: BrowserRunStore,
    settings: BrowserSettings,
) -> tuple[int, int]:
    today_utc = datetime.now(UTC).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    started_today = store.count_started_browser_runs_since(today_utc)
    return started_today, settings.max_daily_browser_runs - started_today


def _run_case(
    case_name: str,
    *,
    base_url: str,
    settings: BrowserSettings,
    store: BrowserRunStore,
) -> int:
    initial_run_count = store.count_browser_runs()
    timeout = httpx2.Timeout(30.0, read=300.0)
    started_today, remaining_today = _remaining_run_slots(store, settings)

    if case_name in {"flow3-case1", "flow3-case3"} and remaining_today < 1:
        return _print_case_result(
            case_name,
            False,
            {
                "reason": "one daily Browser Run slot is required",
                "started_today": started_today,
                "max_daily_browser_runs": settings.max_daily_browser_runs,
            },
        )

    with httpx2.Client(timeout=timeout) as client:
        if case_name == "flow3-case1":
            health = client.get(f"{base_url}/health")
            run_count_after_health = store.count_browser_runs()
            max_pages = min(settings.max_pages, 5)
            response = _submit(
                client,
                base_url,
                settings.browser_api_token,
                _task(settings, max_pages=max_pages),
            )
            payload = response.json()
            final_run_count = store.count_browser_runs()
            passed = all(
                (
                    health.status_code == 200,
                    health.json().get("checks", {}).get("browser_runtime")
                    == "ready",
                    run_count_after_health == initial_run_count,
                    response.status_code == 200,
                    _result_is_traceable(
                        payload,
                        allowed_domain=settings.allowed_domain,
                        max_pages=max_pages,
                        required_found_topics={"company_overview", "products"},
                    ),
                    final_run_count == initial_run_count + 1,
                )
            )
            return _print_case_result(
                case_name,
                passed,
                {
                    "health": health.json(),
                    "browser_run": payload,
                    "run_count_before": initial_run_count,
                    "run_count_after": final_run_count,
                },
            )

        if case_name == "flow3-case2":
            external_task = _task(settings, max_pages=5)
            external_task["target_company"] = "其他公司"
            external_task["start_url"] = "https://example.com/"
            external_task["allowed_domain"] = "example.com"
            external = _submit(
                client,
                base_url,
                settings.browser_api_token,
                external_task,
            )
            missing_field_task = _task(settings, max_pages=5)
            missing_field_task.pop("target_company")
            missing_field = _submit(
                client,
                base_url,
                settings.browser_api_token,
                missing_field_task,
            )
            unknown_topic_task = _task(settings, max_pages=5)
            unknown_topic_task["requested_topics"] = ["unknown_topic"]
            unknown_topic = _submit(
                client,
                base_url,
                settings.browser_api_token,
                unknown_topic_task,
            )
            too_many_pages = _submit(
                client,
                base_url,
                settings.browser_api_token,
                _task(settings, max_pages=6),
            )
            wrong_token = secrets.token_urlsafe(32)
            if secrets.compare_digest(wrong_token, settings.browser_api_token):
                wrong_token += "-wrong"
            missing_token = _submit(
                client,
                base_url,
                None,
                _task(settings, max_pages=5),
            )
            invalid_token = _submit(
                client,
                base_url,
                wrong_token,
                _task(settings, max_pages=5),
            )
            external_payload = external.json()
            external_rejection_reason = str(
                (external_payload.get("execution_request") or {}).get(
                    "rejection_reason"
                )
                or ""
            )
            final_run_count = store.count_browser_runs()
            passed = all(
                (
                    external.status_code == 422,
                    external_payload.get("status") == "rejected",
                    external_payload.get("browser_run") is None,
                    settings.company_name == SUPPORTED_COMPANY_NAME,
                    str(settings.start_url).rstrip("/")
                    == SUPPORTED_START_URL.rstrip("/"),
                    settings.allowed_domain == SUPPORTED_ALLOWED_DOMAIN,
                    "target_company" in external_rejection_reason,
                    "start_url" in external_rejection_reason,
                    "allowed_domain" in external_rejection_reason,
                    missing_field.status_code == 422,
                    unknown_topic.status_code == 422,
                    too_many_pages.status_code == 422,
                    missing_token.status_code == 401,
                    invalid_token.status_code == 401,
                    final_run_count == initial_run_count,
                )
            )
            return _print_case_result(
                case_name,
                passed,
                {
                    "external_domain": external_payload,
                    "supported_company": settings.company_name,
                    "scope_rejection_reason": external_rejection_reason,
                    "schema_rejections": {
                        "missing_field": missing_field.json(),
                        "unknown_topic": unknown_topic.json(),
                        "too_many_pages": too_many_pages.json(),
                    },
                    "http_statuses": {
                        "missing_token": missing_token.status_code,
                        "invalid_token": invalid_token.status_code,
                    },
                    "run_count_before": initial_run_count,
                    "run_count_after": final_run_count,
                },
            )

        response = _submit(
            client,
            base_url,
            settings.browser_api_token,
            _task(settings, max_pages=1),
        )
        payload = response.json()
        final_run_count = store.count_browser_runs()
        passed = all(
            (
                response.status_code == 200,
                _result_is_traceable(
                    payload,
                    allowed_domain=settings.allowed_domain,
                    max_pages=1,
                    require_budget_stop=True,
                ),
                final_run_count == initial_run_count + 1,
            )
        )
        return _print_case_result(
            case_name,
            passed,
            {
                "browser_run": payload,
                "run_count_before": initial_run_count,
                "run_count_after": final_run_count,
            },
        )


def _print_case_result(
    case_name: str,
    passed: bool,
    result: dict[str, Any],
) -> int:
    print(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "case": case_name,
                "result": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def main() -> int:
    arguments = _arguments()
    settings = BrowserSettings.from_env()
    store = BrowserRunStore(settings.data_dir)
    store.initialize()
    base_url = arguments.url.rstrip("/")
    if arguments.case_name:
        return _run_case(
            arguments.case_name,
            base_url=base_url,
            settings=settings,
            store=store,
        )
    wrong_token = secrets.token_urlsafe(32)
    if secrets.compare_digest(wrong_token, settings.browser_api_token):
        wrong_token += "-wrong"

    today_utc = datetime.now(UTC).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    started_today = store.count_started_browser_runs_since(today_utc)
    remaining_today = settings.max_daily_browser_runs - started_today
    if remaining_today < 2:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "two daily Browser Run slots are required",
                    "started_today": started_today,
                    "max_daily_browser_runs": settings.max_daily_browser_runs,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    initial_run_count = store.count_browser_runs()
    timeout = httpx2.Timeout(30.0, read=300.0)
    with httpx2.Client(timeout=timeout) as client:
        health = client.get(f"{base_url}/health")
        run_count_after_health = store.count_browser_runs()

        full_task = _task(
            settings,
            max_pages=min(settings.max_pages, 5),
        )
        full_run = _submit(
            client,
            base_url,
            settings.browser_api_token,
            full_task,
        )

        one_page_task = _task(settings, max_pages=1)
        one_page_run = _submit(
            client,
            base_url,
            settings.browser_api_token,
            one_page_task,
        )

        external_task = _task(settings, max_pages=5)
        external_task["target_company"] = "其他公司"
        external_task["start_url"] = "https://example.com/"
        external_task["allowed_domain"] = "example.com"
        external = _submit(
            client,
            base_url,
            settings.browser_api_token,
            external_task,
        )

        missing_field_task = _task(settings, max_pages=5)
        missing_field_task.pop("target_company")
        missing_field = _submit(
            client,
            base_url,
            settings.browser_api_token,
            missing_field_task,
        )

        unknown_topic_task = _task(settings, max_pages=5)
        unknown_topic_task["requested_topics"] = ["unknown_topic"]
        unknown_topic = _submit(
            client,
            base_url,
            settings.browser_api_token,
            unknown_topic_task,
        )

        too_many_pages_task = _task(settings, max_pages=6)
        too_many_pages = _submit(
            client,
            base_url,
            settings.browser_api_token,
            too_many_pages_task,
        )

        missing_token = _submit(client, base_url, None, full_task)
        invalid_token = _submit(client, base_url, wrong_token, full_task)

    final_run_count = store.count_browser_runs()
    health_payload = health.json()
    full_payload = full_run.json()
    one_page_payload = one_page_run.json()
    external_payload = external.json()
    external_rejection_reason = str(
        (external_payload.get("execution_request") or {}).get(
            "rejection_reason"
        )
        or ""
    )
    passed = all(
        (
            health.status_code == 200,
            health_payload.get("checks", {}).get("browser_runtime") == "ready",
            run_count_after_health == initial_run_count,
            full_run.status_code == 200,
            _result_is_traceable(
                full_payload,
                allowed_domain=settings.allowed_domain,
                max_pages=min(settings.max_pages, 5),
                required_found_topics={"company_overview", "products"},
            ),
            one_page_run.status_code == 200,
            _result_is_traceable(
                one_page_payload,
                allowed_domain=settings.allowed_domain,
                max_pages=1,
                require_budget_stop=True,
            ),
            external.status_code == 422,
            external_payload.get("status") == "rejected",
            external_payload.get("browser_run") is None,
            settings.company_name == SUPPORTED_COMPANY_NAME,
            str(settings.start_url).rstrip("/")
            == SUPPORTED_START_URL.rstrip("/"),
            settings.allowed_domain == SUPPORTED_ALLOWED_DOMAIN,
            "target_company" in external_rejection_reason,
            "start_url" in external_rejection_reason,
            "allowed_domain" in external_rejection_reason,
            missing_field.status_code == 422,
            unknown_topic.status_code == 422,
            too_many_pages.status_code == 422,
            missing_token.status_code == 401,
            invalid_token.status_code == 401,
            final_run_count == initial_run_count + 2,
        )
    )
    print(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "health": health_payload,
                "full_browser_run": full_payload,
                "one_page_browser_run": one_page_payload,
                "rejections": {
                    "external_domain": external_payload,
                    "scope_rejection_reason": external_rejection_reason,
                    "missing_field": missing_field.json(),
                    "unknown_topic": unknown_topic.json(),
                    "too_many_pages": too_many_pages.json(),
                },
                "http_statuses": {
                    "full_browser_run": full_run.status_code,
                    "one_page_browser_run": one_page_run.status_code,
                    "external_domain": external.status_code,
                    "missing_field": missing_field.status_code,
                    "unknown_topic": unknown_topic.status_code,
                    "too_many_pages": too_many_pages.status_code,
                    "missing_token": missing_token.status_code,
                    "invalid_token": invalid_token.status_code,
                },
                "browser_run_counts": {
                    "before_health": initial_run_count,
                    "after_health": run_count_after_health,
                    "after_submissions": final_run_count,
                },
                "daily_run_limit": {
                    "started_before_test": started_today,
                    "maximum": settings.max_daily_browser_runs,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
