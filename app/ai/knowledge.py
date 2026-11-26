"""Conhecimento da IA jurídica: setores padrão de um escritório e explicações em linguagem simples.

As explicações são GERAIS e escritas para quem nunca leu um processo: sem juridiquês, frases curtas,
exemplos do dia a dia. Nunca opinam sobre o caso da pessoa (isso é do advogado, e a OAB veda a IA
de dar parecer ou prometer resultado)."""
import re
import unicodedata


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip()


# Setores sugeridos para um escritório (o escritório edita tudo na tela "Equipe e setores").
# Palavra com "!" na frente define o assunto e vale mais (ex.: "!aposentar" pesa mais que "carteira assinada").
DEFAULT_DEPARTMENTS = [
    {
        "slug": "geral",
        "name": "Atendimento geral",
        "color": "#5b6b7f",
        "is_default": True,
        "description": "Primeiro contato, assuntos que não se encaixam nos outros setores, andamento de processo e documentos.",
        "keywords": "andamento, meu processo, documento, endereco do escritorio, horario de funcionamento",
    },
    {
        "slug": "civel",
        "name": "Ações comuns (cível e consumidor)",
        "color": "#2b59c3",
        "description": "Problemas com bancos, lojas, planos de saúde, cobranças, contratos, aluguel, golpes e indenizações.",
        "keywords": "!negativado, !nome sujo, !cobranca indevida, !danos morais, !golpe, banco, emprestimo, consignado, nome sujo, negativado, serasa, spc, cobranca indevida, cobranca, "
        "compra, produto com defeito, loja, plano de saude, danos morais, indenizacao, acidente de transito, "
        "consumidor, contrato, aluguel, despejo, golpe, pix, cartao de credito, juros, conta de luz, companhia aerea, voo",
    },
    {
        "slug": "previdenciario",
        "name": "Previdenciário (INSS e aposentadoria)",
        "color": "#0a7a55",
        "needs_consult": True,
        "consult_price_cents": 20000,
        "description": "Aposentadoria, benefícios do INSS, BPC/LOAS, auxílio-doença, pensão por morte, revisões e perícias.",
        "keywords": "!aposentadoria, !aposentar, !aposentado, !inss, !bpc, !loas, !pensao por morte, !auxilio doenca, beneficio, auxilio doenca, auxilio-doenca, "
        "auxilio acidente, pensao por morte, revisao, revisao da vida toda, pericia, incapacidade, invalidez, "
        "tempo de contribuicao, cnis, salario maternidade, previdencia, beneficio negado",
    },
    {
        "slug": "trabalhista",
        "name": "Trabalhista",
        "color": "#b4540a",
        "needs_consult": True,
        "consult_price_cents": 20000,
        "description": "Problemas com patrão ou empresa: demissão, rescisão, FGTS, horas extras, salário atrasado, assédio.",
        "keywords": "!demissao, !demitido, !demitida, !rescisao, !fgts, !justa causa, !horas extras, mandado embora, mandada embora, fui mandado, rescisao, fgts, horas extras, hora extra, "
        "carteira assinada, sem carteira, patrao, salario atrasado, justa causa, assedio, ferias, "
        "decimo terceiro, 13o salario, seguro desemprego, acidente de trabalho, verbas rescisorias, empresa nao pagou",
    },
    {
        "slug": "familia",
        "name": "Família e sucessões",
        "color": "#7a3aa8",
        "needs_consult": True,
        "consult_price_cents": 20000,
        "description": "Divórcio, pensão alimentícia, guarda dos filhos, união estável, inventário e herança.",
        "keywords": "!divorcio, !divorciar, !pensao alimenticia, !guarda, !inventario, divorcio, divorciar, me separar, separacao, separar do marido, separar da esposa, pensao alimenticia, pensao para o filho, alimentos, guarda, guarda do filho, "
        "visitas, uniao estavel, casamento, partilha, inventario, heranca, testamento, falecimento, pai faleceu, mae faleceu",
    },
    {
        "slug": "precatorios",
        "name": "Precatórios",
        "color": "#9a6b00",
        "description": "Precatórios e RPV: valores que o governo foi condenado a pagar. Compra, venda e antecipação.",
        "keywords": "!precatorio, !precatorios, !rpv, requisicao de pequeno valor, vender precatorio, venda de precatorio, "
        "antecipar precatorio, antecipacao de precatorio, credito judicial, governo me deve, estado me deve, "
        "prefeitura me deve, uniao me deve, fila de pagamento do governo",
    },
]

