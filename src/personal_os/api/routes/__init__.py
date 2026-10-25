"""API route modules for Personal OS."""

from personal_os.api.routes.captures import router as captures_router
from personal_os.api.routes.companion_ws import router as companion_ws_router
from personal_os.api.routes.devices import router as devices_router
from personal_os.api.routes.github import router as github_router
from personal_os.api.routes.health import router as health_router
from personal_os.api.routes.processing import router as processing_router
from personal_os.api.routes.projects import router as projects_router
from personal_os.api.routes.raphael import router as raphael_router
from personal_os.api.routes.sync import router as sync_router
from personal_os.api.routes.tasks import router as tasks_router
from personal_os.api.routes.voice import router as voice_router

__all__ = [
    "health_router",
    "projects_router",
    "tasks_router",
    "captures_router",
    "processing_router",
    "devices_router",
    "companion_ws_router",
    "sync_router",
    "raphael_router",
    "github_router",
    "voice_router",
]
