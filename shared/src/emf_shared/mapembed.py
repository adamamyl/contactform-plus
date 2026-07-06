from __future__ import annotations


def component_script_src(map_url: str) -> str:
    return f"{map_url.rstrip('/')}/component.js"
