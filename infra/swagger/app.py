from __future__ import annotations

import asyncio
import json
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

_OIDC_ISSUER: str = os.environ.get("OIDC_ISSUER", "http://oidc.emf-forms.internal/default")

_TITLES: dict[str, str] = {
    "form": "Report Form",
    "team": "Panel",
    "router": "Message Router (internal)",
    "tts": "Text-to-Speech",
    "jambonz": "Jambonz Adapter (internal)",
}

# Fallback server URLs for local dev when config.json has no domains section
_LOCAL_SERVER_URLS: dict[str, str] = {
    "form": "https://report.emf-forms.internal",
    "team": "https://panel.emf-forms.internal",
}


def _load_public_urls() -> dict[str, str]:
    """Derive public-facing server URLs from config.json domains section."""
    config_path = Path(os.environ.get("CONFIG_PATH", "config.json"))
    try:
        cfg = json.loads(config_path.read_text())
        domains_raw = cfg.get("domains") or {}
        domains: dict[str, str | None] = domains_raw if isinstance(domains_raw, dict) else {}
    except Exception:
        return {}
    mapping = {
        "form": domains.get("report"),
        "team": domains.get("panel"),
    }
    return {svc: f"https://{host}" for svc, host in mapping.items() if host}


_PUBLIC_URLS: dict[str, str] = _load_public_urls()

_PATHS: dict[str, list[str]] = {
    "form": ["form"],
    "team": ["team"],
    "dispatch": ["router", "jambonz"],
    "tts": ["tts"],
}

_specs: dict[str, dict[str, object]] = {}


def _server_url(service: str) -> str | None:
    return _PUBLIC_URLS.get(service) or _LOCAL_SERVER_URLS.get(service)


def _inject_spec_extras(spec: dict[str, object], service: str) -> dict[str, object]:
    """Inject server URL and security schemes before serving a spec."""
    spec = dict(spec)

    url = _server_url(service)
    if url:
        spec["servers"] = [{"url": url, "description": "Local"}]

    components_raw = spec.get("components")
    components: dict[str, object] = dict(components_raw) if isinstance(components_raw, dict) else {}
    schemes_raw = components.get("securitySchemes")
    schemes: dict[str, object] = dict(schemes_raw) if isinstance(schemes_raw, dict) else {}

    if service == "team":
        schemes["oidc"] = {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": f"{_OIDC_ISSUER}/authorize",
                    "tokenUrl": f"{_OIDC_ISSUER}/token",
                    "scopes": {
                        "openid": "OpenID Connect",
                        "profile": "User profile",
                        "email": "Email address",
                        "groups": "Group membership",
                    },
                }
            },
        }
    elif service in ("router", "jambonz"):
        schemes["internalSecret"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-Internal-Secret",
        }

    if schemes:
        components["securitySchemes"] = schemes
        spec["components"] = components

    return spec


async def _fetch_spec(name: str, url: str) -> None:
    for delay in (0, 2, 5, 10, 20):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url}/openapi.json")
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        _specs[name] = data
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

