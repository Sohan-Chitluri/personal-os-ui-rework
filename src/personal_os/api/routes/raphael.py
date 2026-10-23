"""FastAPI router for Raphael conversational endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from personal_os.api.deps import get_current_user, get_raphael_service
from personal_os.api.schemas_raphael import (
    ConversationDetail,
    ConversationSummary,
    CreateConversationRequest,
    MessageResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from personal_os.db.models import User
from personal_os.services.hermes import (
    HermesConnectionError,
    HermesError,
    HermesTimeoutError,
    sanitize_hermes_error,
)
from personal_os.services.raphael import RaphaelService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/raphael", tags=["Raphael"])


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationSummary]:
    """List conversations belonging to the authenticated user."""
    conversations = await service.list_conversations(
        owner_id=current_user.id, limit=limit, offset=offset
    )
    return [ConversationSummary.model_validate(c) for c in conversations]


@router.get("/active", response_model=ConversationDetail)
async def get_active_conversation(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
) -> ConversationDetail:
    """Retrieve the single active conversation, auto-summarizing if inactive >= 60 minutes."""
    conversation = await service.get_or_create_active_conversation(owner_id=current_user.id)
    messages = await service.get_messages(owner_id=current_user.id, conversation_id=conversation.id)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        hermes_session_id=conversation.hermes_session_id,
        status=conversation.status,
        last_activity_at=conversation.last_activity_at,
        summary_status=conversation.summary_status,
        summarized_at=conversation.summarized_at,
        summary_capture_id=conversation.summary_capture_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[MessageResponse.model_validate(m) for m in messages],
    )


@router.post("/reset", response_model=ConversationDetail)
async def reset_active_conversation(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
) -> ConversationDetail:
    """Finalize current active conversation to Diary and start a fresh session."""
    conversation = await service.reset_active_conversation(owner_id=current_user.id)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        hermes_session_id=conversation.hermes_session_id,
        status=conversation.status,
        last_activity_at=conversation.last_activity_at,
        summary_status=conversation.summary_status,
        summarized_at=conversation.summarized_at,
        summary_capture_id=conversation.summary_capture_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[],
    )


@router.get("/memories")
async def get_session_memories(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
    q: Annotated[str, Query(description="Search query")] = "",
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> list[dict[str, Any]]:
    """Search Diary-persisted past session summaries."""
    return await service.search_session_memories(owner_id=current_user.id, query=q, limit=limit)


@router.delete("/memories/{capture_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session_memory(
    capture_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
) -> None:
    """Delete a Diary session summary owned by the authenticated user."""
    try:
        deleted = await service.delete_session_memory(
            owner_id=current_user.id, capture_id=capture_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session memory not found or already deleted.",
        )


@router.post("/conversations/{conversation_id}/finalize")
async def finalize_conversation(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
) -> dict[str, Any]:
    """Force summarization of a conversation into Diary."""
    await service.get_conversation(owner_id=current_user.id, conversation_id=conversation_id)
    cap = await service.finalize_conversation(conversation_id=conversation_id, force=True)
    return {
        "conversation_id": conversation_id,
        "summary_status": "summarized",
        "capture_id": cap.id if cap else None,
    }


@router.post(
    "/conversations",
    response_model=ConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: CreateConversationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
) -> ConversationDetail:
    """Create a new Raphael conversation."""
    conversation = await service.create_conversation(owner_id=current_user.id, title=payload.title)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        hermes_session_id=conversation.hermes_session_id,
        status=conversation.status,
        last_activity_at=conversation.last_activity_at,
        summary_status=conversation.summary_status,
        summarized_at=conversation.summarized_at,
        summary_capture_id=conversation.summary_capture_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[],
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_conversation(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
) -> ConversationDetail:
    """Get a conversation and its messages."""
    conversation = await service.get_conversation(
        owner_id=current_user.id, conversation_id=conversation_id
    )
    messages = await service.get_messages(owner_id=current_user.id, conversation_id=conversation_id)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        hermes_session_id=conversation.hermes_session_id,
        status=conversation.status,
        last_activity_at=conversation.last_activity_at,
        summary_status=conversation.summary_status,
        summarized_at=conversation.summarized_at,
        summary_capture_id=conversation.summary_capture_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[MessageResponse.model_validate(m) for m in messages],
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SendMessageResponse,
)
async def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RaphaelService, Depends(get_raphael_service)],
) -> SendMessageResponse:
    """Submit a user message to Hermes and return both user and assistant messages."""
    try:
        user_msg, assistant_msg = await service.send_message(
            owner_id=current_user.id,
            conversation_id=conversation_id,
            content=payload.content,
            client_message_id=payload.client_message_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except HermesTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Hermes agent timed out: {sanitize_hermes_error(str(exc))}",
        ) from exc
    except HermesConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Hermes agent is unavailable: {sanitize_hermes_error(str(exc))}",
        ) from exc
    except HermesError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Hermes agent failed: {sanitize_hermes_error(str(exc))}",
        ) from exc

    return SendMessageResponse(
        user_message=MessageResponse.model_validate(user_msg),
        assistant_message=MessageResponse.model_validate(assistant_msg),
    )
