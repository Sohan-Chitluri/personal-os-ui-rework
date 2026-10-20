"""HTTP request and response shapes for authenticated sync v1 routes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from personal_os.sync.contracts import Acknowledgement, ChangeFeedItem


class SyncUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: int
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class SyncUploadResponse(BaseModel):
    protocol_version: int
    acknowledgements: list[Acknowledgement]


class SyncChangePageResponse(BaseModel):
    protocol_version: int
    items: list[ChangeFeedItem]
    next_cursor: str
    cursor_expires_at: datetime
    has_more: bool
