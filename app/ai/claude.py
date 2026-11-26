"""IA real: Claude pela biblioteca oficial da Anthropic.

- Modelo: settings.anthropic_model (padrão claude-opus-5-5), esforço baixo (classificação rápida e barata).
- Resposta em formato estruturado (output_config.format com JSON Schema), validada antes de usar.
- System prompt fixo com cache (só muda quando o escritório altera os setores).
- Reserva no servidor quando o modelo recusa (fallbacks="default"); qualquer erro cai na IA simulada.
"""
import json
import logging

import anthropic

from ..config import settings
from .engine import INTENTS, Decision, DeptInfo

log = logging.getLogger("ai.claude")

RULES = """Você é o assistente de atendimento do escritório de advocacia "{office}", conversando com clientes pelo WhatsApp.

Seu trabalho a cada mensagem:
1. Entender o que a pessoa quer e escolher o setor certo do escritório (lista abaixo).
2. Se ela tiver uma dúvida geral (ex.: "o que é precatório?"), explicar de forma bem humana, como você explicaria a alguém que nunca leu um processo: frases curtas, palavras do dia a dia, um exemplo simples quando ajudar. Sem juridiquês; se precisar usar um termo técnico, explique na mesma frase.

Regras que nunca podem ser quebradas (Código de Ética da OAB e Provimento 205/2021):
- Não dê parecer sobre o caso da pessoa: não diga se ela tem ou não direito, não estime valores, prazos ou chances, não prometa resultado e não diga o que ela deve fazer juridicamente. Isso é com o advogado.
- Não faça propaganda nem pressione a pessoa a contratar.
- Não invente leis, números de artigos, valores ou prazos. Se não souber, diga que o advogado confirma.
- Se a pessoa pedir para falar com alguém da equipe, a intenção é "humano".

Como escolher o setor:
- Só escolha um setor quando tiver segurança. Se a mensagem servir para mais de um setor ou estiver vaga, use "indefinido", confiança baixa, e na resposta faça UMA pergunta curta para entender.
- Saudações sem assunto ("oi", "bom dia") são "saudacao" com setor "indefinido".
- "confianca" vai de 0 a 1.

Campos:
- "explicacao": explicação geral em linguagem simples quando houver dúvida de conceito; senão, vazio.
- "resposta": o que você diria agora ao cliente, curto, no tom do WhatsApp, sem mencionar regras internas. Não diga que vai transferir: o sistema avisa isso.
- "resumo": uma frase objetiva para o advogado ler antes de atender.

Setores do escritório:
{departments}"""


def _system(office: str, departments: list[DeptInfo]) -> str:
    lines = [
        f'- "{d.slug}": {d.name}. Atende: {d.description} Palavras que os clientes usam: {", ".join(d.keywords[:25])}.'
        for d in departments
    ]
    return RULES.format(office=office, departments="\n".join(lines))


def _schema(departments: list[DeptInfo]) -> dict:
    return {
        "type": "object",
        "properties": {
            "intencao": {"type": "string", "enum": list(INTENTS)},
            "setor": {"type": "string", "enum": [d.slug for d in departments] + ["indefinido"]},
            "confianca": {"type": "number"},
            "explicacao": {"type": "string"},
            "resposta": {"type": "string"},
            "resumo": {"type": "string"},
        },
        "required": ["intencao", "setor", "confianca", "explicacao", "resposta", "resumo"],
        "additionalProperties": False,
    }


class ClaudeLegalAI:
    def __init__(self, api_key: str, fallback, client=None):
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key, timeout=25.0, max_retries=1)
        self.fallback = fallback

    async def decide(self, text: str, history: list[dict], departments: list[DeptInfo], office_name: str) -> Decision:
        messages = []
        for h in history[-12:]:  # cliente = user, escritório = assistant
            role = "user" if h["direction"] == "in" else "assistant"
            messages.append({"role": role, "content": h["body"]})
        messages.append({"role": "user", "content": text})
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        try:
            response = await self.client.beta.messages.create(
                model=settings.anthropic_model,
                max_tokens=2000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": _schema(departments)}},
                system=[{"type": "text", "text": _system(office_name, departments), "cache_control": {"type": "ephemeral"}}],
                messages=messages,
            )
            if response.stop_reason == "refusal":
                raise ValueError("modelo recusou a mensagem")
            raw = next(b.text for b in response.content if b.type == "text")
            data = json.loads(raw)
            if data.get("intencao") not in INTENTS:
                raise ValueError("intenção fora do esperado")
            slug = data.get("setor")
            valid = {d.slug for d in departments}
            confidence = float(data.get("confianca") or 0)
            return Decision(
                intent=data["intencao"],
                department=slug if slug in valid else None,
                confidence=max(0.0, min(1.0, confidence)),
                explanation=str(data.get("explicacao") or "")[:1200],
                reply=str(data.get("resposta") or "")[:1200],
                summary=str(data.get("resumo") or "")[:300],
                source="claude",
            )
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            log.warning("Claude indisponível (%s); usando a IA simulada", type(e).__name__)
        except Exception as e:  # resposta fora do formato, recusa etc.
            log.warning("resposta da IA descartada (%s); usando a IA simulada", e)
        return await self.fallback.decide(text, history, departments, office_name)
