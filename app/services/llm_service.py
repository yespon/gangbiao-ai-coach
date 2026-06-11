import json
import os
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException

from app.models.chat import ChatMessage, ChatSession


def _build_model_messages(session: ChatSession, user_msg: ChatMessage) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是岗位标准化 AI 教练。"
                "请在回答中保持教练式引导，优先围绕用户提供的上下文和材料。"
            ),
        }
    ]

    for msg in session.messages:
        if msg.role not in {"system", "user", "assistant"}:
            continue
        # Current user message is appended again below with attachment content.
        if msg is user_msg:
            continue
        messages.append({"role": msg.role, "content": msg.content})

    # Build user message with attachment content
    user_content = user_msg.content
    if user_msg.attachments:
        attachment_info = "\n\n【用户上传的附件信息】\n"
        for att in user_msg.attachments:
            attachment_info += f"文件名: {att.get('filename', '未知')}\n"
            attachment_info += f"大小: {att.get('size', 0)} bytes\n"
            if att.get('excerpt'):
                attachment_info += f"内容:\n{att.get('excerpt')}\n"
            attachment_info += "\n"
        user_content = user_content + attachment_info if user_content.strip() else attachment_info
    
    messages.append({"role": "user", "content": user_content})
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
