# Arquitetura — Plataforma de Atendimento com IA (Fase 0)

Status: **proposta para aprovação**. Nenhum código gerado.

## 1. Princípios
- Time de 2 pessoas: **monólito modular** + 1 worker. Zero microserviços.
- Serviços gerenciados sempre que possível.
- Tudo que varia por vertical/tenant é **configuração em banco**, não código.
- Nenhuma mensagem se perde: **outbox** + status explícito.
- Isolamento por tenant garantido no banco (**RLS**), não só na aplicação.

## 2. Stack (decisão resumida; detalhes em DECISIONS.md)
| Camada | Escolha |
|---|---|
| Linguagem | TypeScript em todo o projeto (um idioma, tipos compartilhados) |
| Backend | NestJS (módulos por domínio, DI, bom para time pequeno crescer) |
| Frontend | Next.js (App Router) + Tailwind + shadcn/ui |
| Banco | PostgreSQL + pgvector + RLS |
| Fila | BullMQ sobre Redis |
| Tempo real | WebSocket (Socket.IO) com adapter Redis |
| Storage | S3 compatível (Cloudflare R2 / Supabase Storage) |
| WhatsApp | Cloud API oficial da Meta (direto no MVP; BSP como plano B) |
| LLM | `LLMProvider` abstrato; Claude padrão |
| Billing | Stripe Billing (metered usage) |
| Observabilidade | pino (logs JSON), Sentry, métricas Prometheus-style, `/admin/health` |
| Infra MVP | Railway/Render/Fly (API + worker + web), Neon ou Supabase (PG), Upstash (Redis) |
| Dev | Monorepo pnpm + Turborepo, Docker Compose, GitHub Actions |

## 3. Diagrama

```
 Cliente final (WhatsApp)
        │  webhook (HTTPS)
        ▼
 ┌───────────────┐   enfileira    ┌──────────────┐
 │  API (NestJS) │───────────────▶│ Redis/BullMQ │
 │  REST + WS    │◀───────────────│              │
 └──────┬────────┘   eventos      └──────┬───────┘
        │                                │
        │ SQL (RLS por tenant)           ▼
        ▼                        ┌───────────────┐
 ┌───────────────┐               │    Worker     │
 │  PostgreSQL   │◀──────────────│ - outbox send │
 │  + pgvector   │               │ - agente IA   │
 └───────────────┘               │ - billing     │
        ▲                        └──────┬────────┘
        │ WebSocket                     │
 ┌──────┴────────┐               ┌──────▼────────┐
 │ Web (Next.js) │               │ Meta Cloud API│
 │ Inbox/Funil   │               │ LLM Provider  │
 └───────────────┘               └───────────────┘
```

## 4. Módulos (dentro do monólito)
`auth` · `tenants` · `users/roles` · `channels` (ChannelProvider: whatsapp) · `messaging` (outbox, status) · `inbox` (conversas, assumir, notas, tags) · `routing` (setores, filas, distribuição) · `contacts` · `pipeline` (funil kanban) · `ai` (RAG, agente, guardrails, copiloto, handoff) · `knowledge` · `billing` · `analytics` · `compliance` (LGPD, auditoria) · `admin`.

## 5. Mensageria confiável (dor nº 1)
**Saída (outbox):**
1. Atendente/IA cria `messages` com `status=queued` na mesma transação que atualiza a conversa.
2. Worker pega (`FOR UPDATE SKIP LOCKED`), marca `sending`, chama Meta com `idempotency_key = message.id`.
3. Sucesso → `sent`; webhooks da Meta → `delivered`/`read`; falha → retry exponencial (1s, 5s, 30s, 2min, 10min; máx. 6) → `failed` com `error_code`.
4. UI mostra status real via WebSocket e botão **Reenviar** em `failed`.
5. Reconciliador a cada minuto: mensagens `sending` há > 2 min voltam para `queued`.

**Entrada:** webhook grava o evento bruto em `inbound_events` (idempotente por `wamid`) e responde 200 imediatamente; processamento assíncrono. Nada é descartado.

## 6. Assumir conversa atômico (dor nº 2)
```sql
UPDATE conversations
SET assigned_to = $user, status='open', version = version + 1
WHERE id = $id AND tenant_id = $tenant AND assigned_to IS NULL
RETURNING *;
```
0 linhas → outro atendente ganhou → aviso imediato. UI otimista; evento `conversation.assigned` por WebSocket para todos. Meta: p95 < 300 ms.

## 7. Multi-tenant e RLS
- Toda tabela de negócio tem `tenant_id NOT NULL`.
- Conexão define `SET LOCAL app.tenant_id = '<uuid>'` por transação; policies: `USING (tenant_id = current_setting('app.tenant_id')::uuid)`.
- Role de aplicação **sem** `BYPASSRLS`. Role separada para migrações/admin.
- Teste automatizado obrigatório: tenant A tenta ler/escrever dados do B em cada tabela → deve falhar.
- Filas e cache com prefixo `tenant:<id>`.

