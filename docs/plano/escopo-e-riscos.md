[Índice do plano](../plano.md)

## Escopo da v1

**Entra (F1 a F3):**
- Página para cada um dos 5.571 municípios da lista do IBGE, com busca por nome.
- SIH (2021 em diante), IBGE (população e PIB), Censo Escolar e IDEB.
- Construtor de dashboards sobre cruzamentos pré-calculados, links compartilháveis, exportação para CSV e PNG.
- Filtro por tema, com só os temas que têm dado publicado. O tema entra na especificação e na URL.
- **Painel "Fonte e método" em todo número, gráfico e dashboard:** órgão e pesquisa, tipo de acesso e link (ou receita), período de referência, arquivos usados com hash e data da coleta, fórmula e parcelas quando a métrica é calculada, a regra de supressão (sem o motivo de cada célula, que não é publicado) e as ressalvas. Num dashboard, o painel junta as fontes de todos os gráficos.
- **Exportação que leva a fonte junto:** o CSV tem colunas de fonte, link ou receita, data da coleta e ressalva em cada linha; o PNG traz no rodapé a fonte e o link do estado que o gerou.
- Busca em português com avaliação publicada.
- Arquivos abertos para download, com caminhos estáveis.
- Tema claro e escuro, tabela equivalente a cada gráfico, uso bom no celular.

**Não entra:**
- CAGED, Siconfi e cidades parecidas (próximas fases).
- Diagnóstico por categoria CID de 3 dígitos, procedimento por município e dados por estabelecimento.
- Óbito por mês ou por categoria no município.
- Margens de UF ou Brasil por categoria, até passarem no teste de dedução.
- Consulta livre, qualquer dado individual, login ou servidor.

## Riscos

