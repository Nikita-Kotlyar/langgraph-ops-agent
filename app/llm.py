from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from .config import Settings


def build_llm(settings: Settings) -> BaseChatModel:
    """Создать chat-модель по настройкам OPS_LLM_*.

    Поддерживается RouterAI, Anthropic и любой OpenAI-совместимый endpoint:
    провайдер и адрес задаются через OPS_LLM_PROVIDER и OPS_LLM_BASE_URL.
    """
    if not settings.resolved_api_key:
        raise RuntimeError(
            "Не задан ключ LLM. Укажи OPS_LLM_API_KEY или ANTHROPIC_API_KEY/OPENAI_API_KEY."
        )

    if settings.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "model": settings.resolved_model,
            "api_key": settings.resolved_api_key,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
        }
        if settings.resolved_base_url:
            kwargs["base_url"] = settings.resolved_base_url
        return ChatAnthropic(**kwargs)

    from langchain_openai import ChatOpenAI

    kwargs = {
        "model": settings.resolved_model,
        "api_key": settings.resolved_api_key,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    }
    if settings.resolved_base_url:
        kwargs["base_url"] = settings.resolved_base_url
    return ChatOpenAI(**kwargs)


def build_structured(llm: BaseChatModel, schema: type) -> Any:
    """Runnable, который возвращает Pydantic-объект schema.

    function_calling надёжнее для RouterAI/Anthropic-совместимых прокси,
    чем response_format: модель вызывает tool со схемой, а не пишет JSON текстом.
    """
    try:
        return llm.with_structured_output(schema, include_raw=False, method="function_calling")
    except TypeError:  # pragma: no cover - older LangChain
        return llm.with_structured_output(schema, include_raw=False)


_langfuse_client: Any | None = None


def get_langfuse_client(settings: Settings) -> Any | None:
    """Инициализировать глобальный клиент LangFuse один раз на процесс."""
    global _langfuse_client
    if not settings.langfuse_enabled:
        return None
    if _langfuse_client is None:
        try:
            from langfuse import Langfuse
        except ImportError:  # pragma: no cover - optional dependency
            return None
        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key_resolved,
            secret_key=settings.langfuse_secret_key_resolved,
            host=settings.langfuse_host_resolved,
            flush_at=1,
            flush_interval=1,
        )
    return _langfuse_client


def build_langfuse_handler(settings: Settings, trace_id: str | None = None) -> Any | None:
    """CallbackHandler LangFuse. trace_id объединяет все шаги одного запуска."""
    client = get_langfuse_client(settings)
    if client is None:
        return None
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:  # pragma: no cover - optional dependency
        return None
    trace_context = None
    if trace_id:
        import hashlib

        trace_context = {"trace_id": hashlib.md5(trace_id.encode("utf-8")).hexdigest()}
    return CallbackHandler(
        public_key=settings.langfuse_public_key_resolved,
        trace_context=trace_context,
    )


def flush_langfuse() -> None:
    """Отправить накопленные OTel-спаны в LangFuse."""
    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
        except Exception:  # pragma: no cover - never break the agent on telemetry
            pass
