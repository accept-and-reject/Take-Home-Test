"""Manually verify authenticated Knowledge MCP queries over Streamable HTTP."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Knowledge MCP tools, evidence handling, and auth."
    )
    parser.add_argument("--url", required=True, help="Full MCP URL ending in /mcp")
    parser.add_argument("--token", required=True)
    return parser.parse_args()


async def _connect(url: str, token: str) -> tuple[list[str], dict[str, Any]]:
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30.0, read=300.0),
    ) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport) as client:
            listed = await client.list_tools()
            tool_names = sorted(tool.name for tool in listed.tools)

            search = await client.call_tool(
                "search_meetings",
                {
                    "query": (
                        "請整理 Display Driver 專案最近兩週的討論、"
                        "決策與未完成事項，並列出可核對來源。"
                    )
                },
            )
            search_payload = _payload(search)
            company_action = next(
                (
                    item
                    for item in search_payload.get("action_items", [])
                    if item.get("action_kind") == "company_research"
                ),
                None,
            )
            if company_action is None:
                raise RuntimeError("search returned no company_research ActionItem")

            action = await client.call_tool(
                "get_action_item",
                {"action_item_id": company_action["action_item_id"]},
            )
            insufficient = await client.call_tool(
                "search_meetings",
                {
                    "query": (
                        "這個專案在火星辦公室的正式啟用"
                        "日期是什麼？"
                        "請只根據會議文件回答。"
                    )
                },
            )
            return tool_names, {
                "search": search_payload,
                "action_item": _payload(action),
                "insufficient_evidence": _payload(insufficient),
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


async def _main() -> int:
    arguments = _arguments()
    tool_names, tool_results = await _connect(arguments.url, arguments.token)
    http_checks = await _http_checks(arguments.url, arguments.token)

    expected_tools = ["get_action_item", "search_meetings"]
    passed = (
        tool_names == expected_tools
        and tool_results["search"].get("status") == "completed"
        and tool_results["action_item"].get("status") == "found"
        and tool_results["insufficient_evidence"].get("status")
        == "insufficient_evidence"
        and http_checks["missing_token_status"] == 401
        and http_checks["wrong_token_status"] == 401
        and http_checks["health_status"] == 200
    )
    print(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
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
