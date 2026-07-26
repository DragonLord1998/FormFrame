from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def coefficients(length: int, seed: List[float] | None = None) -> List[float]:
    values = list(seed or [])
    return (values + [0.0] * length)[:length]


class Appearance(BaseModel):
    apparent_age: int = Field(default=32, ge=18, le=90)
    skin_tone: str = "#B9795B"
    skin_description: str = "warm medium skin with natural texture"
    hair_style: str = "Sculpted crop"
    hair_proxy: str = "hair_proxy_sculpted_crop"
    hair_description: str = "short dark sculpted hair"
    outfit: str = "Studio black"
    garment_proxy: str = "garment_proxy_studio_black"
    outfit_prompt: str = "minimal black fitted studio outfit"


class ReferenceImage(BaseModel):
    reference_id: str = Field(default_factory=lambda: new_id("reference"))
    role: Literal["face_front", "face_left", "face_right", "outfit"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str
    width: int = Field(ge=1, le=4096)
    height: int = Field(ge=1, le=4096)


class IdentityLora(BaseModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str = Field(min_length=1, max_length=255)
    bytes: int = Field(ge=1, le=2 * 1024 * 1024 * 1024)
    trigger_token: str = Field(default="ff_character", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    strength: float = Field(default=1.0, ge=-10, le=10)


class CharacterState(BaseModel):
    character_id: str = Field(default_factory=lambda: new_id("character"))
    preset: str = "Mara / Studio"
    name: str = "Mara"
    identity: List[float] = Field(
        default_factory=lambda: coefficients(253, [0.04, -0.11, 0.08]),
        max_length=253,
    )
    body_shape: List[float] = Field(
        default_factory=lambda: coefficients(10, [0.15, -0.07, 0.03]),
        max_length=10,
    )
    height: float = Field(default=1.0, ge=0.85, le=1.15)
    build: float = Field(default=0.48, ge=0, le=1)
    shoulder_width: float = Field(default=0.52, ge=0, le=1)
    leg_length: float = Field(default=0.55, ge=0, le=1)
    appearance: Appearance = Field(default_factory=Appearance)
    references: List[ReferenceImage] = Field(default_factory=list, max_length=4)
    identity_lora: Optional[IdentityLora] = None


class PoseState(BaseModel):
    preset: str = "Contrapposto"
    torso_twist: float = Field(default=-8, ge=-45, le=45)
    head_turn: float = Field(default=12, ge=-60, le=60)
    head_tilt: float = Field(default=-3, ge=-35, le=35)
    left_arm: float = Field(default=-18, ge=-90, le=90)
    right_arm: float = Field(default=24, ge=-90, le=90)
    left_elbow: float = Field(default=22, ge=0, le=130)
    right_elbow: float = Field(default=48, ge=0, le=130)
    hip_shift: float = Field(default=-0.12, ge=-0.4, le=0.4)
    left_knee: float = Field(default=4, ge=0, le=100)
    right_knee: float = Field(default=18, ge=0, le=100)
    expression: str = "Quiet confidence"
    expression_strength: float = Field(default=0.38, ge=0, le=1)
    gaze_x: float = Field(default=0.08, ge=-1, le=1)
    gaze_y: float = Field(default=0.02, ge=-1, le=1)
    gnm_expression: List[float] = Field(
        default_factory=lambda: coefficients(383),
        max_length=383,
    )
    gnm_joint_rotations: List[float] = Field(
        default_factory=lambda: coefficients(12),
        max_length=12,
    )
    smplx_global_orient: List[float] = Field(
        default_factory=lambda: coefficients(3),
        max_length=3,
    )
    smplx_body_pose: List[float] = Field(
        default_factory=lambda: coefficients(63),
        max_length=63,
    )
    smplx_left_hand_pose: List[float] = Field(
        default_factory=lambda: coefficients(45),
        max_length=45,
    )
    smplx_right_hand_pose: List[float] = Field(
        default_factory=lambda: coefficients(45),
        max_length=45,
    )


class SceneState(BaseModel):
    camera_yaw: float = Field(default=-8, ge=-180, le=180)
    camera_pitch: float = Field(default=3, ge=-45, le=45)
    camera_distance: float = Field(default=5.8, ge=3.5, le=9)
    focal_length: int = Field(default=70, ge=24, le=135)
    frame: Literal["portrait", "square", "landscape"] = "portrait"
    key_light: float = Field(default=0.78, ge=0, le=1)
    fill_light: float = Field(default=0.28, ge=0, le=1)
    background: str = "Warm seamless"
    floor_visible: bool = True


class RenderSettings(BaseModel):
    prompt: str = (
        "Editorial character portrait, soft directional studio light, "
        "natural skin texture, restrained cinematic color"
    )
    negative_prompt: str = "distorted anatomy, duplicate limbs, plastic skin, oversharpened"
    seed: int = 184627
    width: int = Field(default=768, ge=512, le=2048)
    height: int = Field(default=1024, ge=512, le=2048)
    denoise: float = Field(default=0.55, ge=0, le=1)
    depth_strength: float = Field(default=0.72, ge=0, le=1)
    pose_strength: float = Field(default=0.34, ge=0, le=1)
    quality: Literal["Draft", "Studio", "Final"] = "Studio"


class Project(BaseModel):
    schema_version: int = 1
    project_id: str = Field(default_factory=lambda: new_id("project"))
    name: str = "Untitled character"
    character: CharacterState = Field(default_factory=CharacterState)
    pose: PoseState = Field(default_factory=PoseState)
    scene: SceneState = Field(default_factory=SceneState)
    render: RenderSettings = Field(default_factory=RenderSettings)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


RuntimeStatus = Literal[
    "offline",
    "provisioning",
    "installing",
    "restoring",
    "starting",
    "loading",
    "warming",
    "ready",
    "rendering",
    "disconnected",
    "recovering",
]


class RuntimeSnapshot(BaseModel):
    status: RuntimeStatus = "offline"
    label: str = "Offline"
    progress: int = 0
    gpu: Optional[str] = None
    workflow: str = "controlled-character-v1"
    models_loaded: bool = False
    queue_size: int = 0
    runtime_id: Optional[str] = None
    provider: Literal["local-preview", "colab"] = "local-preview"
    detail: str = "Local editing is available. Start the backend to render a preview."
    capabilities: List[str] = Field(default_factory=list)
    readiness_errors: List[str] = Field(default_factory=list)


JobStatus = Literal["queued", "freezing", "exporting", "packaging", "rendering", "completed", "cancelled", "failed"]


class RenderRequest(BaseModel):
    project: Project
    provider: Literal["local-preview", "colab"] = "local-preview"


class RenderJob(BaseModel):
    schema_version: int = 1
    job_id: str = Field(default_factory=lambda: new_id("job"))
    project_id: str
    status: JobStatus = "queued"
    progress: int = 0
    stage: str = "Queued"
    provider: Literal["local-preview", "colab"] = "local-preview"
    workflow: str = "controlled-character-v1"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    bundle_path: Optional[str] = None
    preview_url: Optional[str] = None
    result_url: Optional[str] = None
    manifest: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class AssetCheck(BaseModel):
    hashes: List[str]


class AssetCheckResult(BaseModel):
    missing: List[str]
