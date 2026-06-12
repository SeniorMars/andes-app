from __future__ import annotations

import uvicorn

from andes_core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "andes_api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )
