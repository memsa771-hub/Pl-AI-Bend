"""ASGI entrypoint for FastAPI Cloud and `fastapi dev` / `uvicorn pai.main:app`."""

from pai.app import create_app_from_env

app = create_app_from_env()
