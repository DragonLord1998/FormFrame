from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AssetCheck(BaseModel):
    hashes: list[str] = Field(max_length=512)


class JobSubmission(BaseModel):
    job_id: str = Field(pattern=r"^job_[a-f0-9]{12}$")
    remote_bundle: str


class RemoteJob(BaseModel):
    job_id: str
    status: Literal["queued", "validating", "rendering", "completed", "failed", "cancelled"]
    progress: int = Field(default=0, ge=0, le=100)
    stage: str
    remote_bundle: str
    prompt_id: Optional[str] = None
    cancel_requested: bool = False
    result_path: Optional[str] = None
    preview_path: Optional[str] = None
    result_manifest_path: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
