"""handlers package — aiogram routers for the Malda College bot."""

from .start import router as start_router
from .profile import router as profile_router
from .notify import router as notify_router
from .routine_cmd import router as routine_router
from .status import router as status_router
from .admin import router as admin_router
from .suggest import router as suggest_router
from .misc import router as misc_router
from .donate import router as donate_router

__all__ = [
    "start_router",
    "profile_router",
    "notify_router",
    "routine_router",
    "status_router",
    "admin_router",
    "suggest_router",
    "misc_router",
    "donate_router",
]
