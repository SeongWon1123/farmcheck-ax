# Copyright (c) 2026 FarmCheck contributors.
"""Server-selected catalogs and workspace-scoped human review HTTP routes."""

import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from farmcheck.agent_routes import configured_status, register_agent_routes
from farmcheck.import_routes import MAX_IMPORT_BODY, register_import_routes
from farmcheck.model_connection import model_identity
from farmcheck.model_routes import register_model_routes
from farmcheck.read_routes import register_read_routes
from farmcheck.rules import RULE_REGISTRY
from farmcheck.service import Service, View, json_object
from farmcheck.snapshot_routes import register_snapshot_routes
from farmcheck.source import Catalog, load_catalog
from farmcheck.store import DomainError, Store
from farmcheck.workflow_routes import register_workflow_routes


def _register_private_responses(application: FastAPI, route_prefix: str) -> None:
    @application.middleware("http")
    async def private_responses(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Bound import bytes and prevent caching of cookie-scoped API responses."""
        # Bound streaming JSON before FastAPI allocates a decoded base64 string.
        if request.url.path == route_prefix + "/api/imports":
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_IMPORT_BODY:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "code": "import_too_large",
                            "message": "가져오기 요청이 너무 큽니다.",
                            "details": {},
                        },
                        headers={"Cache-Control": "private, no-store"},
                    )
            # Starlette requires its cached body for replay after bounded stream consumption.
            request._body = bytes(body)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        response = await call_next(request)
        if request.url.path.startswith(route_prefix + "/api/"):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Cookie"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def create_app(
    catalog: Catalog | None = None,
    database: Path | None = None,
    mode: str | None = None,
    base_path: str | None = None,
) -> FastAPI:
    """Create isolated apps for production and deterministic integration tests."""
    selected_mode = mode or os.environ.get("FARM_DATA_MODE", "synthetic")
    base = (base_path if base_path is not None else os.environ.get("APP_BASE_PATH", "")).rstrip("/")
    source_path = os.environ.get("FARM_SOURCE_ZIP")
    selected_catalog = catalog or load_catalog(
        selected_mode,
        None if source_path is None else Path(source_path),
    )
    store = Store(database or Path(os.environ.get("FARM_DB_PATH", f"var/{selected_mode}.sqlite")))
    service = Service(selected_catalog, store, selected_mode)
    application = FastAPI(title="FarmCheck", docs_url=None, redoc_url=None, openapi_url=None)
    route_prefix = os.environ.get("APP_ROUTE_PREFIX", base).rstrip("/")
    router = APIRouter(prefix=route_prefix)
    static = Path(__file__).parent / "static"

    _register_private_responses(application, route_prefix)

    @application.exception_handler(DomainError)
    async def domain_error(_request: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={"code": error.code, "message": error.message, "details": {}},
        )

    @application.exception_handler(RequestValidationError)
    async def input_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_input",
                "message": "요청 필드와 필수 값을 확인하세요.",
                "details": {},
            },
        )

    def private_guard(request: Request) -> None:
        if selected_mode == "green_tea" and (
            request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}
        ):
            code = "private_data"
            raise DomainError(code, "실제 자료는 로컬 검증에서만 열 수 있습니다.", 403)

    def workspace(request: Request) -> str:
        private_guard(request)
        identifier = request.cookies.get("farmcheck_workspace")
        if identifier is None:
            code = "workspace_required"
            raise DomainError(code, "먼저 작업 공간을 열어 주세요.", 401)
        return store.resolve_session(identifier)

    @router.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        """Serve only owned static assets; inject the server-chosen prefix."""
        return HTMLResponse(
            static.joinpath("index.html")
            .read_text(encoding="utf-8")
            .replace(
                "__BASE_PATH__",
                base,
            )
        )

    @router.get("/health")
    def health() -> View:
        return View(
            {
                "status": "ready",
                "mode": selected_mode,
                "records": len(selected_catalog.records),
            }
        )

    @router.get("/api/bootstrap")
    def bootstrap(request: Request, response: Response) -> View:
        private_guard(request)
        identifier, credential = store.bootstrap_session(request.cookies.get("farmcheck_workspace"))
        response.set_cookie(
            "farmcheck_workspace",
            credential,
            httponly=True,
            samesite="lax",
            secure=os.environ.get("FARM_COOKIE_SECURE") == "1" or request.url.scheme == "https",
            path=base or "/",
        )
        snapshot = store.read(identifier)
        return View(
            {
                "workspace_revision": snapshot.workspace_revision,
                "capabilities": {"import_pair": True},
                "model_connection": {
                    "enabled": True, "model": dict(model_identity().items()),
                },
                "mode": selected_mode,
                "summary": service.summary(snapshot).root,
                "rule_registry": [json_object(rule) for rule in RULE_REGISTRY],
                "agent_status": json_object(configured_status()),
                "source_problems": [json_object(problem) for problem in selected_catalog.problems],
            }
        )

    register_import_routes(router, service, workspace)
    register_read_routes(router, service, workspace)
    register_workflow_routes(router, service, workspace)
    register_model_routes(router, service, workspace)
    register_snapshot_routes(router, service, workspace)
    register_agent_routes(router, service, workspace)
    application.include_router(router)
    application.mount(route_prefix + "/static", StaticFiles(directory=static), name="static")
    return application


app = create_app()
