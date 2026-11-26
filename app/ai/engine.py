"""Decisão da IA a cada mensagem do cliente + a IA simulada (por regras, sem custo, usada nos testes)."""
import re
from dataclasses import dataclass, field

from .knowledge import GLOSSARY, norm

INTENTS = ("saudacao", "duvida", "caso", "agendar", "humano", "outro")


@dataclass
class DeptInfo:
    slug: str
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    needs_consult: bool = False
    is_default: bool = False


@dataclass
class Decision:
    intent: str  # saudacao | duvida | caso | agendar | humano | outro
    department: str | None  # slug do setor, ou None se ainda não dá para saber
    confidence: float  # 0 a 1
    explanation: str = ""  # explicação GERAL em linguagem simples (nunca parecer sobre o caso)
    reply: str = ""  # texto sugerido pela IA real (a simulada deixa vazio)
    summary: str = ""  # uma frase para o advogado
    source: str = "simulada"


HUMAN_RE = re.compile(r"\b(atendente|humano|falar com alguem|falar com uma pessoa|pessoa de verdade)\b")
GREET_RE = re.compile(r"^(oi+|ola|opa|bom dia|boa tarde|boa noite|tudo bem|e ai|eai|alo)\b[\s!.,?]*(tudo bem)?[\s!.,?]*$")
QUESTION_RE = re.compile(
    r"\b(o que e|o que sao|o que significa|como funciona|quanto tempo|qual a diferenca|pra que serve|"
    r"para que serve|como faco|como faz|o que acontece|e verdade que)\b"
)
SCHEDULE_RE = re.compile(r"\b(marcar|agendar|consulta|horario|reuniao|atendimento presencial)\b")
PERSONAL_RE = re.compile(r"\b(meu|minha|meus|minhas|eu|fui|estou|tenho|quero|preciso|me deve|comigo)\b")


def _hits(low: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", low) is not None


def glossary_term(low: str) -> str | None:
    found = [k for k in GLOSSARY if _hits(low, k)]
    if not found:  # plural simples: "precatorios", "audiencias"
        found = [k for k in GLOSSARY if _hits(low, k + "s")]
    return max(found, key=len) if found else None


def score_departments(low: str, departments: list[DeptInfo]) -> list[tuple[float, DeptInfo]]:
    scored = []
    for d in departments:
        s = 0.0
        seen = set()
        for kw in d.keywords:
            strong = kw.strip().startswith("!")
            k = norm(kw.strip().lstrip("!"))
            if not k or k in seen:
                continue
            if _hits(low, k):
                seen.add(k)
                # expressões mais longas valem mais; palavras marcadas com "!" definem o assunto
                s += (3 if strong else 1) + k.count(" ")
        scored.append((s, d))
    return sorted(scored, key=lambda x: -x[0])


class SimulatedLegalAI:
    """IA por regras: identifica o assunto pelas palavras que os clientes usam e explica termos comuns.
    Não entende contexto como a IA real, mas é previsível, gratuita e serve de reserva se a real falhar."""

    async def decide(self, text: str, history: list[dict], departments: list[DeptInfo], office_name: str) -> Decision:
        low = norm(text)
        term = glossary_term(low)
        explanation = GLOSSARY[term] if term else ""
        ranked = score_departments(low, [d for d in departments if d.keywords])
        best_s, best = ranked[0] if ranked else (0, None)
        second_s = ranked[1][0] if len(ranked) > 1 else 0
        if best_s <= 0:
            dept, conf = None, 0.0
        elif best_s == second_s:
            dept, conf = None, 0.5  # empate: melhor perguntar do que errar o setor
        else:
            dept, conf = best.slug, round(best_s / (best_s + second_s), 2)

        is_question = "?" in text or QUESTION_RE.search(low) is not None
        personal = PERSONAL_RE.search(low) is not None
        if HUMAN_RE.search(low):
            intent = "humano"
        elif GREET_RE.match(low):
            intent = "saudacao"
        elif SCHEDULE_RE.search(low):
            intent = "agendar"
        elif is_question and term and not personal:
            intent = "duvida"
        elif dept:
            intent = "caso"
        elif is_question and term:
            intent = "duvida"
        else:
            intent = "outro"
        return Decision(
            intent=intent,
            department=dept,
            confidence=conf,
            explanation=explanation if (is_question or intent == "duvida") else "",
            summary=text.strip()[:200],
        )
