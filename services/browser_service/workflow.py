"""Orchestrate one constrained Browser Run across execution roles.

Design: ``doc/02_系統設計/04_時序圖.md`` — 流程 3.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from playwright.sync_api import Route, sync_playwright

from services.browser_service.browser_execution.browser_research_role import (
    PageDecisionResult,
    VisibleLink,
    capture_research_finding,
    inspect_official_page,
    select_internal_link,
)
from services.browser_service.browser_execution.browser_scope_guard_role import (
    enforce_page_budget,
    validate_navigation_target,
)
from services.browser_service.browser_execution.execution_record_role import (
    finalize_browser_run,
    record_browser_action,
)
from services.browser_service.store import BrowserRunStore
from shared.contracts import (
    BrowserActionStatus,
    BrowserActionType,
    BrowserRun,
    BrowserRunStatus,
    FindingStatus,
    ResearchTopic,
)
from shared.openai_gateway import OpenAIGateway
from shared.settings import BrowserSettings


PAGE_BUDGET_MESSAGE = (
    "No explicit supporting information was found before the page budget "
    "was reached."
)
CANDIDATES_EXHAUSTED_MESSAGE = (
    "No explicit supporting information was found after all discovered "
    "allowed internal links were exhausted."
)


def execute_browser_task(
    run_id: UUID,
    settings: BrowserSettings,
    store: BrowserRunStore,
) -> BrowserRun:
    run = store.get_browser_run(run_id)
    if run is None:
        raise ValueError(f"browser run {run_id} does not exist")
    request = store.get_execution_request(run.execution_request_id)
    if request is None:
        raise ValueError(f"execution request {run.execution_request_id} is missing")
    task = store.get_browser_task(request.browser_task_id)
    if task is None:
        raise ValueError(f"browser task {request.browser_task_id} is missing")

    running = run.model_copy(
        update={
            "status": BrowserRunStatus.RUNNING,
            "started_at": datetime.now(UTC),
            "completed_at": None,
            "error_summary": None,
        }
    )
    store.save_browser_run(running)

    visited_urls: set[str] = set()
    candidate_queue: list[VisibleLink] = []
    queued_urls: set[str] = set()
    completed_topics: set[ResearchTopic] = set()
    visited_page_count = 0
    last_page_url: str | None = None
    error_summary: str | None = None
    stop_message: str | None = None
    gateway = OpenAIGateway(settings.openai_api_key)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.route(
                    "**/*",
                    lambda route: _guard_document_request(
                        route,
                        task.allowed_domain,
                    ),
                )
                page = context.new_page()
                candidate_url: str | None = str(task.start_url)
                while candidate_url is not None:
                    navigation = validate_navigation_target(
                        candidate_url,
                        task.allowed_domain,
                        visited_urls,
                    )
                    if not navigation.allowed or navigation.normalized_url is None:
                        error_summary = (
                            "navigation target was rejected by the scope guard: "
                            + navigation.reason
                        )
                        stop_message = error_summary
                        record_browser_action(
                            run_id,
                            BrowserActionType.ERROR,
                            BrowserActionStatus.FAILED,
                            store,
                            target_url=candidate_url,
                            reason="navigation rejected by scope guard",
                            detail=navigation.reason,
                        )
                        break
                    budget = enforce_page_budget(
                        visited_page_count,
                        task.max_pages,
                    )
                    if not budget.allowed:
                        stop_message = PAGE_BUDGET_MESSAGE
                        record_browser_action(
                            run_id,
                            BrowserActionType.SKIP,
                            BrowserActionStatus.SKIPPED,
                            store,
                            target_url=navigation.normalized_url,
                            reason="page budget stopped navigation",
                            detail=budget.reason,
                        )
                        break

                    try:
                        inspection = inspect_official_page(
                            page,
                            navigation.normalized_url,
                        )
                    except Exception as exc:
                        error_summary = f"official page inspection failed: {exc}"
                        stop_message = error_summary
                        record_browser_action(
                            run_id,
                            BrowserActionType.ERROR,
                            BrowserActionStatus.FAILED,
                            store,
                            target_url=navigation.normalized_url,
                            reason="page could not be opened or inspected",
                            detail=error_summary,
                        )
                        break

                    final_navigation = validate_navigation_target(
                        inspection.final_url,
                        task.allowed_domain,
                        visited_urls,
                    )
                    if (
                        not final_navigation.allowed
                        or final_navigation.normalized_url is None
                    ):
                        if final_navigation.reason == "URL was already visited":
                            record_browser_action(
                                run_id,
                                BrowserActionType.SKIP,
                                BrowserActionStatus.SKIPPED,
                                store,
                                target_url=inspection.final_url,
                                reason="redirect resolved to an already visited page",
                                detail=final_navigation.reason,
                            )
                            fallback_url = _take_fallback_candidate(
                                candidate_queue,
                                queued_urls,
                                task.allowed_domain,
                                visited_urls,
                            )
                            if fallback_url is None:
                                stop_message = CANDIDATES_EXHAUSTED_MESSAGE
                                record_browser_action(
                                    run_id,
                                    BrowserActionType.SKIP,
                                    BrowserActionStatus.SKIPPED,
                                    store,
                                    target_url=inspection.final_url,
                                    reason=(
                                        "allowed internal link candidates "
                                        "were exhausted"
                                    ),
                                    detail=stop_message,
                                )
                                break
                            record_browser_action(
                                run_id,
                                BrowserActionType.FOLLOW_LINK,
                                BrowserActionStatus.SUCCESS,
                                store,
                                target_url=fallback_url,
                                reason=(
                                    "scope-guarded fallback selected after "
                                    "a repeated redirect"
                                ),
                                detail="continuing with an unvisited allowed page",
                            )
                            candidate_url = fallback_url
                            continue
                        error_summary = (
                            "page redirected outside the permitted scope: "
                            + final_navigation.reason
                        )
                        stop_message = error_summary
                        record_browser_action(
                            run_id,
                            BrowserActionType.ERROR,
                            BrowserActionStatus.FAILED,
                            store,
                            target_url=navigation.normalized_url,
                            reason="redirect target rejected by scope guard",
                            detail=error_summary,
                        )
                        break

                    visited_urls.add(final_navigation.normalized_url)
                    _discard_candidate(
                        candidate_queue,
                        queued_urls,
                        final_navigation.normalized_url,
                    )
                    visited_page_count += 1
                    last_page_url = final_navigation.normalized_url
                    record_browser_action(
                        run_id,
                        BrowserActionType.OPEN,
                        BrowserActionStatus.SUCCESS,
                        store,
                        target_url=last_page_url,
                        reason="opened an allowed official-site page",
                        detail=(
                            f"HTTP {inspection.http_status}"
                            if inspection.http_status is not None
                            else "page opened without an HTTP response object"
                        ),
                    )
                    record_browser_action(
                        run_id,
                        BrowserActionType.INSPECT,
                        BrowserActionStatus.SUCCESS,
                        store,
                        target_url=last_page_url,
                        reason="read visible text and links",
                        detail=(
                            f"title={inspection.title!r}; "
                            f"visible_characters={len(inspection.visible_text)}; "
                            f"visible_links={len(inspection.links)}"
                        ),
                    )

                    unresolved_topics = [
                        topic
                        for topic in task.requested_topics
                        if topic not in completed_topics
                    ]
                    allowed_links = _allowed_unvisited_links(
                        inspection.links,
                        task.allowed_domain,
                        visited_urls,
                    )
                    _enqueue_candidates(
                        candidate_queue,
                        queued_urls,
                        allowed_links,
                        visited_urls,
                    )
                    decision = select_internal_link(
                        inspection,
                        unresolved_topics,
                        candidate_queue,
                        gateway,
                        model=settings.agent_model,
                        reasoning_effort=settings.agent_reasoning_effort,
                    )
                    if decision.error is not None:
                        error_summary = decision.error
                        stop_message = error_summary
                        record_browser_action(
                            run_id,
                            BrowserActionType.ERROR,
                            BrowserActionStatus.FAILED,
                            store,
                            target_url=last_page_url,
                            reason="agent could not analyze the inspected page",
                            detail=decision.error,
                        )
                        break
                    _persist_page_decision(
                        decision,
                        run_id,
                        store,
                        completed_topics,
                        last_page_url,
                    )

                    if completed_topics.issuperset(task.requested_topics):
                        stop_message = (
                            "All requested topics obtained explicit supporting "
                            "evidence."
                        )
                        break
                    remaining_budget = enforce_page_budget(
                        visited_page_count,
                        task.max_pages,
                    )
                    if not remaining_budget.allowed:
                        stop_message = PAGE_BUDGET_MESSAGE
                        record_browser_action(
                            run_id,
                            BrowserActionType.SKIP,
                            BrowserActionStatus.SKIPPED,
                            store,
                            target_url=last_page_url,
                            reason="page budget stopped additional navigation",
                            detail=remaining_budget.reason,
                        )
                        break
                    next_url = decision.next_url
                    if next_url is None and candidate_queue:
                        record_browser_action(
                            run_id,
                            BrowserActionType.SKIP,
                            BrowserActionStatus.SKIPPED,
                            store,
                            target_url=last_page_url,
                            reason=(
                                "agent selected no link while allowed candidates "
                                "remained"
                            ),
                            detail=(
                                f"retrying with {len(candidate_queue)} unvisited "
                                "allowed candidates"
                            ),
                        )
                        retry_topics = [
                            topic
                            for topic in task.requested_topics
                            if topic not in completed_topics
                        ]
                        retry_decision = select_internal_link(
                            inspection,
                            retry_topics,
                            candidate_queue,
                            gateway,
                            model=settings.agent_model,
                            reasoning_effort=settings.agent_reasoning_effort,
                        )
                        if retry_decision.error is not None:
                            record_browser_action(
                                run_id,
                                BrowserActionType.SKIP,
                                BrowserActionStatus.SKIPPED,
                                store,
                                target_url=last_page_url,
                                reason=(
                                    "agent navigation retry failed; using "
                                    "scope-guarded fallback"
                                ),
                                detail=retry_decision.error,
                            )
                        else:
                            _persist_page_decision(
                                retry_decision,
                                run_id,
                                store,
                                completed_topics,
                                last_page_url,
                            )
                            next_url = retry_decision.next_url
                            if completed_topics.issuperset(task.requested_topics):
                                stop_message = (
                                    "All requested topics obtained explicit "
                                    "supporting evidence."
                                )
                                break

                    selected_url: str | None = None
                    if next_url is not None:
                        selected = validate_navigation_target(
                            next_url,
                            task.allowed_domain,
                            visited_urls,
                        )
                        if selected.allowed and selected.normalized_url is not None:
                            selected_url = selected.normalized_url
                            _discard_candidate(
                                candidate_queue,
                                queued_urls,
                                selected_url,
                            )
                        else:
                            record_browser_action(
                                run_id,
                                BrowserActionType.SKIP,
                                BrowserActionStatus.SKIPPED,
                                store,
                                target_url=next_url,
                                reason="selected link failed final scope validation",
                                detail=selected.reason,
                            )

                    used_fallback = selected_url is None
                    if used_fallback:
                        selected_url = _take_fallback_candidate(
                            candidate_queue,
                            queued_urls,
                            task.allowed_domain,
                            visited_urls,
                        )
                    if selected_url is None:
                        stop_message = CANDIDATES_EXHAUSTED_MESSAGE
                        record_browser_action(
                            run_id,
                            BrowserActionType.SKIP,
                            BrowserActionStatus.SKIPPED,
                            store,
                            target_url=last_page_url,
                            reason=(
                                "allowed internal link candidates were exhausted"
                            ),
                            detail=stop_message,
                        )
                        break
                    record_browser_action(
                        run_id,
                        BrowserActionType.FOLLOW_LINK,
                        BrowserActionStatus.SUCCESS,
                        store,
                        target_url=selected_url,
                        reason=(
                            "scope-guarded fallback selected for remaining topics"
                            if used_fallback
                            else "agent selected an allowed link for remaining topics"
                        ),
                        detail=(
                            "remaining="
                            + ",".join(
                                topic.value
                                for topic in task.requested_topics
                                if topic not in completed_topics
                            )
                        ),
                    )
                    candidate_url = selected_url
                context.close()
            finally:
                browser.close()
    except Exception as exc:
        error_summary = f"browser runtime failed: {exc}"
        stop_message = error_summary
        record_browser_action(
            run_id,
            BrowserActionType.ERROR,
            BrowserActionStatus.FAILED,
            store,
            target_url=last_page_url,
            reason="browser workflow stopped unexpectedly",
            detail=error_summary,
        )

    unresolved_status = (
        FindingStatus.FAILED if error_summary else FindingStatus.NOT_FOUND
    )
    for topic in task.requested_topics:
        if topic in completed_topics:
            continue
        message = (
            error_summary
            if error_summary
            else stop_message or CANDIDATES_EXHAUSTED_MESSAGE
        )
        capture_research_finding(
            run_id,
            topic,
            unresolved_status,
            store,
            summary=None,
            evidence_quote=None,
            evidence_reason=None,
            source_urls=[],
            message=message,
        )
        record_browser_action(
            run_id,
            BrowserActionType.CAPTURE,
            (
                BrowserActionStatus.FAILED
                if error_summary
                else BrowserActionStatus.SKIPPED
            ),
            store,
            target_url=last_page_url,
            reason=f"finalized unresolved {topic.value}",
            detail=message,
        )

    finalized = finalize_browser_run(
        run_id,
        visited_page_count,
        store,
        error_summary=error_summary,
    )
    record_browser_action(
        run_id,
        BrowserActionType.FINISH,
        (
            BrowserActionStatus.FAILED
            if finalized.status is BrowserRunStatus.FAILED
            else BrowserActionStatus.SUCCESS
        ),
        store,
        reason="browser run finalized from persisted findings",
        detail=f"status={finalized.status.value}",
    )
    return finalized


def _guard_document_request(route: Route, allowed_domain: str) -> None:
    if route.request.resource_type != "document":
        route.continue_()
        return
    if route.request.method not in {"GET", "HEAD"}:
        route.abort()
        return
    validation = validate_navigation_target(
        route.request.url,
        allowed_domain,
        set(),
    )
    if validation.allowed:
        route.continue_()
    else:
        route.abort()


def _allowed_unvisited_links(
    links: list[VisibleLink],
    allowed_domain: str,
    visited_urls: set[str],
) -> list[VisibleLink]:
    accepted: list[VisibleLink] = []
    seen: set[str] = set()
    for link in links:
        validation = validate_navigation_target(
            link.url,
            allowed_domain,
            visited_urls,
        )
        if (
            not validation.allowed
            or validation.normalized_url is None
            or validation.normalized_url in seen
        ):
            continue
        seen.add(validation.normalized_url)
        accepted.append(
            VisibleLink(
                url=validation.normalized_url,
                label=link.label,
            )
        )
    return accepted


def _persist_page_decision(
    decision: PageDecisionResult,
    run_id: UUID,
    store: BrowserRunStore,
    completed_topics: set[ResearchTopic],
    page_url: str,
) -> None:
    if decision.warnings:
        record_browser_action(
            run_id,
            BrowserActionType.SKIP,
            BrowserActionStatus.SKIPPED,
            store,
            target_url=page_url,
            reason="applied evidence and navigation safeguards",
            detail="; ".join(decision.warnings),
        )
    for candidate in decision.findings:
        if candidate.topic in completed_topics:
            continue
        capture_research_finding(
            run_id,
            candidate.topic,
            FindingStatus.FOUND,
            store,
            summary=candidate.summary,
            evidence_quote=candidate.evidence_quote,
            evidence_reason=candidate.evidence_reason,
            source_urls=[page_url],
            message=None,
        )
        completed_topics.add(candidate.topic)
        record_browser_action(
            run_id,
            BrowserActionType.CAPTURE,
            BrowserActionStatus.SUCCESS,
            store,
            target_url=page_url,
            reason=f"captured {candidate.topic.value}",
            detail=json.dumps(
                {
                    "topic": candidate.topic.value,
                    "summary": candidate.summary,
                    "evidence_quote": candidate.evidence_quote,
                    "evidence_reason": candidate.evidence_reason,
                },
                ensure_ascii=False,
            ),
        )


def _enqueue_candidates(
    queue: list[VisibleLink],
    queued_urls: set[str],
    links: list[VisibleLink],
    visited_urls: set[str],
) -> None:
    for link in links:
        if link.url in visited_urls or link.url in queued_urls:
            continue
        queue.append(link)
        queued_urls.add(link.url)


def _discard_candidate(
    queue: list[VisibleLink],
    queued_urls: set[str],
    url: str,
) -> None:
    if url not in queued_urls:
        return
    queue[:] = [candidate for candidate in queue if candidate.url != url]
    queued_urls.discard(url)


def _take_fallback_candidate(
    queue: list[VisibleLink],
    queued_urls: set[str],
    allowed_domain: str,
    visited_urls: set[str],
) -> str | None:
    while queue:
        candidate = queue.pop(0)
        queued_urls.discard(candidate.url)
        validation = validate_navigation_target(
            candidate.url,
            allowed_domain,
            visited_urls,
        )
        if validation.allowed and validation.normalized_url is not None:
            return validation.normalized_url
    return None
