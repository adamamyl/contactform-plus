from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

_SERVICES: dict[str, str] = {
    "form": os.environ.get("FORM_URL", "http://form:8000"),
    "team": os.environ.get("PANEL_URL", "http://panel:8001"),
    "router": os.environ.get("ROUTER_URL", "http://msg-router:8002"),
    "tts": os.environ.get("TTS_URL", "http://tts:8003"),
    "jambonz": os.environ.get("JAMBONZ_URL", "http://jambonz-adapter:8004"),
}

_PATHS: dict[str, list[str]] = {
    "form": ["form"],
    "team": ["team"],
    "dispatch": ["router", "jambonz"],
    "sysadmin": ["tts"],
}

_specs: dict[str, object] = {}


async def _fetch_spec(name: str, url: str) -> None:
    for delay in (0, 2, 5, 10, 20):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url}/openapi.json")
                if resp.status_code == 200:
                    _specs[name] = resp.json()
                    return
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await asyncio.gather(*(_fetch_spec(name, url) for name, url in _SERVICES.items()))
    yield


app = FastAPI(title="EMF API Docs", lifespan=lifespan)

_DIST = Path(__file__).parent / "swagger_ui_dist"
if _DIST.exists():
    app.mount("/swagger-ui-dist", StaticFiles(directory=str(_DIST)), name="swagger-ui")


def _swagger_page(title: str, spec_url: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <link rel="stylesheet" href="/swagger-ui-dist/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="/swagger-ui-dist/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({{
      url: "{spec_url}",
      dom_id: "#swagger-ui",
      presets: [SwaggerUIBundle.presets.apis],
      layout: "BaseLayout"
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(html)


def _swagger_multi_page(title: str, urls: list[dict[str, str]]) -> HTMLResponse:
    urls_js = "[" + ",".join(
        f'{{"url":"/api/specs/{u["service"]}","name":"{u["name"]}"}}'
        for u in urls
    ) + "]"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <link rel="stylesheet" href="/swagger-ui-dist/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="/swagger-ui-dist/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({{
      urls: {urls_js},
      dom_id: "#swagger-ui",
      presets: [SwaggerUIBundle.presets.apis],
      layout: "BaseLayout"
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    all_link = '<li><a href="/all"><code>/all</code></a> — all services</li>'
    path_links = "\n".join(
        f'<li><a href="/{path}"><code>/{path}</code></a>'
        f' — {", ".join(svc_keys)}</li>'
        for path, svc_keys in _PATHS.items()
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>EMF API Docs</title>
  <style>body{{font-family:sans-serif;max-width:600px;margin:2rem auto;}}
    code{{background:#f4f4f4;padding:2px 6px;border-radius:3px;}}</style>
</head>
<body>
  <h1>EMF API Documentation</h1>
  <ul>
    {all_link}
    {path_links}
  </ul>
  <p><small>Specs fetched from running services. Reload to refresh.</small></p>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/api/specs/{service}")
async def get_spec(service: str) -> JSONResponse:
    if service not in _specs:
        raise HTTPException(status_code=404, detail=f"Spec for '{service}' not available")
    return JSONResponse(_specs[service])


@app.get("/all", response_class=HTMLResponse)
async def swagger_all() -> HTMLResponse:
    urls = [{"service": name, "name": name} for name in _specs]
    return _swagger_multi_page("EMF — All APIs", urls)


@app.get("/{path}", response_class=HTMLResponse)
async def swagger_path(path: str) -> HTMLResponse:
    if path not in _PATHS:
        raise HTTPException(status_code=404, detail=f"Unknown path: /{path}")
    svc_keys = _PATHS[path]
    if len(svc_keys) == 1:
        return _swagger_page(f"EMF — {path}", f"/api/specs/{svc_keys[0]}")
    urls = [{"service": k, "name": k} for k in svc_keys]
    return _swagger_multi_page(f"EMF — {path}", urls)
