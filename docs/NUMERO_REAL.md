# Conectar o seu WhatsApp real (API oficial da Meta)

Este guia leva do zero até receber no site as mensagens do seu número. Use um número de teste da Meta primeiro.

## O que você precisa
1. Uma conta no Facebook e acesso ao **Meta for Developers** (developers.facebook.com).
2. O sistema com um **endereço público HTTPS** (a Meta entrega as mensagens por webhook). Duas opções:
   - **Túnel temporário** (para testar em casa): `cloudflared tunnel --url http://127.0.0.1:8000`. Ele imprime um endereço `https://alguma-coisa.trycloudflare.com`. Muda a cada vez que roda.
   - **Hospedagem** (para uso de verdade): Railway, Render ou Fly. Aí o endereço é fixo.
3. No `.env` do servidor: `WHATSAPP_VERIFY_TOKEN` com um texto secreto seu e, depois, `WHATSAPP_APP_SECRET`.

## Passo a passo
1. **Criar o app:** Meta for Developers > Meus apps > Criar app > tipo **Business** > adicione o produto **WhatsApp**.
2. **Número de teste:** em *WhatsApp > Configuração da API* a Meta dá um número de teste grátis e um **Phone number ID**. Você pode cadastrar até 5 números de destinatário para conversar com ele.
3. **Token:** na mesma tela, gere um **token de acesso**. O temporário vale 24 horas. Para uso contínuo, crie um *usuário do sistema* no Meta Business e gere um token permanente.
4. **Webhook:** em *WhatsApp > Configuração*, clique em editar o webhook:
   - **URL de callback:** `https://SEU-ENDERECO-PUBLICO/webhooks/whatsapp`
   - **Token de verificação:** o mesmo valor de `WHATSAPP_VERIFY_TOKEN` (a tela do sistema mostra, com botão de copiar).
   - Depois de verificar, **assine o campo `messages`**.
5. **No sistema** (aberto pelo endereço público): compre o plano > *Meu número real (Meta)* > preencha o número, o Phone number ID e o token > **Conectar e verificar**. O sistema confere as credenciais na Meta antes de salvar.
6. **Teste:** do celular de um dos números cadastrados, mande uma mensagem para o número da Meta. Ela aparece na fila. Com a automação ligada, o cliente recebe as perguntas de qualificação.

## Número seu de verdade (produção)
- Verifique a empresa no Meta Business e adicione uma forma de pagamento (a Meta cobra por mensagem desde 01/10/2026, inclusive as respostas dentro das 24 horas, acima de 1.000 por mês por número).
- O número passa a ser usado pela API. Se ele está no aplicativo comum do WhatsApp, confira as regras atuais da Meta (existe um modo de coexistência) antes de migrar, porque o aplicativo pode deixar de receber mensagens.
- O sistema só mostra mensagens a partir da conexão. O histórico antigo do celular não é importado.

## Limites conhecidos
- **Só texto.** Imagens, áudios e documentos chegam como "[tipo não suportado ainda]".
- **Janela de 24 horas.** A Meta só aceita texto livre até 24 horas depois da última mensagem do cliente. Fora disso é preciso um modelo (template) aprovado, que ainda não existe aqui. A mensagem aparece como "falhou" com o motivo.
- O envio pela Meta **não tem chave de idempotência**: se o servidor cair no instante do envio, pode haver uma duplicata rara.
- O token da Meta fica no banco **sem criptografia** por enquanto (pendência da Fase 4).

## Automação e qualificação de leads (como funciona hoje)
Roteiro fixo, **sem IA**, que roda quando um contato novo escreve e ninguém assumiu a conversa:
1. Cumprimenta e pergunta o **nome**.
2. Pergunta o **interesse** (texto livre).
3. Pergunta a **urgência** (1 hoje, 2 esta semana, 3 sem pressa).
4. Pergunta a **faixa de investimento** (1 até R$ 500, 2 de R$ 500 a R$ 2.000, 3 acima de R$ 2.000).

Classificação: urgência (3, 2 ou 1 ponto) + investimento (3, 2 ou 1) → **quente** (5 ou mais), **morno** (3 ou 4), **frio** (2). A ficha aparece no topo da conversa e o selo na lista.
A automação **para sozinha** quando o cliente pede um atendente ("atendente", "humano", "falar com alguém"), quando um atendente assume a conversa ou quando o administrador a desliga (botão *Automação* no topo).
As perguntas e os valores ainda são fixos. Na Fase 3 isso vira uma conversa conduzida por IA com perguntas configuráveis.
