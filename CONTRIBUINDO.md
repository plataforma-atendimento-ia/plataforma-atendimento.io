# Guia para os sócios: como mexer no projeto

## 1. Instalar uma vez (Windows)
No PowerShell:
```powershell
winget install --id Git.Git -e --source winget
winget install --id Python.Python.3.12 -e --source winget
```
Feche e abra o PowerShell depois de instalar.

## 2. Baixar o projeto
Crie sua conta gratuita em github.com e passe seu usuário ao dono (IohanCarpen), que vai convidar você. Aceite o convite que chega no seu e-mail. Depois:
```powershell
git clone https://github.com/IohanCarpen/plataforma-atendimento.git
cd plataforma-atendimento
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest pytest-asyncio
copy .env.example .env
```

## 3. Rodar e testar
```powershell
.\run.ps1      # abre em http://127.0.0.1:8000
.\test.ps1     # deve mostrar todos os testes passando
```

## 4. Como programar juntos sem se atropelar
Os três têm liberdade total para editar e enviar direto na `main`. Para não perder o trabalho um do outro, repita este ciclo, com mudanças pequenas e frequentes:
```powershell
git pull                           # SEMPRE antes de começar a editar
# ...edite os arquivos (à mão ou pedindo ao Claude Code)...
.\test.ps1                         # só envie se os testes passarem
git add -A
git commit -m "Descreva o que mudou"
git pull                           # de novo, para pegar o que os outros enviaram
git push
```
- Combinem no grupo quem mexe em qual arquivo ou parte, para não editar o mesmo trecho ao mesmo tempo.
- Se o `git pull` avisar de **conflito**, não force nada: peça ao Claude Code para resolver o conflito, ou chame um sócio.
- Para uma mudança grande ou arriscada, vale usar uma branch separada: `git checkout -b nome-da-ideia` e `git push -u origin nome-da-ideia`. Depois combinem juntos antes de juntar na `main`.
- Se mexer em mensagens, fila, cobrança ou isolamento entre empresas, ajuste ou crie um teste.

## 5. Regras de segurança
- **Nunca** suba o arquivo `.env`, chaves da Meta, da Anthropic ou senhas. O `.gitignore` já bloqueia `.env`, mas confira antes de cada commit com `git status`.
- Use sempre `.env.example` para documentar uma variável nova, sem o valor real.
- Dados de clientes reais não entram no repositório.

## 6. Usando o Claude Code
Cada sócio pode abrir o Claude Code na pasta do projeto e pedir mudanças, ideias e revisões. Comece a conversa pedindo: "leia o README.md e a pasta docs antes de mexer". O ciclo do item 4 vale igual: `git pull` antes, testes passando, `git push` depois. Peça ao Claude para registrar decisões novas em `docs/DECISIONS.md`.

## 7. Onde ler mais
- `README.md`: o que funciona e o que falta.
- `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/PRICING.md`, `docs/RISKS_AND_BACKLOG.md`: decisões e planejamento.
- Registre toda decisão nova em `docs/DECISIONS.md`.
