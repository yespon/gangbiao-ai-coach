from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ChatMessage:
    role: str
    content: str
    created_at: str = field(default_factory=_now_iso)
    is_context: bool = False
    visible_in_history: bool = True
    attachments: list[dict[str, Any]] = field(default_factory=list)
    # display_content is shown to the frontend; content (with appended file hints)
    # is sent to the LLM. When None, content is used for both.
    display_content: str | None = None


@dataclass
class ChatSession:
    session_id: str
    show_context_in_history: bool
    context_file: str
    user_id: str = "anonymous"
    created_at: str = field(default_factory=_now_iso)
    messages: list[ChatMessage] = field(default_factory=list)
    current_template_id: str | None = None
