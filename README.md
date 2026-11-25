# Plataforma de Atendimento com IA — Fases 1 e 2

Backend FastAPI (Python) + caixa de entrada web em tempo real. Multi-tenant. Funciona **sem WhatsApp real e sem Docker** (canal simulado + SQLite).

## Como rodar
```powershell
.\run.ps1          # sobe em http://127.0.0.1:8000
.\test.ps1         # roda os testes
```
Em http://127.0.0.1:8000 fica o **site de vendas** (`app/static/site/index.html`, uma página única); o sistema de atendimento fica em http://127.0.0.1:8000/app/. Antes de publicar o site: trocar o nome provisório da marca e confirmar os preços (ver `docs/PRICING.md`).

No sistema (/app/), clique em **Criar conta da empresa**, depois em **Simular cliente** para gerar uma conversa. Abra uma segunda janela anônima com outro usuário (admin cria atendentes em `POST /users`, docs em `/docs`) para testar "assumir" simultâneo.

## O que já funciona
- Cadastro de empresa (tenant), login, usuários e papéis (admin/atendente), isolamento entre empresas.
- **Outbox**: toda mensagem é gravada como `queued` antes de enviar; worker com retry exponencial (1s, 5s, 30s, 2min, 10min; máx. 6), `failed` visível com motivo e botão **Reenviar**; reconciliador de mensagens presas em `sending`.
- Status de entrega (`sent → delivered → read`) via webhook, sem regressão se chegar fora de ordem.
- **Assumir conversa atômico** (UPDATE condicional): se dois clicam juntos, um ganha e o outro recebe 409. Limite de conversas simultâneas por atendente.
- Webhook da WhatsApp Cloud API (verificação, assinatura HMAC opcional, entrada idempotente por `wamid`).
- Tempo real por WebSocket (nova mensagem, status, conversa assumida).
- 14 testes automatizados (isolamento A×B, assumir atômico, outbox, retry, idempotência, webhook).

## O que ainda NÃO existe (e por quê)
- Notas internas e tags, setores/filas/distribuição, funil kanban (Fase 2b).
- IA, RAG, handoff, CRM automático (Fase 3). Cobrança/Stripe (Fase 4).
- **RLS em Postgres**: o script está em `db/rls_postgres.sql`, mas **não foi testado** (rodamos em SQLite). Hoje o isolamento é garantido pela aplicação e coberto por testes; antes de qualquer cliente real, aplicar o RLS em um Postgres e repetir os testes.
- Redis/fila externa: o MVP usa o próprio banco como fila e o WebSocket em um único processo. Para mais de uma instância, trocar o `Hub` por Redis pub/sub.
- Teste de carga (50 atendentes) e métricas de p95: ainda não medidos.
- Mídia (imagem/áudio/PDF), templates da Meta e janela de 24h: só texto por enquanto.
- Tokens do WhatsApp ainda sem criptografia em repouso.

## Contas necessárias para ir além do simulado
1. **Meta (WhatsApp Cloud API)**: conta em developers.facebook.com → criar app tipo "Business" → adicionar produto WhatsApp → pegar `phone_number_id` e token. Para produção: verificar a empresa no Meta Business e cadastrar método de pagamento (necessário desde 01/10/2026). Cadastre o canal com `POST /channels` e aponte o webhook para `https://SEU-DOMINIO/webhooks/whatsapp` com o `WHATSAPP_VERIFY_TOKEN`. Depois: `CHANNEL_PROVIDER=whatsapp`.
2. **Anthropic (IA, Fase 3)**: chave em console.anthropic.com → `ANTHROPIC_API_KEY`.

## Estrutura
```
app/          código (config, db, models, outbox, services, routers/, channels/, static/)
tests/        testes automatizados
db/           SQL de RLS para Postgres
docs/         arquitetura, preços, decisões, riscos
```
