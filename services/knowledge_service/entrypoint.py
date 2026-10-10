"""Knowledge Service runtime with authenticated Streamable HTTP MCP."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

from services.knowledge_service.knowledge_mcp.knowledge_service_status_role import (
    SERVICE_NAME,
    report_knowledge_service_health,
)
from services.knowledge_service.knowledge_mcp.knowledge_tool_provider_role import (
    create_knowledge_mcp_server,
)
from services.knowledge_service.store import KnowledgeApplicationDataStore
from shared.settings import KnowledgeSettings


ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]],
    Awaitable[None],
]


class MCPBearerTokenMiddleware:
    """Reject unauthenticated MCP HTTP requests before protocol handling."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        path = str(scope.get("path", ""))
        if scope.get("type") == "http" and (
            path == "/mcp" or path.startswith("/mcp/")
        ):
            headers = dict(scope.get("headers", []))
            authorization = headers.get(b"authorization", b"").decode(
                "utf-8", errors="ignore"
            )
            scheme, separator, credential = authorization.partition(" ")
            authorized = (
                separator == " "
                and scheme.casefold() == "bearer"
                and secrets.compare_digest(credential, self.token)
            )
            if not authorized:
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "invalid or missing bearer token"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class KnowledgeServiceApplication:
    """Serve public service metadata before delegating to the MCP app."""

    def __init__(self, mcp_app: ASGIApp) -> None:
        self.mcp_app = mcp_app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope.get("type") == "http" and scope.get("method") == "GET":
            path = str(scope.get("path", ""))
            if path == "/":
                response = JSONResponse(
                    {
                        "service": SERVICE_NAME,
                        "health": "/health",
                        "mcp": "/mcp",
                    }
                )
                await response(scope, receive, send)
                return
            if path == "/health":
                status_code, content = report_knowledge_service_health(
                    SETTINGS,
                    STORE,
                )
                response = JSONResponse(
                    status_code=status_code,
                    content=content,
                )
                await response(scope, receive, send)
                return
        await self.mcp_app(scope, receive, send)


SETTINGS = KnowledgeSettings.from_env()
STORE = KnowledgeApplicationDataStore(SETTINGS.data_dir)
STORE.initialize()
MCP_SERVER = create_knowledge_mcp_server(SETTINGS, STORE)

MCP_APP = MCP_SERVER.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    host=SETTINGS.host,
)
SERVICE_APP = KnowledgeServiceApplication(MCP_APP)
app = MCPBearerTokenMiddleware(SERVICE_APP, SETTINGS.mcp_bearer_token)


def main() -> None:
    """Run the service using the configured bind address."""

    import uvicorn

    uvicorn.run(
        "services.knowledge_service.entrypoint:app",
        host=SETTINGS.host,
        port=SETTINGS.port,
        log_level=SETTINGS.log_level.lower(),
    )


if __name__ == "__main__":
    main()
