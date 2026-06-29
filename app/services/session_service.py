from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update as sa_update
from sqlalchemy.orm import selectinload

from app.models.db_models import ChatSessionDB, ChatMessageDB
from app.models.chat import ChatSession, ChatMessage

from datetime import UTC, datetime

# In-memory cache for LLM runtime context
SESSION_CACHE: dict[str, ChatSession] = {}

# Backward-compatible alias so existing imports still work during transition
SESSIONS = SESSION_CACHE


async def create_session_in_db(
    db: AsyncSession, user_id: str, show_context: bool, context_file: str
) -> ChatSessionDB:
    session = ChatSessionDB(
        user_id=user_id, show_context=show_context, context_file=context_file
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_user_sessions(db: AsyncSession, user_id: str) -> list[ChatSessionDB]:
    # selectinload the messages relationship so db_session_summary_for_client
    # can access session.messages synchronously without triggering a lazy
    # load. In an async session a sync lazy load raises MissingGreenlet
    # and the route returns 500.
    # Exclude soft-deleted sessions; sort pinned first, then by updated_at desc.
    result = await db.execute(
        select(ChatSessionDB)
        .options(selectinload(ChatSessionDB.messages))
        .where(ChatSessionDB.user_id == user_id, ChatSessionDB.deleted_at.is_(None))
        .order_by(ChatSessionDB.pinned.desc(), ChatSessionDB.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_session_by_id(
    db: AsyncSession, session_id: str, user_id: str
) -> ChatSessionDB | None:
    result = await db.execute(
        select(ChatSessionDB)
        .options(selectinload(ChatSessionDB.messages))
        .where(
            ChatSessionDB.id == session_id,
            ChatSessionDB.user_id == user_id,
            ChatSessionDB.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def update_session_settings(
    db: AsyncSession, session: ChatSessionDB, show_context: bool
) -> ChatSessionDB:
    session.show_context = show_context
    await db.commit()
    await db.refresh(session)
    return session


def db_session_history_for_client(session_db: ChatSessionDB) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for msg in session_db.messages:
        if not msg.visible_in_history:
            continue
        if msg.is_context and not session_db.show_context:
            continue
        history.append({
            "role": msg.role,
            "content": msg.display_content if msg.display_content else msg.content,
            "created_at": msg.created_at.isoformat() if msg.created_at else "",
            "is_context": msg.is_context,
            "attachments": msg.attachments if msg.attachments else [],
        })
    return history


def db_session_summary_for_client(session_db: ChatSessionDB) -> dict[str, str]:
    latest_user_msg = next(
        (m for m in reversed(session_db.messages)
         if m.role == "user" and not m.is_context and m.visible_in_history),
        None,
    )
    preview = "\u65b0\u5efa\u4f1a\u8bdd"
    if latest_user_msg:
        text = latest_user_msg.display_content or latest_user_msg.content
        preview = text.strip().replace("\n", " ")[:40] or "\u65b0\u5efa\u4f1a\u8bdd"
    # Use custom title if set; otherwise fall back to message preview
    display_title = session_db.title or preview
    return {
        "session_id": str(session_db.id),
        "title": display_title,
        "pinned": bool(session_db.pinned),
        "created_at": session_db.created_at.isoformat(),
        "updated_at": (latest_user_msg.created_at if latest_user_msg else session_db.created_at).isoformat(),
        "latest_preview": preview,
    }


def rebuild_memory_session(session_db: ChatSessionDB) -> ChatSession:
    """Rebuild an in-memory ChatSession from DB data for LLM context."""
    memory_session = ChatSession(
        session_id=str(session_db.id),
        show_context_in_history=session_db.show_context,
        context_file=session_db.context_file or "",
        user_id=str(session_db.user_id),
        created_at=session_db.created_at.isoformat() if session_db.created_at else "",
        current_template_id=getattr(session_db, "current_template_id", None) or None,
    )
    for msg_db in session_db.messages:
        memory_msg = ChatMessage(
            role=msg_db.role,
            content=msg_db.content,
            created_at=msg_db.created_at.isoformat() if msg_db.created_at else "",
            is_context=msg_db.is_context,
            visible_in_history=msg_db.visible_in_history,
            attachments=msg_db.attachments if msg_db.attachments else [],
            display_content=msg_db.display_content,
        )
        memory_session.messages.append(memory_msg)
    return memory_session


# --- In-memory helpers (used by tests and cache-only paths) ---


def _session_history_for_client(session: ChatSession) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for msg in session.messages:
        if not msg.visible_in_history:
            continue
        if msg.is_context and not session.show_context_in_history:
            continue
        history.append(
            {
                "role": msg.role,
                "content": msg.display_content if msg.display_content is not None else msg.content,
                "created_at": msg.created_at,
                "is_context": msg.is_context,
                "attachments": msg.attachments,
            }
        )
    return history


def _session_summary_for_client(session: ChatSession) -> dict[str, str]:
    latest_user_message = next(
        (
            msg
            for msg in reversed(session.messages)
            if msg.role == "user" and not msg.is_context and msg.visible_in_history
        ),
        None,
    )
    latest_preview = "\u65b0\u5efa\u4f1a\u8bdd"

    if latest_user_message:
        user_text = (
            latest_user_message.display_content
            if latest_user_message.display_content is not None
            else latest_user_message.content
        )
        preview = user_text.strip().replace("\n", " ")
        latest_preview = preview[:40] if preview else "\u65b0\u5efa\u4f1a\u8bdd"

    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "updated_at": latest_user_message.created_at if latest_user_message else session.created_at,
        "latest_preview": latest_preview,
    }


# --- Session management (rename / pin / delete) ---


async def rename_session(
    db: AsyncSession, session_id: str, user_id: str, title: str
) -> ChatSessionDB | None:
    session_db = await get_session_by_id(db, session_id, user_id)
    if session_db is None or session_db.deleted_at is not None:
        return None
    session_db.title = title.strip()[:200] or None
    await db.commit()
    await db.refresh(session_db)
    return session_db


async def toggle_pin_session(
    db: AsyncSession, session_id: str, user_id: str
) -> ChatSessionDB | None:
    session_db = await get_session_by_id(db, session_id, user_id)
    if session_db is None or session_db.deleted_at is not None:
        return None
    session_db.pinned = not session_db.pinned
    await db.commit()
    await db.refresh(session_db)
    return session_db


async def soft_delete_session(
    db: AsyncSession, session_id: str, user_id: str
) -> bool:
    session_db = await get_session_by_id(db, session_id, user_id)
    if session_db is None or session_db.deleted_at is not None:
        return False
    session_db.deleted_at = datetime.now(UTC)
    await db.commit()
    # Also remove from in-memory cache if present
    SESSION_CACHE.pop(session_id, None)
    return True


async def update_session_template(
    db: AsyncSession, session_id: str, template_id: str
) -> None:
    """Persist the current template_id (D1..D7) on a session. Called when
    classify_file successfully identifies an attachment's template."""
    await db.execute(
        sa_update(ChatSessionDB)
        .where(ChatSessionDB.id == session_id)
        .values(current_template_id=template_id)
    )
    await db.commit()