## 8. IA
- **RAG por tenant:** documentos → chunks → embeddings (pgvector, filtro `tenant_id`) → top-k → prompt.
- **Guardrails:** (a) só responde com base em contexto recuperado; (b) sem contexto suficiente → admite e faz handoff; (c) nunca inventa preço, prazo, diagnóstico ou aconselhamento jurídico (regras por vertical + checagem pós-geração); (d) palavras/intenções de risco (reclamação grave, urgência médica, pedido de humano) → handoff imediato.
- **Modos:** `autopilot` (responde sozinha) e `copilot` (sugere ao atendente).
- **Humanização:** tom de voz do tenant, mensagens curtas e divididas, indicador "digitando", latência natural (0,8–3 s conforme tamanho), nome do cliente, memória entre conversas (resumo por contato), horário comercial.
- **CRM automático:** a cada turno, extração estruturada (nome, telefone, interesse, orçamento, urgência, etapa, tags) via tool call; gravada com auditoria (`field_changes`: quem, quando, valor anterior). Humano sobrescreve; campo editado por humano fica "travado" contra sobrescrita da IA.
- **Imagens/arquivos:** modelo multimodal para ler imagem/PDF recebidos.
- **Avaliação:** suíte de conversas de teste por vertical, com métricas de acerto, alucinação e handoff correto, rodando no CI.

## 8b. Onboarding adaptativo (produto para qualquer empresa)
Não há vertical fixa. Ao contratar, a empresa informa site, documentos e responde um questionário curto (o que vende, quem atende, horários, tom de voz, o que a IA nunca pode fazer). Um assistente de configuração (LLM) propõe, **para revisão e aprovação do admin**: persona e regras do agente, base de conhecimento inicial, campos do cadastro de contato, etapas do funil, setores/filas e gatilhos de handoff. Tudo é gravado como configuração do tenant (`agents`, `contact_fields`, `pipelines`, `departments`), editável depois. "Pacotes iniciais" por setor (clínica, imobiliária, etc.) podem existir como atalhos opcionais, mas nenhum é requisito.
Guardrails de domínio regulado (saúde, jurídico, financeiro) são **regras configuráveis por tenant**, com avisos padrão ligados quando o admin indicar o setor.

## 9. Roteamento e funil
- Setores → filas → regras de distribuição (round-robin, menos ocupado, por habilidade), limite simultâneo por atendente, horário por setor, transferência com histórico, fila de espera com tempo visível.
- Funil: `pipelines` e `stages` configuráveis por tenant, semeados por template de vertical; `deals` ligados a contato/conversa, valor, responsável, motivo de perda, movimentação manual ou pela IA.

## 10. Billing
Ver `PRICING.md`. Eventos de uso (`usage_events`) gravados por regra configurável e enviados ao Stripe como metered usage; cada atendimento cobrado tem registro auditável (motivo da cobrança).

## 11. Segurança e LGPD
Senhas com argon2; JWT curto + refresh; segredos só em variáveis de ambiente; tokens do WhatsApp criptografados em repouso; logs com redação de telefone/CPF/e-mail; exportar/apagar dados de contato; log de acesso; DPA e termos; backup diário com teste de restauração.

## 12. Metas de desempenho
API p95 < 500 ms · mensagem recebida na tela < 2 s · assumir conversa < 300 ms · carga: 50 atendentes simultâneos por tenant (k6).

## 13. Modelo de dados (núcleo)
```
tenants(id, name, plan_id, currency, settings)
users(id, tenant_id, email, role, skills[], max_concurrent)
channels(id, tenant_id, type, phone_number_id, waba_id, token_enc, status)
contacts(id, tenant_id, name, phone, email, fields jsonb, tags[], summary)
field_changes(id, tenant_id, contact_id, field, old, new, actor_type, actor_id, at)
departments(id, tenant_id, name, hours jsonb, distribution_rule)
conversations(id, tenant_id, contact_id, channel_id, department_id,
              assigned_to, status, version, last_message_at, ai_mode)
messages(id, tenant_id, conversation_id, direction, type, body, media_url,
         status, wamid, error_code, attempts, idempotency_key, sender_type, sender_id)
inbound_events(id, tenant_id, wamid UNIQUE, payload, processed_at)
notes(id, tenant_id, conversation_id, user_id, body)
pipelines(id, tenant_id, name) / stages(id, pipeline_id, name, position)
deals(id, tenant_id, contact_id, conversation_id, stage_id, value, owner_id, lost_reason)
agents(id, tenant_id, name, tone, rules, hours, mode, vertical_template)
knowledge_docs(id, tenant_id, title, source) / knowledge_chunks(id, doc_id, tenant_id, content, embedding vector)
plans(id, code, limits jsonb, price_brl, price_usd, included_attendances, overage_brl, overage_usd)
subscriptions(id, tenant_id, plan_id, stripe_ids, spend_cap, status)
usage_events(id, tenant_id, conversation_id, kind, reason, billable, at)
audit_log(id, tenant_id, actor, action, entity, at, meta)
```
Todas com RLS. Índices principais: `messages(conversation_id, created_at)`, `conversations(tenant_id, status, department_id)`, `messages(status) WHERE status IN ('queued','sending')`.
