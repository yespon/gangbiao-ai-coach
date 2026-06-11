import json
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException

from app.models.chat import ChatMessage, ChatSession

# ---------------------------------------------------------------------------
# Context-budget helpers
# ---------------------------------------------------------------------------
# Maximum total chars (history + context) sent to the LLM per request.
# 0 means no limit. Default 40 000 (≈ 10 000 tokens) keeps the attachment
# content prominent while allowing reasonable coaching history.
_LLM_MAX_HISTORY_CHARS = max(int(os.getenv("LLM_MAX_HISTORY_CHARS", "40000") or "40000"), 0)

# Always keep this many recent *real* conversation turns (user+assistant pairs).
_LLM_MIN_RECENT_TURNS = max(int(os.getenv("LLM_MIN_RECENT_TURNS", "6") or "6"), 1)


def _build_user_content(user_msg: ChatMessage) -> str:
    user_content = user_msg.content
    if user_msg.attachments:
        attachment_info = "\n\n【用户上传的附件信息】\n"
        for att in user_msg.attachments:
            attachment_info += f"文件名: {att.get('filename', '未知')}\n"
            attachment_info += f"大小: {att.get('size', 0)} bytes\n"
            if att.get("excerpt"):
                attachment_info += f"内容:\n{att.get('excerpt')}\n"
            attachment_info += "\n"
        user_content = user_content + attachment_info if user_content.strip() else attachment_info
    return user_content


def _build_model_messages(session: ChatSession, user_msg: ChatMessage) -> list[dict[str, str]]:
    system_content = (
        "你是岗位标准化 AI 教练。"
        "请在回答中保持教练式引导，优先围绕用户提供的上下文和材料。"
    )

    # Separate pre-loaded context turns from real conversation turns.
    context_turns: list[dict[str, str]] = []
    history_turns: list[dict[str, str]] = []

    for msg in session.messages:
        if msg.role not in {"system", "user", "assistant"}:
            continue
        if msg is user_msg:
            continue
        entry = {"role": msg.role, "content": msg.content}
        if msg.is_context:
            context_turns.append(entry)
        else:
            history_turns.append(entry)

    current_user_content = _build_user_content(user_msg)

    # ------------------------------------------------------------------
    # Budget trimming: drop oldest context turns first.
    # Real conversation turns and the current user message are never cut.
    # ------------------------------------------------------------------
    max_chars = _LLM_MAX_HISTORY_CHARS
    if max_chars > 0:
        # Chars already committed (non-negotiable).
        fixed_chars = len(system_content) + len(current_user_content)
        for m in history_turns:
            fixed_chars += len(m["content"])

        budget = max_chars - fixed_chars
        # Walk context turns from most-recent to oldest, include while budget allows.
        included_context: list[dict[str, str]] = []
        for turn in reversed(context_turns):
            turn_chars = len(turn["content"])
            if budget >= turn_chars:
                included_context.insert(0, turn)
                budget -= turn_chars
            # Once budget is exhausted keep skipping older turns.
        dropped = len(context_turns) - len(included_context)
    else:
        included_context = context_turns
        dropped = 0

    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    messages.extend(included_context)
    messages.extend(history_turns)
    messages.append({"role": "user", "content": current_user_content})

    # Attach trim metadata so callers (e.g. debug logger) can see what happened.
    messages[0]["_dropped_context_turns"] = str(dropped)  # type: ignore[index]

    return messages


async def _call_llm(messages: list[dict[str, str]]) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    if not api_key:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return (
            "当前未配置 OPENAI_API_KEY，已使用本地回退回复。\n"
            "如需真实模型回复，请设置环境变量后重试。\n\n"
            f"你刚才的问题是：{last_user[:400]}"
        )

    url = f"{base_url}/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0.2}
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"LLM 调用失败: {response.status_code} {response.text}",
            )
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def _call_llm_stream(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    if not api_key:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        fallback = (
            "当前未配置 OPENAI_API_KEY，已使用本地回退回复。\n"
            "如需真实模型回复，请设置环境变量后重试。\n\n"
            f"你刚才的问题是：{last_user[:400]}"
        )
        for i in range(0, len(fallback), 30):
            yield fallback[i:i + 30]
        return

    url = f"{base_url}/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0.2, "stream": True}
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise HTTPException(
                    status_code=502,
                    detail=f"LLM 调用失败: {response.status_code} {body.decode('utf-8', errors='ignore')}",
                )

            async for raw_line in response.aiter_lines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue

                data_line = line[5:].strip()
                if data_line == "[DONE]":
                    break

                try:
                    data = json.loads(data_line)
                except json.JSONDecodeError:
                    continue

                choice = (data.get("choices") or [{}])[0]
                delta = (choice.get("delta") or {}).get("content")

                if isinstance(delta, str) and delta:
                    yield delta
                    continue

                content = ((choice.get("message") or {}).get("content"))
                if isinstance(content, str) and content:
                    yield content