_OAUTH2_REDIRECT = """<!doctype html>
<html><head><title>Swagger UI: OAuth2 Redirect</title></head><body>
<script>
'use strict';
function run() {
    var oauth2 = window.opener.swaggerUIRedirectOauth2;
    var sentState = oauth2.state;
    var redirectUrl = oauth2.redirectUrl;
    var qp, arr;
    if (/code|token|error/.test(window.location.hash)) {
        qp = window.location.hash.substring(1).replace('?','&');
    } else {
        qp = location.search.substring(1);
    }
    arr = qp.split('&');
    arr.forEach(function(v,i,a){a[i]='"'+v.replace('=','":"')+'"';});
    qp = qp ? JSON.parse('{'+arr.join()+'}',function(k,v){return k?decodeURIComponent(v):v;}) : {};
    var isValid = sentState === qp.state;
    var flow = oauth2.auth.schema.get('flow');
    var codeFlows = ['accessCode','authorizationCode','authorization_code'];
    if (codeFlows.indexOf(flow) >= 0 && !oauth2.auth.code) {
        if (!isValid) {
            oauth2.errCb({authId:oauth2.auth.name,source:'auth',
                level:'warning',message:'State mismatch'});
        }
        if (qp.code) {
            delete oauth2.state; oauth2.auth.code = qp.code;
            oauth2.callback({auth:oauth2.auth,redirectUrl:redirectUrl});
        } else {
            oauth2.errCb({authId:oauth2.auth.name,source:'auth',
                level:'error',message:'No authorization code'});
        }
    } else if (oauth2.auth.token) { oauth2.callback({auth:oauth2.auth}); }
}
document.addEventListener('DOMContentLoaded', run);
</script></body></html>"""

_SWAGGER_OPTS = (
    "persistAuthorization: true, oauth2RedirectUrl: window.location.origin + '/oauth2-redirect'"
)


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
      layout: "BaseLayout",
      {_SWAGGER_OPTS}
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(html)


def _swagger_sections_page(title: str, sections: list[dict[str, str]]) -> HTMLResponse:
    """Render one swagger-ui instance per section with a heading."""
    divs = "\n  ".join(
        f'<h2 style="font-family:sans-serif;margin:2rem 0 0.5rem">'
        f'{s["title"]}</h2>\n  <div id="swagger-ui-{s["service"]}"></div>'
        for s in sections
    )
    inits = "\n    ".join(
        f"""SwaggerUIBundle({{
      url: "{s["url"]}",
      dom_id: "#swagger-ui-{s["service"]}",
      presets: [SwaggerUIBundle.presets.apis],
      layout: "BaseLayout",
      {_SWAGGER_OPTS}
    }});"""
        for s in sections
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <link rel="stylesheet" href="/swagger-ui-dist/swagger-ui.css">
</head>
<body>
  {divs}
  <script src="/swagger-ui-dist/swagger-ui-bundle.js"></script>
  <script>
    {inits}
  </script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/oauth2-redirect", response_class=HTMLResponse)
async def oauth2_redirect() -> HTMLResponse:
    return HTMLResponse(_OAUTH2_REDIRECT)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    all_link = '<li><a href="/all"><code>/all</code></a> — all services</li>'
    path_links = "\n".join(
        f'<li><a href="/{path}"><code>/{path}</code></a>'
        f" — {', '.join(_TITLES.get(k, k) for k in svc_keys)}</li>"
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
    return JSONResponse(_inject_spec_extras(_specs[service], service))


@app.get("/all", response_class=HTMLResponse)
async def swagger_all() -> HTMLResponse:
    if not _specs:
        return HTMLResponse("<p>No specs available yet — try reloading.</p>")
    sections = [
        {"service": name, "title": _TITLES.get(name, name), "url": f"/api/specs/{name}"}
        for name in _specs
    ]
    return _swagger_sections_page("EMF — All APIs", sections)


@app.get("/{path}", response_class=HTMLResponse)
async def swagger_path(path: str) -> HTMLResponse:
    if path not in _PATHS:
        raise HTTPException(status_code=404, detail=f"Unknown path: /{path}")
    svc_keys = [k for k in _PATHS[path] if k in _specs]
    if not svc_keys:
        return HTMLResponse(f"<p>No specs available for /{path} — try reloading.</p>")
    if len(svc_keys) == 1:
        return _swagger_page(
            f"EMF — {_TITLES.get(svc_keys[0], svc_keys[0])}",
            f"/api/specs/{svc_keys[0]}",
        )
    sections = [
        {"service": k, "title": _TITLES.get(k, k), "url": f"/api/specs/{k}"} for k in svc_keys
    ]
    return _swagger_sections_page(f"EMF — {path}", sections)
