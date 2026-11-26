# Registro de Decisões

Formato: data · decisão · motivo.

| Data | Decisão | Motivo |
|---|---|---|
| 2026-11-24 | Monólito modular + 1 worker, sem microserviços | Time de 2; menos infra, deploy simples |
| 2026-11-24 | TypeScript no backend e no frontend (NestJS + Next.js) | Um idioma, tipos compartilhados, contratação mais fácil; Python só se o time já dominar FastAPI |
| 2026-11-24 | PostgreSQL com RLS desde o dia 1 | Isolamento de tenant garantido no banco; difícil de retrofitar depois |
| 2026-11-24 | pgvector em vez de banco vetorial separado | Menos um serviço; suficiente para o volume do MVP |
| 2026-11-24 | BullMQ + Redis para filas e Socket.IO para tempo real | Maduro, simples; mesma infra Redis |
| 2026-11-24 | WhatsApp só pela Cloud API oficial; nada de QR code/API não-oficial | Evita banimento de número e risco jurídico |
| 2026-11-24 | Cloud API direta no MVP; BSP (360dialog) como plano B | Sem taxa de intermediário; trocar via `ChannelProvider` |
| 2026-11-24 | Padrão outbox com idempotência e retry exponencial | Corrige falha de envio (dor nº 1 do Amplific) |
| 2026-11-24 | Assumir conversa por UPDATE condicional atômico | Corrige travamento (dor nº 2) |
| 2026-11-24 | `LLMProvider` abstrato, Claude como padrão | Trocar modelo sem refatorar; controle de custo por modelo |
| 2026-11-24 | **Produto horizontal (qualquer empresa), sem vertical fixa.** Substitui a decisão anterior de começar por clínica | Pedido do dono: adaptar à empresa contratante. Tudo (agente, campos, funil, regras) é configuração por tenant; vertical vira só "pacote inicial" opcional |
| 2026-11-24 | Onboarding adaptativo: empresa informa site/documentos/questionário e a IA propõe agente, campos do CRM, etapas do funil, FAQ e regras de handoff; o admin revisa e aprova | Substitui templates fixos por vertical; reduz tempo de ativação |
| 2026-11-24 | Meta cobra mensagens de serviço desde 01/10/2026 (acima de 1.000/número/mês): repassar custo Meta em linha separada | Absorver derrubaria a margem de Pro/Business para 39% e 27% (ver PRICING.md) |
| 2026-11-24 | Modelos: Haiku 5.5 para triagem/extração, Sonnet 5.5 para resposta, com cache de prompt | Custo-alvo ≈ R$ 0,20 por atendimento (preços oficiais da Anthropic) |
| 2026-11-24 | MVP só em BRL; USD na fase 2 | Reduz complexidade de impostos/Stripe no lançamento |
| 2026-11-24 | Cobrança "atendimento" = conversa resolvida pela IA OU atendida por humano, regra configurável | Alinhado ao pedido; auditável por atendimento |
| 2026-11-24 | Funil kanban simples (sem automações complexas) no MVP | Pilar obrigatório, mas sem construtor visual |
| 2026-11-24 | Sem cliente piloto ainda: Fase 5 depende de recrutar 3–5 betas | Registrar como risco de negócio |
| 2026-11-26 | **Foco no nicho de advocacia** (substitui "produto horizontal"): setores com filas, responsável direto, consultas pagas só onde a análise exige, IA de triagem que explica em linguagem simples sem parecer (OAB), Google Agenda | Pedido do dono; concorrente cobra R$ 15 mil para personalizar por nicho |
| 2026-11-26 | IA real = Claude pela biblioteca oficial (`claude-opus-5-5`, esforço baixo, structured outputs, cache do system prompt, fallback do servidor); sem chave, IA simulada por regras; falha da IA real cai na simulada | Funciona sem custo para testes; troca sem mexer no fluxo |
| 2026-11-26 | Encaminhamento só com confiança >= 60%; abaixo disso, menu numerado | "Sem erro de setor": melhor perguntar do que errar |
| 2026-11-25 | Número real do dono conecta pela **API oficial da Meta** (formulário no sistema, credenciais conferidas na Meta antes de salvar). QR Code/API não oficial descartado | Dono escolheu a opção oficial; evita banimento do número |
| 2026-11-25 | **Revisão:** o dono pediu também a conexão por QR Code, sem Meta, ciente do risco de banimento. Implementada como **opção extra** (ponte Node.js com Baileys 7.0.0-rc14 em `bridge/`), ao lado da API oficial. Ponte só em 127.0.0.1 com segredo, sessões fora do OneDrive, limite de 20 envios/min | Dono decidiu; a opção oficial continua recomendada para uso profissional |
| 2026-11-25 | Provedor de envio escolhido **por canal** (teste = simulado, real = Meta); simulador de cliente nunca escreve em número real | Evita misturar teste com produção |
| 2026-11-25 | Qualificação de leads por **roteiro fixo sem IA** (nome, interesse, urgência, investimento → quente/morno/frio), desligável, para na hora se o cliente pedir humano ou um atendente assumir | Permite testar o fluxo sem chave de IA; vira conversa por IA na Fase 3 |
| 2026-11-25 | `TEST_MODE` (padrão ligado) controla compra simulada, número de teste e simulador; em produção deve ser `false` | Impede plano grátis e números falsos com clientes reais |

## Pendentes (precisam do seu ok)
1. Confirmar TypeScript (vs Python).
2. Confirmar Cloud API direta (vs BSP).
3. Confirmar repasse do custo Meta em linha separada (com ou sem margem).
