"""Shared state for the sidecar process."""

import asyncio
import uuid
from dataclasses import dataclass, field


@dataclass
class Settings:
    blender_port: int = 0
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    auth_mode: str = "subscription"


@dataclass
class ChatMessage:
    id: str = ""
    role: str = ""
    content: str = ""
    code: str = ""
    post_message: str = ""
    has_code: bool = False
    code_executed: bool = False
    code_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "code": self.code,
            "post_message": self.post_message,
            "has_code": self.has_code,
            "code_executed": self.code_executed,
            "code_bytes": self.code_bytes,
        }


@dataclass
class ConversationState:
    messages: list[ChatMessage] = field(default_factory=list)
    is_streaming: bool = False
    active_code_index: int = -1
    developer_events: list[dict] = field(default_factory=list)


# Module-level singletons
settings = Settings()
conversation = ConversationState()
sse_queues: dict[str, asyncio.Queue] = {}


def init(blender_port: int, api_key: str = "", model: str = "claude-sonnet-4-20250514") -> None:
    """Configure sidecar settings."""
    settings.blender_port = blender_port
    settings.api_key = api_key
    settings.auth_mode = "api_key" if api_key else "subscription"
    if model:
        settings.model = model


def new_message_id() -> str:
    return str(uuid.uuid4())[:8]
