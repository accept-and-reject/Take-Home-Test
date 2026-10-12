"""Run all fixed showcase scenarios or one selected scenario.

Design source: ``doc/05_交付系統/02_使用者手冊.md``（展示操作）.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from demo.browser_client import run_browser_scenario
from demo.mcp_agent import MCPAgentSession


SCENARIO_ORDER = (
    "question-1-01",
    "question-1-02",
    "integrated-01",
    "question-3-01",
    "question-3-02",
)
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
BROWSER_SCENARIOS = frozenset(
    {"integrated-01", "question-3-01", "question-3-02"}
)
ACTION_LABELS = {
    "open": "開啟官網頁面",
    "inspect": "讀取頁面內容與連結",
    "follow_link": "前往下一個官網頁面",
    "capture": "記錄研究證據",
    "skip": "略過不可執行的動作",
    "error": "記錄執行錯誤",
    "finish": "完成本次研究",
}
STATUS_LABELS = {
    "success": "成功",
    "skipped": "已略過",
    "failed": "失敗",
}
RUN_STATUS_LABELS = {
    "completed": "完成",
    "partial": "部分完成",
    "failed": "失敗",
    "rejected": "已拒絕",
    "running": "執行中",
    "pending": "等待執行",
}
TOPIC_LABELS = {
    "company_overview": "公司介紹",
    "products": "主要產品",
    "benefits": "福利制度",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the five fixed take-home showcase scenarios."
    )
    parser.add_argument(
        "--scenario",
        choices=SCENARIO_ORDER,
        help="Run only one scenario instead of the complete showcase.",
    )
    parser.add_argument(
        "--mcp-url",
        default=_knowledge_mcp_url(),
        help="Knowledge MCP public URL ending in /mcp.",
    )
    parser.add_argument(
        "--browser-url",
        default=os.getenv(
            "BROWSER_PUBLIC_BASE_URL", "http://host.docker.internal:8002"
        ),
        help="Browser Service public base URL.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv(
            "DEMO_AGENT_MODEL",
            os.getenv("KNOWLEDGE_AGENT_MODEL", "gpt-6-luna"),
        ),
        help="OpenAI model used by MCP Agent scenarios.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(os.getenv("DEMO_LOG_DIR", "/app/logs/demo")),
        help="Root directory for generated demo logs.",
    )
    return parser.parse_args()


def _knowledge_mcp_url() -> str:
    base_url = os.getenv(
        "KNOWLEDGE_PUBLIC_BASE_URL",
        "http://host.docker.internal:8001",
    ).rstrip("/")
    if base_url.endswith("/mcp"):
        return base_url
    return base_url + "/mcp"


async def _run() -> int:
    arguments = _arguments()
    scenario_ids = (
        (arguments.scenario,) if arguments.scenario else SCENARIO_ORDER
    )
    run_directory = arguments.log_dir / _run_id()
    try:
        run_directory.mkdir(parents=True, exist_ok=False)
    except PermissionError as exc:
        raise RuntimeError(
            "無法寫入 Demo Log。請確認主機 logs/demo 可寫，且 "
            "docker run 已加入 --user \"$(id -u):$(id -g)\" 與對應 Bind Mount。"
        ) from exc

    print("[DEMO] AI Application Engineer Home Test")
    print(f"[LOGS] {run_directory}")
    failures = 0
    for scenario_id in scenario_ids:
        scenario = _load_scenario(scenario_id)
        scenario_directory = run_directory / scenario_id
        scenario_directory.mkdir()
        _write_json(scenario_directory / "input.json", scenario)
        print(f"\n== {scenario_id}｜{scenario['title']} ==")
        try:
            if scenario["mode"] == "mcp_agent":
                transcript, run_logs = await _run_agent_scenario(
                    scenario,
                    mcp_url=arguments.mcp_url,
                    model=arguments.model,
                )
            elif scenario["mode"] == "browser_client":
                transcript, run_logs = _run_browser_client_scenario(
                    scenario,
                    browser_url=arguments.browser_url,
                )
            else:
                raise ValueError(
                    f"不支援的展示模式：{scenario.get('mode')}"
                )
            _write_text(scenario_directory / "transcript.md", transcript)
            if run_logs is not None:
                _write_text(
                    scenario_directory / "run-logs.md",
                    run_logs,
                )
            print("[OK] 情境完成")
        except Exception as exc:
            failures += 1
            error = {
                "scenario_id": scenario_id,
                "error_type": type(exc).__name__,
                "message": _safe_error_message(exc),
            }
            _write_json(scenario_directory / "error.json", error)
            _write_text(
                scenario_directory / "transcript.md",
                _error_transcript(scenario, error),
            )
            if scenario_id in BROWSER_SCENARIOS:
                _write_text(
                    scenario_directory / "run-logs.md",
                    _error_run_logs(scenario, error),
                )
            print(
                f"[FAILED] {type(exc).__name__}: {error['message']}",
                file=sys.stderr,
            )

    print(f"\n[LOGS] {run_directory}")
    if failures:
        print(f"[FAILED] {failures} 個展示情境未完成。", file=sys.stderr)
        return 1
    print("[DONE] 所有指定展示情境皆已完成。")
    return 0


async def _run_agent_scenario(
    scenario: dict[str, Any],
    *,
    mcp_url: str,
    model: str,
) -> tuple[str, str | None]:
    mcp_token = _required_env("MCP_BEARER_TOKEN")
    _required_env("OPENAI_API_KEY")
    transcript = [
        f"# {scenario['title']}",
        "",
        "- 執行模式：Knowledge MCP Agent",
        "- Session：僅限本情境、程式記憶體內",
        "",
    ]

    def progress(tool_name: str, status: str) -> None:
        if status == "running":
            print(f"[MCP] {tool_name} ...")
        elif status == "success":
            print(f"[MCP] {tool_name}: success")
        else:
            print(f"[MCP] {tool_name}: failed", file=sys.stderr)

    session = MCPAgentSession(
        mcp_url=mcp_url,
        mcp_token=mcp_token,
        model=model,
        reasoning_effort=os.getenv("DEMO_AGENT_REASONING_EFFORT", "low"),
        progress=progress,
    )
    browser_activities: list[dict[str, Any]] = []
    try:
        for index, turn in enumerate(scenario["turns"], start=1):
            user_input = str(turn["input"])
            print(f"\n[USER {index}]\n{user_input}")
            result = await session.run_turn(user_input)
            if (
                scenario["id"] == "integrated-01"
                and index == 2
                and not any(
                    call["tool_name"] == "execute_action_item"
                    for call in result.tool_calls
                )
            ):
                raise RuntimeError(
                    "使用者已明確要求執行，但 Agent 未呼叫 execute_action_item。"
                )
            print(f"\n[AGENT {index}]\n{result.output_text}")
            transcript.extend(
                (
                    f"## 使用者輸入 {index}",
                    "",
                    user_input,
                    "",
                    f"## Agent 輸出 {index}",
                    "",
                    result.output_text,
                    "",
                    "### Tool 執行摘要",
                    "",
                )
            )
            if result.tool_calls:
                transcript.extend(
                    f"- `{call['tool_name']}`：{call['status']}"
                    for call in result.tool_calls
                )
            else:
                transcript.append("- 本回合未呼叫 Tool")
            transcript.append("")
            for call in result.tool_calls:
                activity = call.get("browser_activity")
                if isinstance(activity, dict):
                    browser_activities.append(activity)
    finally:
        await session.close()

    return (
        "\n".join(transcript).rstrip() + "\n",
        (
            _format_browser_run_logs(scenario["title"], browser_activities)
            if browser_activities
            else None
        ),
    )


def _run_browser_client_scenario(
    scenario: dict[str, Any],
    *,
    browser_url: str,
) -> tuple[str, str]:
    print(f"\n[USER]\n{scenario['input']}")
    result = run_browser_scenario(
        scenario,
        browser_base_url=browser_url,
        browser_token=_required_env("BROWSER_API_TOKEN"),
        start_url=_required_env("START_URL"),
        allowed_domain=_required_env("ALLOWED_DOMAIN"),
        timeout_seconds=int(
            os.getenv("BROWSER_REQUEST_TIMEOUT_SECONDS", "180")
        ),
    )
    print(f"\n[BROWSER RESULT]\n{result.output_text}")
    transcript = "\n".join(
        (
            f"# {scenario['title']}",
            "",
            "- 執行模式：Direct Browser Test Client",
            "- Knowledge MCP：未使用",
            "",
            "## 使用者輸入",
            "",
            str(scenario["input"]),
            "",
            "## Browser Task 輸出",
            "",
            result.output_text,
            "",
        )
    )
    return (
        transcript,
        _format_browser_run_logs(scenario["title"], [result.payload]),
    )


def _format_browser_run_logs(
    title: str,
    activities: list[dict[str, Any]],
) -> str:
    lines = [f"# {title}：Browser Run Logs", ""]
    for activity_index, raw_activity in enumerate(activities, start=1):
        activity = _normalized_browser_activity(raw_activity)
        if len(activities) > 1:
            lines.extend((f"## Browser Run {activity_index}", ""))
        lines.extend(
            (
                "## 執行摘要" if len(activities) == 1 else "### 執行摘要",
                "",
                "- 結果："
                + RUN_STATUS_LABELS.get(
                    str(activity["status"]),
                    str(activity["status"]),
                ),
                f"- 目標頁面：{activity['target_url'] or '無'}",
                "- 造訪頁數："
                + (
                    str(activity["visited_page_count"])
                    if activity["visited_page_count"] is not None
                    else "0"
                ),
                "",
                "## 動作紀錄" if len(activities) == 1 else "### 動作紀錄",
                "",
            )
        )
        action_logs = activity["action_logs"]
        if action_logs:
            for position, action in enumerate(action_logs, start=1):
                sequence = action.get("sequence") or position
                action_type = str(action.get("action_type") or "unknown")
                status = str(action.get("status") or "unknown")
                lines.append(
                    f"{sequence}. {ACTION_LABELS.get(action_type, action_type)}"
                    f"（{STATUS_LABELS.get(status, status)}）"
                )
                if action.get("target_url"):
                    lines.append(f"   - 頁面：{action['target_url']}")
                if action.get("reason"):
                    lines.append(
                        f"   - 目的：{_readable_reason(str(action['reason']))}"
                    )
                if action.get("detail"):
                    readable_detail = _readable_detail(
                        action_type,
                        str(action["detail"]),
                    )
                    if readable_detail:
                        lines.append(f"   - 結果：{readable_detail}")
                lines.append("")
        elif activity.get("validation_status") == "rejected":
            lines.extend(
                (
                    "1. 接收 Browser Task 請求（成功）",
                    "",
                    "2. 驗證目標網域（已拒絕）",
                    "   - 原因："
                    + _readable_rejection_reason(
                        str(
                            activity.get("rejection_reason")
                            or "請求超出允許範圍"
                        )
                    ),
                    "",
                    "3. 停止執行（成功）",
                    "   - 未啟動 Chromium、未建立 Browser Run、未造訪頁面。",
                    "",
                )
            )
        else:
            lines.extend(("- API 未回傳可顯示的動作紀錄。", ""))
    return "\n".join(lines).rstrip() + "\n"


def _normalized_browser_activity(payload: dict[str, Any]) -> dict[str, Any]:
    if "response" in payload:
        response = payload.get("response") or {}
        request = payload.get("request") or {}
        task = request.get("browser_task") or response.get("browser_task") or {}
        execution_request = response.get("execution_request") or {}
        run = response.get("browser_run") or {}
        return {
            "status": response.get("status") or run.get("status") or "unknown",
            "target_url": task.get("start_url"),
            "validation_status": execution_request.get("validation_status"),
            "rejection_reason": execution_request.get("rejection_reason"),
            "visited_page_count": run.get("visited_page_count"),
            "action_logs": response.get("action_logs") or [],
        }
    return {
        "status": payload.get("status") or "unknown",
        "target_url": payload.get("target_url"),
        "validation_status": payload.get("validation_status"),
        "rejection_reason": payload.get("rejection_reason"),
        "visited_page_count": payload.get("visited_page_count"),
        "action_logs": payload.get("action_logs") or [],
    }


def _readable_reason(reason: str) -> str:
    translations = {
        "navigation rejected by scope guard": "目標網址未通過網域範圍驗證",
        "page budget stopped navigation": "已達頁數上限，停止開啟新頁面",
        "page could not be opened or inspected": "無法開啟或讀取目標頁面",
        "redirect resolved to an already visited page": "重導向已造訪過的頁面",
        "redirect target rejected by scope guard": "重導向目標未通過網域範圍驗證",
        "opened an allowed official-site page": "開啟通過驗證的官方網站頁面",
        "read visible text and links": "讀取頁面上可見文字與連結",
        "agent could not analyze the inspected page": "Agent 無法分析目前頁面",
        "page budget stopped additional navigation": "已達頁數上限，停止額外導覽",
        "selected link failed final scope validation": "選定的連結未通過最終網域驗證",
        "allowed internal link candidates were exhausted": "允許的官網連結已全部檢查",
        "browser workflow stopped unexpectedly": "Browser Workflow 異常停止",
        "browser run finalized from persisted findings": "根據已保存的研究結果完成本次執行",
        "applied evidence and navigation safeguards": "套用證據與導覽安全檢查",
    }
    if reason.startswith("captured "):
        topic = reason.removeprefix("captured ")
        return f"記錄{TOPIC_LABELS.get(topic, topic)}的可用證據"
    if reason.startswith("finalized unresolved "):
        topic = reason.removeprefix("finalized unresolved ")
        return f"將未找到足夠證據的{TOPIC_LABELS.get(topic, topic)}標記為未找到"
    return translations.get(reason, reason)


def _readable_detail(action_type: str, detail: str) -> str | None:
    if action_type == "capture":
        return None
    if action_type == "inspect":
        match = re.fullmatch(
            r"title=(.+); visible_characters=(\d+); visible_links=(\d+)",
            detail,
        )
        if match:
            return (
                f"頁面標題 {match.group(1)}，讀取 "
                f"{match.group(2)} 個可見字元與 {match.group(3)} 個連結"
            )
    if detail == "page budget reached":
        return "已達本次允許的最大造訪頁數"
    if detail.startswith("status="):
        status = detail.removeprefix("status=")
        return "最終狀態為" + RUN_STATUS_LABELS.get(status, status)
    return detail


def _readable_rejection_reason(reason: str) -> str:
    translations = {
        "start_url is not the configured official homepage": "起始網址不是設定的官方首頁",
        "allowed_domain is outside the configured official domain": "請求網域不在允許的官方網域範圍內",
    }
    return "；".join(
        translations.get(item.strip(), item.strip())
        for item in reason.split(";")
        if item.strip()
    )


def _load_scenario(scenario_id: str) -> dict[str, Any]:
    path = SCENARIO_DIR / f"{scenario_id}.json"
    with path.open("r", encoding="utf-8") as file:
        scenario = json.load(file)
    if not isinstance(scenario, dict) or scenario.get("id") != scenario_id:
        raise ValueError(f"情境檔格式錯誤：{path.name}")
    return scenario


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必要環境變數：{name}")
    return value


def _run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{secrets.token_hex(2)}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _error_transcript(
    scenario: dict[str, Any],
    error: dict[str, str],
) -> str:
    return "\n".join(
        (
            f"# {scenario['title']}",
            "",
            "## 執行失敗",
            "",
            f"- 類型：{error['error_type']}",
            f"- 訊息：{error['message']}",
            "",
        )
    )


def _error_run_logs(
    scenario: dict[str, Any],
    error: dict[str, str],
) -> str:
    return "\n".join(
        (
            f"# {scenario['title']}：Browser Run Logs",
            "",
            "## 動作紀錄",
            "",
            "1. 接收展示情境（成功）",
            "",
            "2. 送出或執行 Browser Task（失敗）",
            f"   - 錯誤類型：{error['error_type']}",
            f"   - 錯誤說明：{error['message']}",
            "",
            "3. 結束本次展示（已停止）",
            "   - 未將失敗記錄成成功研究結果。",
            "",
        )
    )


def _safe_error_message(error: Exception) -> str:
    message = str(error)
    for name in (
        "OPENAI_API_KEY",
        "MCP_BEARER_TOKEN",
        "BROWSER_API_TOKEN",
    ):
        secret = os.getenv(name, "")
        if secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(
        r"(?i)authorization\s*[:=]\s*bearer\s+[^\s,}\]]+",
        "Authorization: Bearer [REDACTED]",
        message,
    )
    message = re.sub(r"\bsk-[A-Za-z0-9_-]+", "sk-[REDACTED]", message)
    return message


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
