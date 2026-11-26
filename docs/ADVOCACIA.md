# Plataforma para escritórios de advocacia

## O que ela faz
1. **O cliente escreve no WhatsApp.** O assistente responde na hora.
2. **Encaminha para o setor certo.** A IA identifica o assunto e manda a conversa para a fila do setor (ex.: Previdenciário). Se o setor tem **responsável direto** (ex.: Dr. Pedro em Precatórios), a conversa vai direto para a fila pessoal dele.
3. **Explica em linguagem simples.** Dúvidas gerais ("o que é RPV?") recebem uma explicação como para quem nunca leu um processo, com o aviso de que o caso concreto é com o advogado.
4. **Marca consulta quando o caso exige.** Nos setores com consulta paga (aposentadoria, trabalhista, família), o assistente oferece três horários livres da agenda do advogado; o cliente responde o número e a consulta é marcada (e vai para o Google Agenda, se conectado). Precatórios e dúvidas são resolvidos pelo chat, sem consulta.
5. **Na dúvida, pergunta.** Se a mensagem é vaga ou serve para dois setores, o assistente mostra um menu numerado em vez de arriscar o setor errado.
6. **Pede uma pessoa?** Vai para o atendimento geral na hora.

## Filas e permissões
- Cada pessoa vê: **Meus atendimentos** (o que ela assumiu ou recebeu direto), **Em triagem pela IA** e as **filas dos setores dela**.
- Só assume conversas dos setores em que trabalha. O administrador vê e assume tudo.
- Transferir: para a fila de outro setor (se tiver responsável direto, vai para ele) ou direto para uma pessoa.

## Regras da IA (OAB)
Não dá parecer sobre o caso, não diz se a pessoa tem direito, não estima valores, prazos ou chances, não promete resultado e não faz propaganda (Código de Ética e Provimento 205/2021). Explica conceitos gerais, sempre com o aviso de que quem confirma é o advogado.

## IA real x IA simulada
- **Sem chave** (`ANTHROPIC_API_KEY` vazio): IA simulada por regras. Usa as palavras de cada setor (palavras com `!` valem mais) e o glossário em `app/ai/knowledge.py`. Gratuita e previsível.
- **Com chave**: Claude (modelo `claude-opus-5-5`, esforço baixo, resposta em formato estruturado, cache do prompt). Entende contexto e frases mais soltas. Se a Anthropic falhar ou responder fora do formato, cai na IA simulada sozinha. Cobra por uso.
- A taxa de acerto da IA simulada é medida em `tests/test_law_firm.py` (frases reais de clientes). **A IA real ainda não foi testada com a chave**; antes de usar com clientes, rode o mesmo conjunto com a chave ligada.

## Como testar
1. `.\run.ps1` e abra http://127.0.0.1:8000, clique em **Comprar plano**.
2. Conecte um número de teste, vá em **Equipe e setores** e clique em **Criar equipe de exemplo** (Pedro, Ana, Carla, Júlia, Bruno; a senha aparece na tela).
3. Em **Atendimento**, use **+ Cliente de teste** e mande, por exemplo, "quero vender meu precatório" ou "quero me aposentar".
4. Saia e entre como um dos advogados para ver as filas de cada um.
