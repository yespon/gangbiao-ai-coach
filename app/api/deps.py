import uuid

from fastapi import Header

from app.core.logger import get_component_logger

LOGGER = get_component_logger(component="chatbot")


def get_current_user_id(x_user_id: str | None = Header(default=None)) -> str:
    """Extract caller identity from X-User-ID request header.

    When the header is present and non-empty it is used as-is, making sessions
    stable across requests for the same browser/client.

    When the header is absent (e.g. direct API call without a client), a fresh
    random UUID is generated for that request.  This ensures that two callers
    who both forget the header are still isolated from each other — neither can
    list or access the other's sessions.

    Migration path to real auth: replace this function body with a JWT/cookie
    validator that returns the authenticated user's canonical ID.
    """
    uid = (x_user_id or "").strip()
    return uid if uid else uuid.uuid4().hex
