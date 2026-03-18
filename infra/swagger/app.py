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

# Individual-service paths shown in the index
_PATHS: dict[str, list[str]] = {
    "form": ["form"],
    "team": ["team"],
    "dispatch": ["router", "jambonz"],
    "tts": ["tts"],
}

_specs: dict[str, dict[str, object]] = {}

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})


def _server_url(service: str) -> str | None:
    return _PUBLIC_URLS.get(service) or _LOCAL_SERVER_URLS.get(service)


def _inject_spec_extras(spec: dict[str, object], service: str) -> dict[str, object]:
    """Inject server URL and security schemes before serving a single-service spec."""
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


def _rewrite_refs(obj: object, prefix: str) -> object:
    """Recursively rewrite #/components/schemas/X → #/components/schemas/Prefix_X."""
    if isinstance(obj, dict):
        return {
            k: (
                "#/components/schemas/" + prefix + "_" + v[len("#/components/schemas/") :]
                if k == "$ref" and isinstance(v, str) and v.startswith("#/components/schemas/")
                else _rewrite_refs(v, prefix)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_rewrite_refs(item, prefix) for item in obj]
    return obj


def _tag_operations(path_item: object, tag: str) -> object:
    """Prepend tag to every HTTP operation in a path item."""
    if not isinstance(path_item, dict):
        return path_item
    result: dict[str, object] = {}
    for method, operation in path_item.items():
        if method in _HTTP_METHODS and isinstance(operation, dict):
            op = dict(operation)
            tags_list: list[object] = list(op.get("tags") or [])
            if tag not in tags_list:
                tags_list.insert(0, tag)
            op["tags"] = tags_list
            result[method] = op
        else:
            result[method] = operation
    return result


def _path_has_tag(path_item: object, tag: str) -> bool:
    if not isinstance(path_item, dict):
        return False
    return any(
        isinstance(op, dict) and tag in (op.get("tags") or [])
        for method, op in path_item.items()
        if method in _HTTP_METHODS
    )


def _merge_specs(
    service_keys: list[str],
    title: str,
    op_tag: str | None = None,
) -> dict[str, object]:
    """Merge multiple OpenAPI specs into one.

    Each service's paths are prefixed with /{svc_key} to avoid collisions.
    An operation-level servers override is added so "try it out" hits the
    correct hostname. Schemas are namespaced with {Prefix}_ and $refs rewritten.

    If op_tag is given, only paths where at least one operation carries that tag
    are included; /metrics is always included as a fallback (added by
    prometheus-fastapi-instrumentator without tags).
    """
    merged_paths: dict[str, object] = {}
    merged_schemas: dict[str, object] = {}
    merged_security_schemes: dict[str, object] = {}
    tags: list[dict[str, str]] = []

    for svc_key in service_keys:
        if svc_key not in _specs:
            continue

        spec = _inject_spec_extras(_specs[svc_key], svc_key)
        display = _TITLES.get(svc_key, svc_key)
        prefix = svc_key.title().replace("-", "")

        # Collect schemas, prefixed to avoid cross-service collisions
        components_raw = spec.get("components")
        if isinstance(components_raw, dict):
            schemas_raw = components_raw.get("schemas")
            if isinstance(schemas_raw, dict):
                for name, defn in schemas_raw.items():
                    merged_schemas[f"{prefix}_{name}"] = _rewrite_refs(defn, prefix)
            sec_raw = components_raw.get("securitySchemes")
            if isinstance(sec_raw, dict):
                merged_security_schemes.update(
                    {k: v for k, v in sec_raw.items() if isinstance(k, str)}
                )

        tags.append({"name": display})

        paths_raw = spec.get("paths")
        if not isinstance(paths_raw, dict):
            continue

        svc_url = _server_url(svc_key)

        for path, path_item in paths_raw.items():
            if op_tag and not _path_has_tag(path_item, op_tag) and path != "/metrics":
                continue

            merged_path = f"/{svc_key}{path}"
            path_item = _rewrite_refs(path_item, prefix)
            path_item = _tag_operations(path_item, display)

            # Override server per path so "try it out" hits the right host
            if svc_url and isinstance(path_item, dict):
                path_item = dict(path_item)
                path_item["servers"] = [{"url": svc_url}]

            merged_paths[merged_path] = path_item

    result: dict[str, object] = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": "1.0"},
        "tags": tags,
        "paths": merged_paths,
    }

    components: dict[str, object] = {}
    if merged_schemas:
        components["schemas"] = merged_schemas
    if merged_security_schemes:
        components["securitySchemes"] = merged_security_schemes
    if components:
        result["components"] = components

    return result


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
    qp = qp ? JSON.parse(
        '{'+arr.join()+'}',
        function(k,v){return k?decodeURIComponent(v):v;}
    ) : {};
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
    special_links = """
    <li><a href="/all"><code>/all</code></a> — all APIs merged</li>
    <li><a href="/sysadmin"><code>/sysadmin</code></a> — health &amp; metrics</li>"""
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
    {special_links}
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


@app.get("/api/specs-merged/all")
async def get_merged_spec() -> JSONResponse:
    return JSONResponse(_merge_specs(list(_specs.keys()), "EMF Conduct — All APIs"))


@app.get("/api/specs-merged/sysadmin")
async def get_sysadmin_spec() -> JSONResponse:
    return JSONResponse(_merge_specs(list(_specs.keys()), "EMF Conduct — Sysadmin", op_tag="ops"))


@app.get("/all", response_class=HTMLResponse)
async def swagger_all() -> HTMLResponse:
    if not _specs:
        return HTMLResponse("<p>No specs available yet — try reloading.</p>")
    return _swagger_page("EMF — All APIs", "/api/specs-merged/all")


@app.get("/sysadmin", response_class=HTMLResponse)
async def swagger_sysadmin() -> HTMLResponse:
    if not _specs:
        return HTMLResponse("<p>No specs available yet — try reloading.</p>")
    return _swagger_page("EMF — Sysadmin", "/api/specs-merged/sysadmin")


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