| Risco | Efeito | O que fazer |
|---|---|---|
| **FTPs sem TLS nem hash** | Não dá para provar que o arquivo é o publicado | Validar a estrutura, comparar versões, registrar tamanho e data da origem |
| **Fontes que recusam o runner** | Medido em 07/11/2026, em 3 execuções do workflow `Alcance das fontes`. A API de serviços do IBGE (`servicodados.ibge.gov.br`: localidades e períodos da SIDRA) não aceitou conexão TCP em nenhuma, nem pelo curl; em 20/11/2026, respondeu nas 2 execuções. O INEP aceitou a conexão na primeira e respondeu com reset no TLS; nas outras duas recusou a conexão, também pelo curl, com e sem a chave híbrida no ClientHello. SIDRA, FTP do IBGE, geoftp do IBGE, FTP do DATASUS (um `RD` baixado, com o mesmo SHA-256 da máquina local), FTP do PDET e Siconfi responderam nas 3. Da máquina local, a API do IBGE respondeu nas 3 rodadas; o INEP falhou no TLS nas 3 rodadas completas e respondeu em 5 de 5 tentativas isoladas. A hipótese de bloqueio por excesso de pedidos foi testada de 08 a 14/11/2026 pelo `INEP diário`, um pedido por dia sem nova tentativa, e os 7 falharam no TLS. **Revisto em 20/11/2026, com captura de pacotes:** não é bloqueio do runner. A primeira conexão TLS depois de um tempo sem pedidos leva reset de um equipamento na frente do servidor (pelo TTL, os pacotes dele saem com 64, e os do servidor, com 255), na máquina local e no runner; a seguinte fecha. Em 2 execuções, num runner do Azure e num da AWS, a nova tentativa passou, e os 6 arquivos baixados no runner têm o mesmo SHA-256 dos baixados daqui. O que ainda falha no runner é a cadeia: o servidor não manda o certificado intermediário, e o Python e o curl do Linux a recusam. As recusas de TCP de 07/11 não se repetiram e não foram explicadas. Nenhuma outra origem oficial guarda os mesmos arquivos. Ver [INEP no runner](ci-e-historico.md#inep-no-runner) | Nada da coleta depende do `servicodados`: a lista vem da DTB do geoftp, e a população e o período mais recente, da `apisidra`. Sem a data de modificação, a mudança é detectada pelo hash dos valores. O INEP fica fora do alcance obrigatório do `Publicar` até a F1c. A rota da F1c, decidida em 20/11/2026, é HTTPS no runner, com nova tentativa e a cadeia completada pelo intermediário da RNP versionado; medida no runner no mesmo dia, em [INEP no runner](ci-e-historico.md#inep-no-runner). O `Alcance das fontes` só pede ao INEP com `inep` ou `inep_tls` marcado no disparo |
| **Agendamento sem hora garantida** | Medido em 8 execuções agendadas, de 08 a 15/11/2026: começaram de 4 h 30 min a 9 h 05 min depois da hora marcada. O `Publicar` de 15/11, marcado para 00:47 UTC, começou às 07:13 UTC | Nada depende do horário em que um workflow agendado roda, nem da ordem entre dois agendados. O que precisa de ordem fica em jobs encadeados do mesmo workflow |
| **Agendamento desativado** | 60 dias sem atividade param a atualização, e a documentação não define "atividade" | Manifesto mensal no git, idade dos dados visível na página, verificação na F1. O commit que altera o `cron` reativa |
| **Fonte republica e piora** | Santo André caiu 64,9% em uma versão | Trava de dois níveis: marca o município, só para tudo em falha estrutural. Limite a reavaliar com mais versões |
| **Dedução entre tabelas** | Sem família, 113 contagens eram dedutíveis. Na F1b, com todas as somas e o motivo da célula público, 4.365 em 2025 | Teste em cadeia sobre os 14 cruzamentos e as margens, em 6 anos, com reforço até zero. Motivo por célula fora do site (decidido). Reprovou, não publica no ano, e a página diz por quê. CAGED e educação entram no teste quando entrarem no site |
| **Dedução entre versões** | Medido na F1b: de 62 a 194.045 diferenças de 1 a 4 por atualização, conforme o par e o ano | Decidido: preliminares em múltiplo de 5, e a versão nova herda o padrão dos períodos fechados. Zero nos 8 pares, nas transições de fechamento e em três versões seguidas. Ataque conjunto com a versão no ar obrigatório antes de publicar: é etapa do pipeline desde 10/11/2026, sobre a superfície das duas versões, com dois motores (intervalos e programação linear) que precisam dar zero, e o ano que não passa não vira partição. Falta o passo que leva a superfície ao site |
| **Comportamento do host muda** | O `HEAD` já informa tamanho comprimido, `Range` depende de detalhes não documentados, e o formato escolhido conta com o gzip do host. Pedido com `Range` e `Accept-Encoding: gzip` recebe uma fatia do gzip, medido em Python | Leitor padrão baixa o arquivo inteiro. Teste semanal no site publicado, em Python e Node, no CI; nos navegadores, o teste é manual. Se o gzip sumir, o build volta ao zstd |
| **Site e TabNet em eixos diferentes** | O site usa a data da alta, e o TabNet, a de internação. Quem comparar um mês vai ver números diferentes e pode achar que é erro | Ressalva junto ao número, explicando a diferença. Conferência pública por competência e por data de internação |
| **Mês da alta sem conferência oficial** | Nenhuma ferramenta oficial testada oferece esse eixo | Conferir as mesmas AIHs pelos outros dois eixos e testar a data da alta no pipeline. Fica registrado como limite |
| **Leitor sem motor de consulta** | Filtrar, ordenar e juntar com população viram código do projeto | Só filtro e desenho sobre cruzamentos prontos; teste de igualdade com o motor no CI, usando-o só como referência |
| **Celular real e iOS sem teste** | Os números são de emulação; WebKit no Windows não é o Safari do iOS | Testar em aparelho antes de lançar a F2 |
| **Licenças ambíguas** | O site do governo declara "SemDerivações" para o conteúdo, sem deixar claro se vale para dados; Siconfi é ODbL | Atribuição em cada número, licença por base, ODbL isolada. Rever antes de publicar os dados para download |
| **Leitura errada** | Mortalidade alta em hospital de referência, só SUS, residência x hospital | Ressalva no catálogo, exibida junto ao número |
| **Limite de banda** | 100 GB por mês | A carga típica tem poucos KB. Com o motor fora, o risco caiu; o maior arquivo por UF transfere 280 KB |
| **Disponibilidade das fontes** | Numa bateria, um endpoint do Siconfi teve 4 de 10 pedidos em timeout. Quedas de conexão vistas no `curl` para o INEP não se repetiram em 20 pedidos em Python, mas o primeiro pedido da coleta de `fontes.json` levou reset, e os outros 11 responderam. Medido em 20/11/2026: o reset do primeiro pedido vem de um equipamento na frente do INEP, e acontece também no runner; ver [INEP no runner](ci-e-historico.md#inep-no-runner) | Novas tentativas; o Censo fica guardado na Release, e o SIH publicado fica na superfície no ar. Reavaliar no runner |
| **Avaliação da busca ruim** | Taxa de resposta errada alta invalida a função | Publicar o número mesmo assim, e recusar mais em vez de errar |