# Explicações gerais, humanizadas. Chave = termo como o cliente escreve (sem acento).
GLOSSARY = {
    "precatorio": (
        "Precatório é o jeito que o governo (União, estado ou prefeitura) paga uma dívida que ele perdeu na Justiça. "
        "Pense assim: a pessoa ganhou o processo, o governo tem que pagar, mas esse pagamento entra numa fila "
        "e pode levar anos. Por isso tem gente que prefere vender esse direito e receber antes, com um desconto."
    ),
    "rpv": (
        "RPV é parecido com o precatório, só que para valores menores. Como o valor é menor, o governo paga "
        "bem mais rápido, normalmente em alguns meses, sem aquela fila longa."
    ),
    "aposentadoria": (
        "Aposentadoria é o valor que o INSS paga todo mês quando a pessoa cumpre as regras de idade e de tempo "
        "em que contribuiu (os anos de carteira assinada ou pagando o INSS por conta própria). As regras mudam "
        "de pessoa para pessoa, por isso o advogado olha os seus documentos antes de dizer qual é o melhor caminho."
    ),
    "bpc": (
        "O BPC, também chamado de LOAS, é um salário mínimo por mês pago pelo INSS para idosos a partir de 65 anos "
        "ou para pessoas com deficiência, quando a família tem renda baixa. Não precisa ter pago o INSS antes."
    ),
    "loas": (
        "LOAS é o nome mais conhecido do BPC: um salário mínimo por mês para idosos a partir de 65 anos ou pessoas "
        "com deficiência, quando a renda da família é baixa. Não exige ter contribuído com o INSS."
    ),
    "auxilio doenca": (
        "Auxílio-doença (hoje o nome oficial é benefício por incapacidade temporária) é o que o INSS paga quando a "
        "pessoa fica um tempo sem conseguir trabalhar por causa de uma doença ou acidente. Normalmente precisa "
        "passar por uma perícia, que é a avaliação feita por um médico do INSS."
    ),
    "pericia": (
        "Perícia é uma avaliação feita por um especialista. No INSS, é o médico que examina a pessoa para ver se ela "
        "consegue ou não trabalhar. Na Justiça, o juiz pode pedir uma perícia para tirar uma dúvida técnica."
    ),
    "processo": (
        "Um processo (ou ação) é o pedido formal que o advogado faz ao juiz para resolver um problema. A partir daí, "
        "o juiz ouve os dois lados, analisa as provas e decide quem tem razão."
    ),
    "audiencia": (
        "Audiência é uma reunião marcada pelo juiz, em que as partes conversam, às vezes tentam um acordo e podem "
        "falar o que aconteceu. O advogado acompanha e orienta antes, então ninguém vai sozinho."
    ),
    "sentenca": (
        "Sentença é a decisão do juiz que resolve o processo, dizendo quem tem razão e o que deve ser feito. "
        "Quem não concorda com ela ainda pode pedir que outro tribunal reveja, o que se chama recurso."
    ),
    "recurso": (
        "Recurso é o pedido para que um tribunal mais alto reveja a decisão do juiz. É como pedir uma segunda opinião "
        "dentro da própria Justiça."
    ),
    "liminar": (
        "Liminar é uma decisão rápida que o juiz pode dar logo no começo, antes do fim do processo, quando existe "
        "urgência. Ela vale até a decisão final."
    ),
    "honorarios": (
        "Honorários são o pagamento do advogado pelo trabalho dele. A forma de cobrar (valor fixo, porcentagem no fim "
        "ou os dois) é combinada com o escritório antes de começar."
    ),
    "rescisao": (
        "Rescisão é o acerto de contas quando o emprego termina: salário dos últimos dias, férias, décimo terceiro e, "
        "dependendo do tipo de saída, a multa do FGTS e o seguro-desemprego."
    ),
    "fgts": (
        "FGTS é uma poupança que a empresa deposita todo mês em nome do trabalhador com carteira assinada. Ela pode ser "
        "sacada em algumas situações, como na demissão sem justa causa."
    ),
    "justa causa": (
        "Justa causa é quando a empresa demite alegando que o trabalhador cometeu uma falta grave. Nesse caso ele "
        "perde alguns direitos da rescisão, por isso vale a pena um advogado olhar se a justa causa foi correta."
    ),
    "pensao alimenticia": (
        "Pensão alimentícia é o valor que um dos pais paga para ajudar nas despesas do filho, como comida, escola e "
        "saúde. O valor leva em conta o que o filho precisa e o que quem paga consegue pagar."
    ),
    "inventario": (
        "Inventário é o procedimento para dividir os bens de quem faleceu entre os herdeiros. Pode ser feito no cartório, "
        "quando todos concordam, ou na Justiça."
    ),
    "uniao estavel": (
        "União estável é quando o casal vive junto como família, mesmo sem ter casado no papel. Ela garante direitos "
        "parecidos com os do casamento."
    ),
    "danos morais": (
        "Danos morais é a indenização por um sofrimento que passou do limite do aceitável, como ter o nome negativado "
        "sem dever nada ou ser humilhado. O valor depende de cada situação."
    ),
    "cnis": (
        "CNIS é o extrato do INSS que mostra todos os seus vínculos de trabalho e contribuições. Ele é a base para "
        "calcular a aposentadoria, e dá para baixar pelo aplicativo Meu INSS."
    ),
}

DISCLAIMER = "Isso é uma explicação geral, tá? Para o seu caso, quem confirma é o advogado."
