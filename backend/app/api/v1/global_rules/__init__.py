from app.api.v1.global_rules.applications import router as applications_router
from app.api.v1.global_rules.router import router
from app.api.v1.global_rules.targets import router as targets_router

__all__ = ["applications_router", "router", "targets_router"]
