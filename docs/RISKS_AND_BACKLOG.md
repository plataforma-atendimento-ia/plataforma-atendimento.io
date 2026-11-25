# Riscos e Backlog — Fase 0

## Riscos
| Risco | Prob. | Impacto | Mitigação |
|---|---|---|---|
| Banimento/bloqueio de número WhatsApp | Média | Alto | Só API oficial; opt-in; qualidade de mensagem; limites de template; monitorar rating |
| Custo de LLM acima do previsto | Média | Alto | Medir por atendimento; cache; modelo menor para triagem; teto por tenant |
| Alucinação (preço, diagnóstico, jurídico) | Média | Muito alto | RAG estrito, guardrails por vertical, handoff se sem contexto, suíte de avaliação no CI |
| LGPD (dados de saúde = sensíveis) | Média | Muito alto | Base legal, DPA, minimização, redação em logs, exportar/apagar, criptografia; revisão jurídica antes do beta |
| Vazamento entre tenants | Baixa | Crítico | RLS + testes A×B obrigatórios + role sem BYPASSRLS |
| Mudança de regras/preços da Meta | Alta | Médio | Preço de template parametrizado; abstração `ChannelProvider` |
| Aprovação do WABA/verificação de empresa demora | Alta | Médio | Iniciar verificação Meta cedo; onboarding guiado |
| Sem clientes piloto | Alta | Alto | Recrutar 3–5 betas gratuitos em paralelo às Fases 1–3 |
| Escopo do MVP grande p/ 2 pessoas | Alta | Alto | Cortar primeiro: distribuição por habilidade, depois kanban avançado |
| Dependência de uma pessoa (bus factor) | Média | Médio | Docs, testes, CI |

## Backlog priorizado
**P0 (Fase 1 — fundação):** monorepo; Docker Compose; auth + papéis; tenants + RLS + testes A×B; CI; `.env.example`; logs/Sentry.
**P0 (Fase 2 — mensageria):** Cloud API (onboarding de número, webhook); `inbound_events`; outbox + worker + retries + reconciliador; status de entrega; inbox em tempo real; assumir atômico; notas e tags; teste de carga k6 (50 atendentes).
**P0 (Fase 2b):** setores, filas, distribuição (round-robin, menos ocupado), limite simultâneo, horário, transferência, fila de espera; contatos; funil kanban.
**P0 (Fase 3 — IA):** `LLMProvider`; ingestão de documentos + RAG; agente com guardrails; autopilot/copilot; handoff com resumo; extração para CRM com auditoria; leitura de imagem/PDF; humanização; suíte de avaliação.
**P0 (Fase 4 — negócio):** planos; `usage_events`; Stripe; teto de gasto; repasse de custo Meta em linha separada; painel (atendimentos, tempo de resposta, resolução pela IA, falhas); onboarding adaptativo (site/documentos/questionário → configuração proposta); LGPD (termos, DPA, exportar/apagar).
**P1:** distribuição por habilidade; USD; status page pública; API pública + webhooks documentados.
**P2 (fase 2+):** Instagram, webchat, e-mail; templates imobiliária/advocacia; campanhas; relatórios avançados.

## Critérios de pronto da Fase 0
- [ ] Você aprovou stack, vertical e plano de preços.
- [ ] Premissas de custo (Meta, LLM) verificadas com valores atuais.
- [ ] Decidido onde hospedar (Railway/Render/Fly + Neon/Supabase + Upstash).
