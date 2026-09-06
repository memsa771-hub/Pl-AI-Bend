"""Local/CI startup without contacting auth or paid model providers."""
from fastapi.testclient import TestClient
from pai.app import create_app
from pai.config import get_settings
from pai.domains.goals.dependencies import input_snapshot
from pai.platform.llm.embeddings import OpenAIEmbeddingProvider

settings = get_settings().model_copy(update={"app_env": "test", "run_workers_in_api": False,
    "enable_graph_checkpoint": False, "openai_api_key": "", "deepseek_api_key": ""})
app = create_app(settings)
with TestClient(app) as client:
    assert client.get("/health/live").status_code == 200
    assert app.openapi()["paths"]
assert OpenAIEmbeddingProvider(settings).dimensions > 0
assert "education.highest_level" in input_snapshot({})
print("Imports, lifespan, routes and provider packaging OK")
