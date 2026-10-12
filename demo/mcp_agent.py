"""Run one short-lived OpenAI Agent session against the Knowledge MCP Server.

Design source: ``doc/05_交付系統/02_使用者手冊.md``（展示操作）.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Iterable

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from openai import AsyncOpenAI


READ_ONLY_TOOLS = frozenset({"search_meetings", "get_action_item"})
EXECUTION_TOOLS = frozenset({"execute_action_item"})
SUPPORTED_TOOLS = READ_ONLY_TOOLS | EXECUTION_TOOLS
MAX_TOOL_ROUNDS = 8
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)

AGENT_INSTRUCTIONS = """
你是會議知識與公司研究助理，只能使用本次提供的 Knowledge MCP Tools。

規則：
1. 使用者詢問會議、決策、待辦或文件內容時，必須先查詢 MCP，不得憑印象回答。
2. 會議內容必須列出可供人工核對的文件名稱與頁碼／投影片位置。
3. 證據不足時，直接說明文件沒有足夠證據，不得推測。
4. execute_action_item 只有在本回合確實提供該 Tool，且使用者明確要求執行已選定待辦時才可呼叫。
5. 最終回答只呈現使用者需要的資訊；不得顯示 UUID、request_id、chunk_id、action_item_id、run_id、分數、原始 JSON 或 Action Log。
6. 官網研究結果應摘要說明 found／not_found，並列出官方來源 URL；不可把未找到的內容寫成已找到。
7. 使用繁體中文，內容簡潔、自然，避免描述系統內部實作。
""".strip()


@dataclass(frozen=True)
class AgentTurnResult:
    """One user-visible answer and its developer-facing tool trace."""

    user_input: str
    output_text: str
    tool_calls: list[dict[str, Any]]


class MCPAgentSession:
    """Keep only the current scenario conversation in process memory."""

    def __init__(
        self,
        *,
        mcp_url: str,
        mcp_token: str,
        model: str,
        reasoning_effort: str = "low",
        progress: Callable[[str, str], None] | None = None,
    ) -> None:
        self._mcp_url = mcp_url
        self._mcp_token = mcp_token
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._progress = progress
        self._history: list[dict[str, str]] = []
        self._execution_ready = False
        self._openai = AsyncOpenAI()

    async def close(self) -> None:
        """Release the short-lived OpenAI HTTP client."""

        await self._openai.close()

    async def run_turn(
        self,
        user_input: str,
    ) -> AgentTurnResult:
        """Let the model choose from the permitted MCP tools for one turn."""

        permitted = set(READ_ONLY_TOOLS)
        if self._execution_ready:
            permitted.update(EXECUTION_TOOLS)

        async with _mcp_client(self._mcp_url, self._mcp_token) as client:
            listed = await client.list_tools()
            tool_definitions = _function_tools(listed.tools, permitted)
            available_names = {tool["name"] for tool in tool_definitions}
            missing = permitted - available_names
            if missing:
                raise RuntimeError(
                    "Knowledge MCP 缺少展示需要的 Tool："
                    + ", ".join(sorted(missing))
                )

            input_items: list[Any] = [*self._history]
            input_items.append({"role": "user", "content": user_input})
            trace: list[dict[str, Any]] = []

            for _ in range(MAX_TOOL_ROUNDS):
                response = await self._openai.responses.create(
                    model=self._model,
                    instructions=AGENT_INSTRUCTIONS,
                    input=input_items,
                    tools=tool_definitions,
                    parallel_tool_calls=False,
                    reasoning={"effort": self._reasoning_effort},
                    store=False,
                )
                if response.status != "completed":
                    raise RuntimeError(
                        f"OpenAI Response 未完成：{response.status}"
                    )

                input_items.extend(
                    item.model_dump(exclude_none=True)
                    for item in response.output
                )
                calls = [
                    item
                    for item in response.output
                    if item.type == "function_call"
                ]
                if not calls:
                    if not trace:
                        raise RuntimeError(
                            "Agent 未使用 Knowledge MCP，無法驗證回答來源。"
                        )
                    answer = _sanitize_user_output(response.output_text or "")
                    if not answer:
                        raise RuntimeError("Agent 沒有產生可顯示的回答。")
                    self._history.extend(
                        (
                            {"role": "user", "content": user_input},
                            {"role": "assistant", "content": answer},
                        )
                    )
                    return AgentTurnResult(
                        user_input=user_input,
                        output_text=answer,
                        tool_calls=trace,
                    )

                for call in calls:
                    if call.name not in permitted:
                        raise RuntimeError(
                            f"Agent 嘗試呼叫未允許的 Tool：{call.name}"
                        )
                    arguments = _arguments_object(call.arguments)
                    _report(self._progress, call.name, "running")
                    try:
                        payload = _tool_payload(
                            await client.call_tool(call.name, arguments)
                        )
                    except Exception:
                        _report(self._progress, call.name, "failed")
                        raise
                    _report(self._progress, call.name, "success")
                    if _contains_executable_company_action(payload):
                        self._execution_ready = True
                    trace.append(
                        {
                            "tool_name": call.name,
                            "status": "success",
                            "browser_activity": _browser_activity(payload),
                        }
                    )
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": json.dumps(
                                payload,
                                ensure_ascii=False,
                                default=str,
                            ),
                        }
                    )

        raise RuntimeError("Agent 超過允許的 MCP Tool 呼叫輪數。")


@asynccontextmanager
async def _mcp_client(url: str, token: str) -> AsyncIterator[Client]:
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30.0, read=300.0),
    ) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport) as client:
            yield client


def _function_tools(
    tools: Iterable[Any],
    permitted: set[str],
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for tool in tools:
        data = tool.model_dump(by_alias=True, exclude_none=True)
        name = str(data.get("name", ""))
        if name not in SUPPORTED_TOOLS or name not in permitted:
            continue
        schema = data.get("inputSchema") or data.get("input_schema") or {}
        definitions.append(
            {
                "type": "function",
                "name": name,
                "description": str(data.get("description") or name),
                "parameters": schema,
                "strict": False,
            }
        )
    return definitions


def _arguments_object(raw_arguments: str) -> dict[str, Any]:
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Agent 產生了無效的 Tool 參數。") from exc
    if not isinstance(arguments, dict):
        raise RuntimeError("Agent Tool 參數必須是 JSON Object。")
    return arguments


def _tool_payload(result: Any) -> dict[str, Any]:
    if result.is_error:
        raise RuntimeError("Knowledge MCP Tool 回傳錯誤。")
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for content in result.content:
        raw_text = getattr(content, "text", None)
        if not isinstance(raw_text, str):
            continue
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("Knowledge MCP Tool 沒有回傳結構化結果。")


def _browser_activity(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a readable Browser execution source without internal IDs."""

    if not any(
        key in payload
        for key in ("browser_run", "action_logs", "findings")
    ):
        return None
    run = payload.get("browser_run") or {}
    task = payload.get("browser_task") or {}
    execution_request = payload.get("execution_request") or {}
    return {
        "status": payload.get("status") or run.get("status"),
        "target_url": task.get("start_url"),
        "validation_status": execution_request.get("validation_status"),
        "rejection_reason": execution_request.get("rejection_reason"),
        "visited_page_count": run.get("visited_page_count"),
        "action_logs": [
            {
                "sequence": item.get("sequence"),
                "action_type": item.get("action_type"),
                "target_url": item.get("target_url"),
                "reason": item.get("reason"),
                "status": item.get("status"),
                "detail": item.get("detail"),
                "occurred_at": item.get("occurred_at"),
            }
            for item in payload.get("action_logs") or []
            if isinstance(item, dict)
        ],
    }


def _contains_executable_company_action(payload: Any) -> bool:
    if isinstance(payload, dict):
        if (
            payload.get("action_kind") == "company_research"
            and payload.get("status") in {"open", "in_progress"}
        ):
            return True
        return any(
            _contains_executable_company_action(value)
            for value in payload.values()
        )
    if isinstance(payload, list):
        return any(_contains_executable_company_action(value) for value in payload)
    return False


def _sanitize_user_output(value: str) -> str:
    return UUID_PATTERN.sub("[內部識別碼已隱藏]", value.strip())


def _report(
    progress: Callable[[str, str], None] | None,
    tool_name: str,
    status: str,
) -> None:
    if progress is not None:
        progress(tool_name, status)
