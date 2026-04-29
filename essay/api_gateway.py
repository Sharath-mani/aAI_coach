from typing import Dict

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

SERVICE_MAP: Dict[str, str] = {
    "essay": "http://localhost:8000",
    "quiz": "http://localhost:8001",
    "prq": "http://localhost:8002",
}

app = FastAPI(title="Agenix Unified API Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def serve_frontend():
    return FileResponse("index.html")


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "healthy", "service": "api-gateway"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_SERVER_ERROR", "message": str(exc)}},
    )


@app.api_route("/api/{service}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(service: str, path: str, request: Request):
    service_name = (service or "").strip().lower()
    upstream_base = SERVICE_MAP.get(service_name)
    if not upstream_base:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "UNKNOWN_SERVICE", "message": f"Unknown service '{service_name}'"}},
        )

    upstream_url = f"{upstream_base}/{path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }

    async with httpx.AsyncClient(timeout=120) as client:
        upstream_response = await client.request(
            method=request.method,
            url=upstream_url,
            content=body,
            headers=headers,
        )

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in {"content-length", "transfer-encoding", "connection"}
    }

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
