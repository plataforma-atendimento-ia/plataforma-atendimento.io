# O que falta acertar (levantamento de 26/11/2026)

Estado verificado: 84 testes automáticos passando, Python compilando sem avisos, código da tela sem erro de sintaxe.
O fluxo completo (planos → conta → pagamento de teste → pesquisa do escritório → atendimento) foi conferido na tela.

## 1. Antes de qualquer cliente real (bloqueia o lançamento)
| # | Pendência | Por quê |
|---|---|---|
| 1 | **Limite de tentativas de login** (bloquear após várias senhas erradas) | Hoje dá para tentar senhas sem limite. |
| 2 | **Proteção contra envio em massa** no formulário do Personalizado e no cadastro | São públicos; um robô pode encher o banco. |
| 3 | **Hospedagem** com endereço fixo e HTTPS | Hoje só roda no computador do dono (ou por túnel temporário). |
| 4 | **Banco Postgres + RLS testado** | Hoje é SQLite; o script de RLS existe mas nunca rodou. |
| 5 | **Pagamento real** (Stripe: cartão, Pix, boleto) | Hoje o pagamento é simulado. |
| 6 | **Criptografar tokens** (Meta, Google) no banco | Hoje ficam em texto puro. |
| 7 | **Revisão jurídica (LGPD e OAB)**: termos de uso, política de privacidade, contrato de dados | Dados de clientes de escritório são sensíveis. |
| 8 | `TEST_MODE=false` em produção | Senão qualquer um "paga" e cria número de teste. |

## 2. Funções prometidas que ainda não existem
- **Limites dos planos**: atendimentos/mês e pessoas na equipe aparecem nos planos mas não são aplicados (só o limite de números de WhatsApp é).
- **Esqueci minha senha** (precisa de um serviço de envio de e-mail).
- **Tela para ver os pedidos do plano Personalizado** (hoje ficam só no banco).
- **Remarcar/cancelar consulta no Google Agenda** (hoje só cria o evento).
- **Mensagens com imagem, áudio e documento** (hoje só texto).
- **Site de vendas**: ainda fala de forma genérica; reposicionar para escritórios de advocacia.
- Aviso de "configurando…" ao finalizar a pesquisa do escritório (leva alguns segundos sem retorno visual).

## 3. Pronto no código, mas nunca testado de verdade
| O quê | O que precisa | Guia |
|---|---|---|
| IA real (Claude) | Chave em console.anthropic.com no `.env`; rodar o conjunto de frases de clientes com a chave ligada | docs/ADVOCACIA.md |
| Google Agenda | Credenciais OAuth do Google | docs/AGENDA_GOOGLE.md |
| WhatsApp pela API oficial | Conta Meta + endereço público | docs/NUMERO_REAL.md |
| WhatsApp por QR Code | Ler o QR com um celular (chip secundário: risco de banimento) | docs/NUMERO_QR.md |

## 4. Qualidade
- Não há testes automáticos da tela (só do servidor). Vale um teste de ponta a ponta no navegador para o fluxo de assinatura.
- Pagamento, limites e permissões merecem revisão de segurança antes do beta.

## Sugestão de ordem
1. Segurança básica (itens 1, 2, 6 e 8) — rápido e barato.
2. Hospedagem + Postgres (3 e 4) — permite os sócios e clientes testarem de qualquer lugar.
3. IA real com a chave — é o coração do produto; medir a taxa de acerto.
4. Pagamento real e limites dos planos.
5. Revisão jurídica e beta com 3 a 5 escritórios.
