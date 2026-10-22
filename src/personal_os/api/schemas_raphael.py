"""Pydantic schemas for the Raphael conversational API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageResponse(BaseModel):
    """A single message within a conversation."""

    id: str
    conversation_id: str
    role: str
    content: str
    client_message_id: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationSummary(BaseModel):
    """Metadata summary of a conversation without messages."""

    id: str
    title: str
    hermes_session_id: str | None = None
    status: str
    last_activity_at: datetime | None = None
    summary_status: str | None = None
    summarized_at: datetime | None = None
    summary_capture_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationDetail(BaseModel):
    """Detailed conversation object including its message history."""

    id: str
    title: str
    hermes_session_id: str | None = None
    status: str
    last_activity_at: datetime | None = None
    summary_status: str | None = None
    summarized_at: datetime | None = None
    summary_capture_id: str | None = None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class CreateConversationRequest(BaseModel):
    """Request payload to initiate a new conversation."""

    title: str | None = Field(default=None, max_length=255)


class SendMessageRequest(BaseModel):
    """Request payload to submit a user message to a conversation."""

    content: str = Field(..., min_length=1, max_length=100000)
    client_message_id: str | None = Field(default=None, max_length=128)


class SendMessageResponse(BaseModel):
    """Response containing both the stored user message and assistant reply."""

    user_message: MessageResponse
    assistant_message: MessageResponse
