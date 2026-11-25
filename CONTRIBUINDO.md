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

## 4. Como trabalhar sem atrapalhar os outros
Nunca edite direto na branch `main`. Para cada mudança:
```powershell
git pull
git checkout -b nome-curto-da-mudanca
# ...edite os arquivos...
.\test.ps1                         # só envie se os testes passarem
git add -A
git commit -m "Descreva o que mudou"
git push -u origin nome-curto-da-mudanca
```
No GitHub, abra um **Pull Request**. Um dos outros sócios revisa e aprova antes de entrar na `main`. Se mexer em mensagens, fila, cobrança ou isolamento entre empresas, escreva ou ajuste um teste.

## 5. Regras de segurança
- **Nunca** suba o arquivo `.env`, chaves da Meta, da Anthropic ou senhas. O `.gitignore` já bloqueia `.env`, mas confira antes de cada commit com `git status`.
- Use sempre `.env.example` para documentar uma variável nova, sem o valor real.
- Dados de clientes reais não entram no repositório.

## 6. Usando o Claude Code
Cada sócio pode abrir o Claude Code na pasta do projeto e pedir mudanças. As regras do item 4 valem igual: branch própria, testes passando, Pull Request.

## 7. Onde ler mais
- `README.md`: o que funciona e o que falta.
- `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/PRICING.md`, `docs/RISKS_AND_BACKLOG.md`: decisões e planejamento.
- Registre toda decisão nova em `docs/DECISIONS.md`.
