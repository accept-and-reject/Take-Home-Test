"""Manually verify authenticated Knowledge MCP behavior by test case."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


EXPECTED_TOOLS = [
    "execute_action_item",
    "get_action_item",
    "search_meetings",
]
MEETING_QUERY = (
    "請整理 Display Driver 專案在 2026-09-18 至 2026-09-25 的討論、"
    "決策與未完成事項，並列出可核對來源。"
)
INSUFFICIENT_QUERY = (
    "這個專案在火星辦公室的正式啟用日期是什麼？請只根據會議文件回答。"
)
POST_EXECUTION_QUERY = (
    "請再次整理 Display Driver 專案在 2026-09-18 至 2026-09-25 "
    "的未完成事項與來源。"
)
CASE_NAMES = (
    "flow2-case1",
    "flow2-case2",
    "flow2-case3",
    "flow2-case4",
    "flow2-case5",
    "flow3-case4",
    "flow3-case5",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Knowledge MCP behavior for one formal test case."
    )
    parser.add_argument("--url", required=True, help="Full MCP URL ending in /mcp")
    parser.add_argument("--token", required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--case",
        dest="case_name",
        choices=CASE_NAMES,
        help="Run one formal test case and return an independent result.",
    )
    selection.add_argument(
        "--execution",
        choices=("available", "unavailable"),
        help="Legacy combined check with optional Browser execution.",
    )
    return parser.parse_args()


@asynccontextmanager
async def _mcp_client(url: str, token: str) -> Any:
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30.0, read=300.0),
    ) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport) as client:
            yield client


async def _search(client: Client, query: str = MEETING_QUERY) -> dict[str, Any]:
    return _payload(
        await client.call_tool(
            "search_meetings",
            {"query": query},
        )
    )


def _company_action(search_payload: dict[str, Any]) -> dict[str, Any]:
    action = next(
        (
            item
            for item in search_payload.get("action_items", [])
            if item.get("action_kind") == "company_research"
        ),
        None,
    )
    if action is None:
        raise RuntimeError("search returned no company_research ActionItem")
    return action


async def _run_case(
    url: str,
    token: str,
    case_name: str,
) -> tuple[bool, dict[str, Any]]:
    if case_name == "flow2-case5":
        http_checks = await _http_checks(url, token)
        passed = all(
            (
                http_checks["missing_token_status"] == 401,
                http_checks["wrong_token_status"] == 401,
                http_checks["health_status"] == 200,
            )
        )
        return passed, {"http_checks": http_checks}

    async with _mcp_client(url, token) as client:
        listed = await client.list_tools()
        tool_names = sorted(tool.name for tool in listed.tools)
        if case_name == "flow2-case1":
            search = await _search(client)
            results = search.get("results", [])
            passed = all(
                (
                    tool_names == EXPECTED_TOOLS,
                    search.get("status") == "completed",
                    bool(results),
                    all(
                        item.get("source_title")
                        and item.get("locator_type")
                        and item.get("locator_value")
                        for item in results
                    ),
                )
            )
            return passed, {"tools": tool_names, "search": search}

        if case_name == "flow2-case2":
            search = await _search(client)
            company_action = _company_action(search)
            action = _payload(
                await client.call_tool(
                    "get_action_item",
                    {"action_item_id": company_action["action_item_id"]},
                )
            )
            action_item = action.get("action_item") or {}
            passed = all(
                (
                    action.get("status") == "found",
                    action_item.get("action_kind") == "company_research",
                    action_item.get("target_company") == "瑞鼎科技",
                    set(action_item.get("requested_topics", []))
                    == {"company_overview", "products", "benefits"},
                    len(action_item.get("evidence", [])) >= 2,
                )
            )
            return passed, {"search": search, "action_item": action}

        if case_name == "flow2-case3":
            insufficient = await _search(client, INSUFFICIENT_QUERY)
            return (
                insufficient.get("status") == "insufficient_evidence",
                {"insufficient_evidence": insufficient},
            )

        if case_name == "flow2-case4":
            search = await _search(client)
            health = await _health_check(url)
            health_body = health["body"]
            passed = all(
                (
                    search.get("status") == "completed",
                    bool(search.get("results")),
                    health["status"] == 200,
                    health_body.get("status") == "healthy",
                    health_body.get("browser_integration") == "degraded",
                )
            )
            return passed, {"search": search, "health": health}

        search = await _search(client)
        company_action = _company_action(search)
        execution = _payload(
            await client.call_tool(
                "execute_action_item",
                {"action_item_id": company_action["action_item_id"]},
            )
        )
        if case_name == "flow3-case4":
            return _available_execution_is_valid(execution), {
                "search": search,
                "execution": execution,
            }

        post_search = await _search(client, POST_EXECUTION_QUERY)
        passed = all(
            (
                _unavailable_execution_is_valid(execution),
                post_search.get("status") == "completed",
            )
        )
        return passed, {
            "search": search,
            "execution": execution,
            "post_execution_search": post_search,
        }


async def _connect(
    url: str,
    token: str,
    execution_expectation: str | None,
) -> tuple[list[str], dict[str, Any]]:
    async with _mcp_client(url, token) as client:
        listed = await client.list_tools()
        tool_names = sorted(tool.name for tool in listed.tools)
        search_payload = await _search(client)
        company_action = _company_action(search_payload)
        action = _payload(
            await client.call_tool(
                "get_action_item",
                {"action_item_id": company_action["action_item_id"]},
            )
        )
        insufficient = await _search(client, INSUFFICIENT_QUERY)
        execution_payload: dict[str, Any] | None = None
        post_execution_search: dict[str, Any] | None = None
        if execution_expectation is not None:
            execution_payload = _payload(
                await client.call_tool(
                    "execute_action_item",
                    {"action_item_id": company_action["action_item_id"]},
                )
            )
            post_execution_search = await _search(client, POST_EXECUTION_QUERY)
        return tool_names, {
            "search": search_payload,
            "action_item": action,
            "insufficient_evidence": insufficient,
            "execution": execution_payload,
            "post_execution_search": post_execution_search,
        }


def _payload(result: Any) -> dict[str, Any]:
    if result.is_error:
        raise RuntimeError("MCP tool returned an error")
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for content in result.content:
        text = getattr(content, "text", None)
        if isinstance(text, str):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    raise RuntimeError("MCP tool returned no structured object")


async def _health_check(url: str) -> dict[str, Any]:
    health_url = url.rstrip("/").removesuffix("/mcp").rstrip("/") + "/health"
    async with httpx2.AsyncClient(timeout=10.0) as client:
        response = await client.get(health_url)
    return {"status": response.status_code, "body": response.json()}


async def _http_checks(url: str, token: str) -> dict[str, Any]:
    wrong_token = secrets.token_urlsafe(32)
    if secrets.compare_digest(wrong_token, token):
        wrong_token += "-wrong"
    normalized_url = url.rstrip("/")
    health_url = normalized_url.removesuffix("/mcp").rstrip("/") + "/health"
    async with httpx2.AsyncClient(timeout=10.0) as client:
        missing = await client.post(url, json={})
        wrong = await client.post(
            url,
            headers={"Authorization": f"Bearer {wrong_token}"},
            json={},
        )
        health = await client.get(health_url)
    return {
        "missing_token_status": missing.status_code,
        "wrong_token_status": wrong.status_code,
        "health_status": health.status_code,
        "health": health.json(),
    }


def _available_execution_is_valid(execution: dict[str, Any]) -> bool:
    browser_task = execution.get("browser_task") or {}
    execution_request = execution.get("execution_request") or {}
    browser_run = execution.get("browser_run") or {}
    findings = execution.get("findings") or []
    action_logs = execution.get("action_logs") or []
    action_item = execution.get("action_item") or {}
    return all(
        (
            execution.get("status") in {"completed", "partial"},
            browser_task.get("task_source") == "action_item",
            browser_task.get("source_action_item_id")
            == action_item.get("action_item_id"),
            execution_request.get("browser_task_id")
            == browser_task.get("browser_task_id"),
            browser_run.get("execution_request_id")
            == execution_request.get("execution_request_id"),
            len(findings) == 3,
            bool(action_logs),
            action_logs[-1].get("action_type") == "finish",
        )
    )


def _unavailable_execution_is_valid(execution: dict[str, Any]) -> bool:
    return all(
        (
            execution.get("status") == "browser_service_unavailable",
            execution.get("execution_request") is None,
            execution.get("browser_run") is None,
            execution.get("findings") == [],
            execution.get("action_logs") == [],
        )
    )


def _execution_is_valid(
    tool_results: dict[str, Any],
    expectation: str | None,
) -> bool:
    if expectation is None:
        return True
    execution = tool_results.get("execution") or {}
    post_search = tool_results.get("post_execution_search") or {}
    if post_search.get("status") != "completed":
        return False
    if expectation == "unavailable":
        return _unavailable_execution_is_valid(execution)
    return _available_execution_is_valid(execution)


async def _main() -> int:
    arguments = _arguments()
    if arguments.case_name:
        passed, result = await _run_case(
            arguments.url,
            arguments.token,
            arguments.case_name,
        )
        print(
            json.dumps(
                {
                    "status": "passed" if passed else "failed",
                    "case": arguments.case_name,
                    "result": result,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if passed else 1

    tool_names, tool_results = await _connect(
        arguments.url,
        arguments.token,
        arguments.execution,
    )
    http_checks = await _http_checks(arguments.url, arguments.token)
    base_passed = all(
        (
            tool_names == EXPECTED_TOOLS,
            tool_results["search"].get("status") == "completed",
            tool_results["action_item"].get("status") == "found",
            tool_results["insufficient_evidence"].get("status")
            == "insufficient_evidence",
            http_checks["missing_token_status"] == 401,
            http_checks["wrong_token_status"] == 401,
            http_checks["health_status"] == 200,
        )
    )
    passed = base_passed and _execution_is_valid(
        tool_results,
        arguments.execution,
    )
    print(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "execution_expectation": arguments.execution,
                "tools": tool_names,
                "tool_results": tool_results,
                "http_checks": http_checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
