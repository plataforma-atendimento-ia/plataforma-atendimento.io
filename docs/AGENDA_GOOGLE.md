# Conectar o Google Agenda

Com o Google Agenda conectado, a IA só oferece horários de consulta em que o advogado está livre na agenda dele, e cada consulta marcada vira um evento na agenda.

## 1. Uma vez por escritório (administrador)
1. Acesse **console.cloud.google.com** com a conta Google do escritório e crie um projeto (ex.: "Atendimento").
2. Em **APIs e serviços > Biblioteca**, ative a **Google Calendar API**.
3. Em **APIs e serviços > Tela de consentimento OAuth**, escolha o tipo **Interno** (se o escritório usa Google Workspace) ou **Externo** e preencha nome e e-mail. Enquanto o app estiver em modo de teste no Google, cadastre como "usuários de teste" os e-mails dos advogados.
4. Em **APIs e serviços > Credenciais > Criar credenciais > ID do cliente OAuth**, tipo **Aplicativo da Web**.
   - Em **URIs de redirecionamento autorizados**, cadastre exatamente o endereço que a tela **Agenda** do sistema mostra, por exemplo `http://127.0.0.1:8000/google/callback` (no computador) ou `https://SEU-DOMINIO/google/callback` (hospedado).
5. Copie o **ID do cliente** e a **chave secreta** para o arquivo `.env` do servidor:
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   PUBLIC_BASE_URL=http://127.0.0.1:8000
   ```
6. Reinicie o sistema (`.\run.ps1`).

## 2. Cada advogado
1. Entre no sistema, vá em **Agenda** e clique em **Conectar meu Google Agenda**.
2. Faça login na conta Google dele e autorize. Ele volta para o sistema com a agenda conectada.
3. Confira o **horário de atendimento** na mesma tela. A IA só oferece consultas dentro desse horário e fora dos compromissos do Google.

## Como funciona
- Horário livre = dentro do horário de atendimento, sem consulta marcada no sistema e sem compromisso no Google Agenda.
- Permissões pedidas ao Google: ver se a agenda está livre ou ocupada e criar eventos. O sistema não lê o conteúdo dos outros compromissos.
- Se o Google estiver fora do ar, a IA usa só o horário de atendimento e as consultas do sistema, e a consulta continua sendo marcada aqui.
- Fuso: horário de Brasília (`OFFICE_UTC_OFFSET_HOURS=-3`).

## Limites conhecidos
- O token do Google fica no banco **sem criptografia** por enquanto (pendência da Fase 4).
- Remarcar ou cancelar no sistema **não** altera o evento no Google ainda: ajuste no Google manualmente.
- Não foi testado contra uma conta Google real: os testes automáticos simulam as respostas do Google.
