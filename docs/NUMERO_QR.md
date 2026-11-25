# Conectar o WhatsApp por QR Code (NÃO OFICIAL)

> **Risco:** isto usa o protocolo do WhatsApp Web por uma biblioteca de terceiros (Baileys). **Viola os termos do WhatsApp**
> e pode causar o **banimento permanente do número**. Use um chip secundário que você aceite perder. Para uso profissional,
> use a API oficial da Meta ([NUMERO_REAL.md](NUMERO_REAL.md)). O WhatsApp pode também mudar o protocolo e quebrar a ponte a qualquer momento.

## Como funciona
- A pasta `bridge/` tem um programa em Node.js (a **ponte**) que mantém a sessão do WhatsApp, gera o QR Code, recebe as mensagens e envia as respostas.
- A ponte só conversa com o sistema, por `127.0.0.1`, com uma senha (`BRIDGE_SECRET` no `.env`). Ela não é exposta à internet.
- As credenciais da sessão ficam em `%LOCALAPPDATA%\plataforma-bridge\auth`, **fora** do projeto e do OneDrive. Quem tiver esses arquivos controla o WhatsApp conectado: não copie nem compartilhe.

## Como usar
1. Rode `.\run.ps1`. Ele liga a ponte (se o Node.js e a pasta `bridge/node_modules` existirem) e o sistema. Se faltar `node_modules`, rode `.\run-bridge.ps1` uma vez.
2. Compre um plano no site, vá em **QR Code (não oficial)** e clique em **Gerar QR Code**.
3. No celular: WhatsApp > Configurações > **Dispositivos conectados** > **Conectar dispositivo** e aponte para o QR.
4. Quando conectar, a tela avança sozinha. As mensagens individuais recebidas no número aparecem na fila.

## O que a ponte faz e não faz
- **Recebe** mensagens de texto individuais. Imagens, áudios e documentos chegam como "[tipo de mensagem não suportado ainda]". **Grupos, status e canais são ignorados.**
- **Não importa o histórico** antigo, só o que chegar depois da conexão.
- **Mensagens que você envia pelo próprio celular não aparecem** no sistema (só as respostas dadas por aqui e as mensagens recebidas).
- **Envia** respostas com ritmo humano (mostra "digitando", pequena espera) e **no máximo 20 por minuto** por número (`BRIDGE_MAX_PER_MINUTE`). Isso reduz, mas **não elimina**, o risco de banimento.
- Contatos com o endereço novo do WhatsApp (LID): o sistema guarda o endereço exato para responder e usa o telefone quando o WhatsApp o informa.
- Se a sessão cair (ex.: você removeu o aparelho no celular), o canal vira "desconectado" e é preciso ler um QR novo.
- A automação de qualificação de leads funciona igual à do número de teste e da Meta.

## Tem custo?
Não há cobrança da Meta, porque não usa a API oficial. A ponte roda no seu computador (Node.js e bibliotecas, uns 50 MB).

## Em produção
A ponte precisa ficar sempre ligada, no mesmo servidor do sistema. Como a conexão é feita por um aparelho "vinculado", o celular precisa abrir o WhatsApp de vez em quando para manter o vínculo (regra do WhatsApp: uns 14 dias).
