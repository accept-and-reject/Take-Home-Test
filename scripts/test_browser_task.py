"""Manually verify Browser Task authentication, validation, and persistence."""

from __future__ import annotations

import argparse
import json
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx2

from services.browser_service.store import BrowserRunStore
from shared.settings import BrowserSettings


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Browser Task acceptance without launching Chromium."
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Browser Service base URL",
    )
    return parser.parse_args()


def _task(
    settings: BrowserSettings,
    *,
    source: str,
    action_item_id: str | None = None,
) -> dict[str, Any]:
    return {
        "browser_task_id": str(uuid4()),
        "task_source": source,
        "source_action_item_id": action_item_id,
        "target_company": settings.company_name,
        "start_url": str(settings.start_url),
        "allowed_domain": settings.allowed_domain,
        "requested_topics": [
            "company_overview",
            "products",
            "benefits",
        ],
        "max_pages": min(settings.max_pages, 5),
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


def _result(
    client: httpx2.Client,
    url: str,
    token: str,
    execution_request_id: str,
) -> httpx2.Response:
    return client.get(
        f"{url}/browser-tasks/{execution_request_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def main() -> int:
    arguments = _arguments()
    settings = BrowserSettings.from_env()
    store = BrowserRunStore(settings.data_dir)
    store.initialize()
    base_url = arguments.url.rstrip("/")
    wrong_token = secrets.token_urlsafe(32)
    if secrets.compare_digest(wrong_token, settings.demo_api_token):
        wrong_token += "-wrong"

    initial_run_count = store.count_browser_runs()
    with httpx2.Client(timeout=30.0) as client:
        health = client.get(f"{base_url}/health")
        run_count_after_health = store.count_browser_runs()

        direct_task = _task(settings, source="direct")
        direct = _submit(
            client,
            base_url,
            settings.demo_api_token,
            direct_task,
        )
        direct_payload = direct.json()
        direct_request = direct_payload.get("execution_request") or {}
        direct_result = _result(
            client,
            base_url,
            settings.demo_api_token,
            direct_request.get(
                "execution_request_id",
                "00000000-0000-0000-0000-000000000000",
            ),
        )

        service_task = _task(
            settings,
            source="action_item",
            action_item_id=str(uuid4()),
        )
        service = _submit(
            client,
            base_url,
            settings.browser_executor_token,
            service_task,
        )

        external_task = _task(settings, source="direct")
        external_task["start_url"] = "https://example.com/"
        external_task["allowed_domain"] = "example.com"
        external = _submit(
            client,
            base_url,
            settings.demo_api_token,
            external_task,
        )
        external_payload = external.json()
        external_request = external_payload.get("execution_request") or {}
        external_request_id = external_request.get("execution_request_id")
        external_result = (
            _result(
                client,
                base_url,
                settings.demo_api_token,
                external_request_id,
            )
            if external_request_id
            else None
        )

        missing_field_task = _task(settings, source="direct")
        missing_field_task.pop("target_company")
        missing_field = _submit(
            client,
            base_url,
            settings.demo_api_token,
            missing_field_task,
        )

        unknown_topic_task = _task(settings, source="direct")
        unknown_topic_task["requested_topics"] = ["unknown_topic"]
        unknown_topic = _submit(
            client,
            base_url,
            settings.demo_api_token,
            unknown_topic_task,
        )

        too_many_pages_task = _task(settings, source="direct")
        too_many_pages_task["max_pages"] = 6
        too_many_pages = _submit(
            client,
            base_url,
            settings.demo_api_token,
            too_many_pages_task,
        )

        missing_token = _submit(client, base_url, None, direct_task)
        invalid_token = _submit(client, base_url, wrong_token, direct_task)
    final_run_count = store.count_browser_runs()

    health_payload = health.json()
    direct_result_payload = direct_result.json()
    direct_run = direct_result_payload.get("browser_run") or {}
    service_payload = service.json()
    external_result_payload = external_result.json() if external_result else {}
    passed = all(
        (
            health.status_code == 200,
            health_payload.get("checks", {}).get("browser_runtime") == "ready",
            run_count_after_health == initial_run_count,
            direct.status_code == 202,
            direct_payload.get("status") == "pending",
            direct_result.status_code == 200,
            direct_run.get("status") == "pending",
            direct_result_payload.get("action_logs") == [],
            direct_result_payload.get("findings") == [],
            service.status_code == 202,
            service_payload.get("status") == "pending",
            external.status_code == 422,
            external_payload.get("status") == "rejected",
            external_payload.get("browser_run") is None,
            external_result is not None,
            external_result_payload.get("browser_run") is None,
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
                "direct_task": direct_payload,
                "service_task": service_payload,
                "external_domain": external_payload,
                "schema_rejections": {
                    "missing_field": missing_field.json(),
                    "unknown_topic": unknown_topic.json(),
                    "too_many_pages": too_many_pages.json(),
                },
                "authentication": {
                    "missing_token_status": missing_token.status_code,
                    "invalid_token_status": invalid_token.status_code,
                },
                "http_statuses": {
                    "health": health.status_code,
                    "direct_task": direct.status_code,
                    "service_task": service.status_code,
                    "external_domain": external.status_code,
                    "missing_field": missing_field.status_code,
                    "unknown_topic": unknown_topic.status_code,
                    "too_many_pages": too_many_pages.status_code,
                },
                "browser_run_counts": {
                    "before_health": initial_run_count,
                    "after_health": run_count_after_health,
                    "after_submissions": final_run_count,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
