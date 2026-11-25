# Preços e Margem (simulador) — Fase 0, revisado em 24/11/2026

## 1. Custos verificados

### Meta (WhatsApp Business Platform)
Fonte oficial: [developers.facebook.com/docs/whatsapp/pricing](https://developers.facebook.com/docs/whatsapp/pricing). Fonte dos valores em BRL: [ChatMaxima](https://chatmaxima.com/pt-br/whatsapp-api-pricing/brazil/) (terceiro, **não** é a tabela oficial; conferir no rate card/CSV da Meta antes de publicar preço).

- Cobrança é **por mensagem entregue** (desde jul/2025), não por conversa.
- Cobrança em **BRL** disponível desde jul/2026 (faturada pela Facebook Brasil); contas em USD precisam migrar até 30/06/2027.
- **Mensagens de serviço** (resposta ao cliente dentro da janela de 24 h): eram grátis; **desde 01/10/2026 são cobradas acima de 1.000 por número/mês**. Sem método de pagamento no Billing Hub, as acima da franquia não são entregues.
- Utility dentro da janela aberta: grátis até 30/09/2026; cobrada a partir de 01/10/2026.
- Janela gratuita de 72 h para conversas vindas de anúncio Click-to-WhatsApp (até 7 dias em alguns casos).

| Categoria | R$/mensagem (a partir de 01/10/2026, fonte de terceiro) |
|---|---|
| Marketing | 0,3217 (sem desconto por volume) |
| Utility | 0,0350 (desconto de volume até 25%) |
| Authentication | 0,0350 (desconto de volume até 25%) |
| Serviço (janela de 24 h) | 0,0350; primeiras 1.000/número/mês grátis |

Se for usar BSP, há taxa de plataforma à parte.

### LLM (Anthropic) — [tabela oficial](https://platform.claude.com/docs/en/about-claude/pricing), US$ por milhão de tokens
| Modelo | Entrada | Saída | Leitura de cache |
|---|---|---|---|
| Haiku 5.5 (prompts ≤ 100k) | 0,10 | 0,50 | 0,01 |
| Sonnet 5.5 | 2,00 | 10,00 | 0,10 |
| Opus 5.5 | 4,00 | 20,00 | 0,20 |

Batch API dá 50% de desconto (não serve para chat em tempo real). Modelos 4.7+ geram ~30% mais tokens para o mesmo texto.

### Custo por atendimento (premissas: 8 respostas da IA; 3.000 tokens de entrada e 150 de saída por resposta; câmbio **R$ 5,50/US$ — assumido, conferir**)
| Estratégia | US$ | R$ |
|---|---|---|
| Sonnet 5.5 sem cache | 0,060 | ≈ 0,33 |
| Sonnet 5.5 com cache de prompt | ≈ 0,03 | ≈ 0,15–0,20 |
| Haiku 5.5 | 0,003 | ≈ 0,02 |

Recomendação: Haiku 5.5 para triagem/classificação/extração e Sonnet 5.5 para a resposta ao cliente, com cache do prompt do tenant. Custo-alvo **≈ R$ 0,20 por atendimento**.

### Meta por atendimento
8 respostas da empresa × R$ 0,035 = **≈ R$ 0,28**, depois de esgotada a franquia de 1.000 mensagens/número/mês (≈ 125 atendimentos). Isso **não pode ficar absorvido** nos planos maiores.

## 2. Modelo de cobrança (revisado)
Mensalidade + franquia de atendimentos + excedente, e **mensagens WhatsApp acima da franquia da Meta repassadas em linha separada** ("custo Meta + 20%", configurável). Moeda por tenant; MVP em BRL.

| | Starter | Pro | Business |
|---|---|---|---|
| Mensalidade | R$ 197 | R$ 497 | R$ 1.197 |
| Atendimentos incluídos | 150 | 500 | 1.500 |
| Excedente por atendimento | R$ 1,50 | R$ 1,20 | R$ 0,90 |
| Atendentes humanos | 2 | 5 | 15 |
| Números WhatsApp | 1 | 1 | 2 |
| Mensagens Meta acima da franquia | repasse | repasse | repasse |

## 3. Margem (uso de 100% da franquia; LLM R$ 0,20/atend.; infra, Stripe ~4%+R$ 0,39 e impostos ~10% assumidos)
| | Starter | Pro | Business |
|---|---|---|---|
| Receita | 197,00 | 497,00 | 1.197,00 |
| LLM | 30,00 | 100,00 | 300,00 |
| Infra rateada | 20,00 | 30,00 | 60,00 |
| Stripe | 8,27 | 20,27 | 48,27 |
| Impostos | 19,70 | 49,70 | 119,70 |
| **Margem com repasse da Meta** | **119,03 (60%)** | **297,03 (60%)** | **669,03 (56%)** |
| Margem se **absorvermos** a Meta | 119,03 (60%) | 192,03 (39%) | 319,03 (27%) |

Conclusão: **repassar o custo da Meta**. Absorver derruba Pro e Business abaixo da margem mínima de 40%.

## 4. Pendências de verificação antes de publicar preço
1. Baixar o rate card/CSV oficial da Meta em BRL (os valores acima vêm de terceiro).
2. Confirmar câmbio e se a Meta/Anthropic cobram em USD ou BRL no seu caso.
3. Medir tokens reais por atendimento num protótipo.
4. Confirmar alíquota de impostos com contador.

## 5. Definição de atendimento cobrável (configurável)
Cobrável se: (a) a IA deu ao menos uma resposta útil e o cliente não pediu humano nem voltou em X horas (padrão 24 h) → "resolvido pela IA"; ou (b) foi atendido por humano (configurável por plano). Cada cobrança grava o motivo, aparece ao cliente e pode ser contestada. Teto de gasto com alertas em 80% e 100%.

## 6. Stripe
Assinatura + metered usage; trial de 14 dias; proração; fatura em PDF; dunning; Pix/boleto na fase 4.
