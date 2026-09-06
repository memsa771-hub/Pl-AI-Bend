import pytest

from pai.app import create_app


@pytest.mark.parametrize("enabled", [True, False])
def test_swagger_visibility_is_independent_of_production(test_settings, enabled):
    settings = test_settings.model_copy(update={
        "app_env": "production", "enable_api_docs": enabled,
    })
    app = create_app(settings)
    assert (app.docs_url == "/docs") is enabled
    assert (app.openapi_url == "/openapi.json") is enabled
    assert app.swagger_ui_parameters["defaultModelsExpandDepth"] == 1
