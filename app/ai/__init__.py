"""IA jurídica do atendimento. Usa Claude quando ANTHROPIC_API_KEY está definida; senão, a IA simulada."""
import logging

from ..config import settings
from .engine import Decision, DeptInfo, SimulatedLegalAI

log = logging.getLogger("ai")
_simulated = SimulatedLegalAI()


def get_ai():
    if settings.anthropic_api_key:
        from .claude import ClaudeLegalAI

        return ClaudeLegalAI(settings.anthropic_api_key, fallback=_simulated)
    return _simulated


__all__ = ["Decision", "DeptInfo", "get_ai", "SimulatedLegalAI"]
