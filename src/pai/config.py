from functools import lru_cache
from typing import Annotated, Literal, Self
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")
    enable_api_docs: bool = Field(default=True, alias="ENABLE_API_DOCS")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"], alias="CORS_ORIGINS"
    )
    trusted_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        alias="TRUSTED_HOSTS",
    )

    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    cookie_same_site: Literal["lax", "strict", "none"] = Field(
        default="lax", alias="COOKIE_SAME_SITE"
    )
    refresh_cookie_name: str = Field(default="pai_refresh_token", alias="REFRESH_COOKIE_NAME")
    csrf_cookie_name: str = Field(default="pai_csrf_token", alias="CSRF_COOKIE_NAME")

    supabase_url: str = Field(..., alias="SUPABASE_URL")
    supabase_anon_key: str = Field(..., alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: str = Field(..., alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_jwt_secret: str = Field(..., alias="SUPABASE_JWT_SECRET")
    supabase_jwt_audience: str = Field(default="authenticated", alias="SUPABASE_JWT_AUDIENCE")

    email_verification_redirect_url: str = Field(..., alias="EMAIL_VERIFICATION_REDIRECT_URL")
    password_reset_redirect_url: str = Field(..., alias="PASSWORD_RESET_REDIRECT_URL")
    frontend_onboarding_path: str = Field(default="/onboarding", alias="FRONTEND_ONBOARDING_PATH")
    frontend_home_path: str = Field(default="/", alias="FRONTEND_HOME_PATH")

    auth_http_timeout_seconds: float = Field(default=10.0, alias="AUTH_HTTP_TIMEOUT_SECONDS")

    database_url: str = Field(..., alias="DATABASE_URL")
    database_ssl_verify: bool = Field(default=True, alias="DATABASE_SSL_VERIFY")
    vault_encryption_key: str = Field(..., alias="VAULT_ENCRYPTION_KEY")

    llm_default_provider: str = Field(default="openai", alias="LLM_DEFAULT_PROVIDER")
    llm_counseling_model: str = Field(default="gpt-5.6-terra", alias="LLM_COUNSELING_MODEL")
    llm_extraction_model: str = Field(default="gpt-5.6-luna", alias="LLM_EXTRACTION_MODEL")
    llm_document_model: str = Field(default="gpt-5.6-luna", alias="LLM_DOCUMENT_MODEL")
    llm_document_vision_model: str = Field(default="gpt-4o-mini", alias="LLM_DOCUMENT_VISION_MODEL")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    llm_timeout_seconds: float = Field(default=60.0, alias="LLM_TIMEOUT_SECONDS")
    llm_counseling_max_tokens: int = Field(default=2048, alias="LLM_COUNSELING_MAX_TOKENS")

    llm_simple_counseling_model: str = Field(default="gpt-5.6-luna", alias="LLM_SIMPLE_COUNSELING_MODEL")
    llm_goal_model: str = Field(default="gpt-5.6-terra", alias="LLM_GOAL_MODEL")
    llm_complex_model: str = Field(default="gpt-5.6-sol", alias="LLM_COMPLEX_MODEL")
    llm_counseling_reasoning: str = Field(default="low", alias="LLM_COUNSELING_REASONING")
    llm_extraction_reasoning: str = Field(default="none", alias="LLM_EXTRACTION_REASONING")
    llm_goal_reasoning: str = Field(default="medium", alias="LLM_GOAL_REASONING")
    llm_counseling_timeout_seconds: float = Field(default=30, alias="LLM_COUNSELING_TIMEOUT_SECONDS")
    enable_integrated_goal_analysis: bool = Field(default=False, alias="ENABLE_INTEGRATED_GOAL_ANALYSIS")
    enable_goal_escalation: bool = Field(default=True, alias="ENABLE_GOAL_ESCALATION")
    # Embedded workers are a development opt-in. Production uses dedicated processes.
    run_workers_in_api: bool = Field(default=False, alias="RUN_WORKERS_IN_API")

    supabase_storage_bucket: str = Field(default="documents", alias="SUPABASE_STORAGE_BUCKET")
    document_max_bytes: int = Field(default=10_485_760, alias="DOCUMENT_MAX_BYTES")
    document_processing_timeout_seconds: float = Field(
        default=180.0, alias="DOCUMENT_PROCESSING_TIMEOUT_SECONDS"
    )
    document_allow_image_uploads: bool = Field(default=True, alias="DOCUMENT_ALLOW_IMAGE_UPLOADS")
    document_ocr_provider: str = Field(default="openai_vision", alias="DOCUMENT_OCR_PROVIDER")
    document_vision_max_pages: int = Field(default=20, alias="DOCUMENT_VISION_MAX_PAGES")
    document_vision_batch_pages: int = Field(default=2, alias="DOCUMENT_VISION_BATCH_PAGES")
    document_vision_max_tokens: int = Field(default=8000, alias="DOCUMENT_VISION_MAX_TOKENS")
    document_malware_scan_provider: str = Field(default="none", alias="DOCUMENT_MALWARE_SCAN_PROVIDER")
    chat_recent_message_limit: int = Field(default=8, alias="CHAT_RECENT_MESSAGE_LIMIT")
    enable_document_worker: bool = Field(default=True, alias="ENABLE_DOCUMENT_WORKER")
    # One loop per API process. person_id advisory locks keep students serialized
    # if you run `uvicorn --workers N`.
    enable_intelligence_worker: bool = Field(default=True, alias="ENABLE_INTELLIGENCE_WORKER")
    enable_goal_worker: bool = Field(default=True, alias="ENABLE_GOAL_WORKER")

    # Web search (Tavily) — leave empty until you add the key; tool degrades gracefully
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    tavily_max_results: int = Field(default=5, alias="TAVILY_MAX_RESULTS")
    tavily_search_depth: str = Field(default="basic", alias="TAVILY_SEARCH_DEPTH")

    # AgentSpan semantic / conversation memory
    semantic_memory_max_results: int = Field(default=5, alias="SEMANTIC_MEMORY_MAX_RESULTS")
    conversation_memory_max_messages: int = Field(
        default=40, alias="CONVERSATION_MEMORY_MAX_MESSAGES"
    )
    enable_counselor_tools: bool = Field(default=True, alias="ENABLE_COUNSELOR_TOOLS")
    counselor_max_tool_rounds: int = Field(default=1, alias="COUNSELOR_MAX_TOOL_ROUNDS")
    # Cap rows scanned when ranking lexically (the vector path ranks in SQL)
    semantic_memory_scan_limit: int = Field(default=200, alias="SEMANTIC_MEMORY_SCAN_LIMIT")
    # Semantic recall via embeddings. Off (or no API key) -> lexical ranking, unchanged.
    enable_semantic_embeddings: bool = Field(default=True, alias="ENABLE_SEMANTIC_EMBEDDINGS")
    embedding_provider: str = Field(default="openai", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    # Must match the model's output size and the vector(N) column in migration 014.
    embedding_dimensions: int = Field(default=1536, alias="EMBEDDING_DIMENSIONS")
    # Rows pulled by vector search before structural re-ranking in Python.
    embedding_candidate_limit: int = Field(default=40, alias="EMBEDDING_CANDIDATE_LIMIT")
    embedding_timeout_seconds: float = Field(default=0.6, gt=0, alias="EMBEDDING_TIMEOUT_SECONDS")
    memory_recall_budget_seconds: float = Field(default=0.9, gt=0, le=2, alias="MEMORY_RECALL_BUDGET_SECONDS")
    turn_understanding_budget_seconds: float = Field(default=1.2, gt=0, le=2, alias="TURN_UNDERSTANDING_BUDGET_SECONDS")
    memory_rerank_url: str = Field(default="", alias="MEMORY_RERANK_URL")
    memory_rerank_api_key: str = Field(default="", alias="MEMORY_RERANK_API_KEY")
    memory_rerank_model: str = Field(default="", alias="MEMORY_RERANK_MODEL")
    memory_rerank_budget_seconds: float = Field(default=0.2, gt=0, le=1, alias="MEMORY_RERANK_BUDGET_SECONDS")
    # Postgres LangGraph checkpoints add remote writes per node — off by default for chat latency
    enable_graph_checkpoint: bool = Field(default=False, alias="ENABLE_GRAPH_CHECKPOINT")

    enable_rate_limits: bool = Field(default=True, alias="ENABLE_RATE_LIMITS")
    rate_limit_fail_closed: bool = Field(default=False, alias="RATE_LIMIT_FAIL_CLOSED")
    rate_limit_backend_timeout_seconds: float = Field(
        default=1.0, gt=0, le=5, alias="RATE_LIMIT_BACKEND_TIMEOUT_SECONDS"
    )
    request_limit_per_minute: int = Field(default=120, gt=0, alias="REQUEST_LIMIT_PER_MINUTE")
    user_request_limit_per_minute: int = Field(default=60, gt=0, alias="USER_REQUEST_LIMIT_PER_MINUTE")
    upload_limit_per_day: int = Field(default=30, gt=0, alias="UPLOAD_LIMIT_PER_DAY")
    llm_call_limit_per_day: int = Field(default=300, gt=0, alias="LLM_CALL_LIMIT_PER_DAY")
    llm_token_limit_per_day: int = Field(default=1000000, gt=0, alias="LLM_TOKEN_LIMIT_PER_DAY")
    llm_global_token_limit_per_day: int = Field(default=10000000, gt=0, alias="LLM_GLOBAL_TOKEN_LIMIT_PER_DAY")
    readiness_timeout_seconds: float = Field(default=3, gt=0, alias="READINESS_TIMEOUT_SECONDS")
    worker_heartbeat_max_age_seconds: int = Field(default=600, gt=0, alias="WORKER_HEARTBEAT_MAX_AGE_SECONDS")
    worker_queue_max_age_seconds: int = Field(default=900, gt=0, alias="WORKER_QUEUE_MAX_AGE_SECONDS")

    @field_validator(
        "supabase_anon_key",
        "supabase_service_role_key",
        "supabase_jwt_secret",
        "supabase_url",
        "database_url",
        "vault_encryption_key",
        "deepseek_api_key",
        "openai_api_key",
        "tavily_api_key",
        mode="before",
    )
    @classmethod
    def strip_secrets(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def split_csv(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "email_verification_redirect_url",
        "password_reset_redirect_url",
        mode="before",
    )
    @classmethod
    def frontend_redirect_url(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        url = value.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Redirect URL must be an absolute http(s) frontend address.")
        if not parsed.path or parsed.path == "/":
            raise ValueError(
                "Redirect URL must include a dedicated path "
                "(e.g. /auth/verify-email or /auth/reset-password), not the site root."
            )
        return url

    @model_validator(mode="after")
    def redirects_match_cors(self) -> Self:
        allowed = set(self.cors_origins)
        if "*" in allowed:
            return self
        for url in (self.email_verification_redirect_url, self.password_reset_redirect_url):
            parsed = urlparse(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in allowed:
                raise ValueError(
                    f"Redirect origin {origin} must also be listed in CORS_ORIGINS."
                )
        return self

    def next_path(self, *, onboarding_completed: bool) -> str:
        return self.frontend_home_path if onboarding_completed else self.frontend_onboarding_path

    @property
    def supabase_auth_base(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
