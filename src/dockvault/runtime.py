from contextvars import ContextVar, Token

_server_url_override: ContextVar[str | None] = ContextVar(
    "dockvault_server_url_override",
    default=None,
)


def set_server_url_override(value: str | None) -> Token[str | None]:
    return _server_url_override.set(value)


def reset_server_url_override(token: Token[str | None]) -> None:
    _server_url_override.reset(token)


def get_server_url_override() -> str | None:
    return _server_url_override.get()
