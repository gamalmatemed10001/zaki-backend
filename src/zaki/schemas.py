"""Request/response models for /api/assistant."""

from pydantic import BaseModel, Field


class AssistantRequest(BaseModel):
    text: str = Field(min_length=1, description="User's message (transcribed or typed)")
    session_id: str = Field(
        default="default",
        description="Groups conversation history; a device/client picks one",
    )


class AssistantResponse(BaseModel):
    reply: str
    route: str  # "gemini" | "claude" — which model handled this turn
    model: str  # exact model ID used, for debugging/observability


class TTSRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = Field(
        default=None, description="Overrides ZAKI_TTS_VOICE for this call, e.g. ar-EG-SalmaNeural"
    )
