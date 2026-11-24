from __future__ import annotations

from fastapi import APIRouter

from ..models import ClaudeUsage
from ..services import claude_usage

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/claude-code", response_model=ClaudeUsage)
def claude_code_usage(refresh: bool = False):
    """Claude Code's plan limits (5-hour session, weekly) for the signed-in subscription."""
    return claude_usage.get_usage(refresh=refresh)
