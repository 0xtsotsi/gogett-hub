import secrets

from fastapi import Header, HTTPException, status

from agentbox.config import settings


async def require_api_key(
    x_api_key: str | None = Header(default=None),
) -> None:
    # Sandbox-manager auth is exclusively via X-API-Key. The Authorization
    # header is reserved for the function/lemma bearer token, so it must not be
    # accepted as the manager key here.
    #
    # Strip surrounding whitespace so a stray newline in a Secret value (the
    # classic `echo` vs `echo -n` base64 trap) can't cause a silent 401.
    provided = (x_api_key or "").strip()
    expected = (settings.agentbox_api_key or "").strip()

    # Constant-time compare to avoid leaking the key via timing.
    if not provided or not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid AgentBox API key",
        )
