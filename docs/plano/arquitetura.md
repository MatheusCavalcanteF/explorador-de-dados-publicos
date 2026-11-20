[Índice do plano](../plano.md)

## Arquitetura

```mermaid
flowchart LR
    F[("Fontes oficiais<br/>FTP, HTTPS, APIs")] -->|"janela aberta"| P["Pipeline em Python<br/>GitHub Actions"]
    P -->|"contrato de dados<br/>comparação de versões"| A["Agregados<br/>supressão por família<br/>teste de dedução"]
    C["Catálogo<br/>métricas, cruzamentos, regras"] --> A
    R[("Release<br/>Censo 2022")] --> A
    V[("Versão no ar do SIH<br/>a superfície publicada")] -->|"herança e ataque conjunto"| A
    A --> J["JSON por município"]
    A --> Q["Parquet por tema, UF e ano<br/>(do SIH, só a superfície)"]
    A --> X["Manifesto no git<br/>versões das fontes"]
    J --> S[("GitHub Pages")]
    Q --> S
    C --> S
    S --> M["Página do município"]
    S -->|"leitor em JavaScript"| D["Construtor de dashboards"]
    S --> B["Busca em português"]
    B -->|"especificação validada"| D
```

**Pipeline.** Idempotente, com a mesma disciplina do projeto de origem: `.part` até conferir o tamanho, versão da fonte nos metadados, contrato que recusa dado errado. A supressão só é determinística com linhas em ordem fixa, porque os empates são resolvidos pela posição: medido em duas rodadas, as máscaras das 27 tabelas saíram idênticas. O site só é trocado se tudo passar; se algo falhar, a versão anterior continua no ar. Nada sem supressão sai do runner.

**Três caminhos de leitura:**
1. **Página de município:** HTML estático + um JSON. Estimativa para a v1: mediana de 20,6 KB (4,2 KB com gzip).
2. **Construtor:** Parquet por tema, UF e ano, lido por um leitor em JavaScript. Baixa o arquivo inteiro, o que funciona em qualquer host.
3. **Busca:** dicionário de sinônimos e correspondência aproximada, tudo no navegador. Só gera uma especificação, validada no catálogo. A consulta sai de um modelo fixo, e nenhuma consulta livre é gerada.

**Especificação.** Pergunta, gráfico e dashboard usam o mesmo objeto, que também vai para a URL:

```json
{
  "v": 1,
  "paineis": [{
    "tema": "saude",
    "metrica": "internacoes",
    "territorio": { "papel": "residencia", "municipios": ["3548500"] },
    "filtros": { "capitulo_cid": ["10"] },
    "detalhar": [],
    "periodo": { "de": "2025-T1", "ate": "2025-T4" },
    "grafico": "linha"
  }]
}
```

Implementada em 12/11/2026 e ligada à primeira página em 18/11/2026: está em [`web/especificacao.js`](../../web/especificacao.js), usada pela [página de consulta](#página-de-consulta). Até ali ficou em `consulta/`, fora de `web/`, porque o build publica tudo o que está lá (decidido em 12/11/2026).

- **Versão desde a primeira.** `v` vem antes de tudo. Uma pergunta ou um gráfico é um estado de um painel; um dashboard, de até 12.
- **O município tem papel.** `territorio.papel` é `residencia` ou `hospital`, com até 10 municípios por painel. Sem o papel, a resposta da validação é a pergunta, com as duas opções já validadas; não há padrão. A exceção é a métrica que não distingue os dois, como a população: nela o painel vai sem papel, e um painel com papel é recusado, com o mesmo pedido sem ele como sugestão.
- **Filtrar e detalhar.** `filtros` escolhe valores de uma dimensão; `detalhar` mostra todos. As duas listas, mais o município e o tempo, formam o grão. O tempo sai do formato do período: `2025`, `2025-T1` ou `2025-03`.
- **Sem texto livre** (decidido em 12/11/2026). Todo campo é identificador do catálogo, código do IBGE ou período, e campo desconhecido invalida o estado. Os títulos saem da própria especificação: um link não consegue exibir uma frase de quem o montou.
- **Gráfico:** `linha`, `barras`, `tabela` ou `numero`, o valor único em destaque. O `numero` só vale quando o painel resolve para uma célula: um município, um período, nada detalhado e um valor em cada filtro. Fora disso é recusado, com a mesma consulta em tabela como sugestão.
- **Mapa fica para depois da v1** (decidido em 12/11/2026): a superfície não tem margens de UF nem do Brasil, e um mapa pede o país ou o estado inteiro de uma vez.

A validação liga cada painel a **um cruzamento pré-calculado, no grão exato**. O navegador nunca soma células de uma tabela suprimida para chegar a outro grão: somar por cima de células ocultas daria um total errado e silencioso. Internações só por sexo, por exemplo, é recusada, embora a tabela de faixa de idade e sexo exista. Vale o que está publicado: a base precisa ir para o site (`publicacao.site`), e, quando ela declara uma superfície, o cruzamento e a medida precisam estar nela. Se o cruzamento pedido não existe, a resposta é uma recusa com o motivo e até três sugestões, cada uma conferida pela mesma validação antes de sair. O que a validação aceita são os parâmetros de uma consulta de modelo (cruzamento, municípios, filtros, período), nunca um número.

Com o catálogo de 15/11/2026, **a única consulta que valida é a população estimada por município e ano**; o SIH segue com `publicacao.site: false`, e tudo dele é recusado, com a população como sugestão. A população já vem agregada do IBGE e fica numa lista à parte do catálogo, `cruzamentos_da_origem`, que a supressão e o teste de dedução não percorrem. O cruzamento declara os anos que tem e onde a série quebra, e a validação usa os dois: período antes de 2013, ou só com 2022 e 2023, é recusado, com o período mais próximo que tem dado como sugestão. Uma consulta aceita devolve, além dos parâmetros, as **ressalvas** do pedido, sem número: cada quebra de série que o período atravessa (2018 e 2024, a segunda pela troca do Censo 2010 pelo de 2022), os anos sem estimativa dentro dele e a nota de um município num ano. A tela mostra essas ressalvas junto do gráfico, não só em "Fonte e método". O último ano não fica no catálogo, porque é o que a SIDRA tiver: vem da coleta, pela ficha `.json` publicada ao lado do arquivo, que `comCobertura` junta ao catálogo antes de validar. Um período que começa depois do último ano coletado é recusado, com esse ano como sugestão; um que só termina depois dele vale, com a ressalva dos anos sem dado. Sem a ficha, ou com uma ficha que contradiz o catálogo, a consulta é recusada: a validação não supõe até onde a série vai. Com o SIH ligado numa cópia do catálogo, passam as cinco tabelas da superfície, só nas medidas que ela mostra. O próprio exemplo acima seria recusado, porque capítulo por trimestre não está na superfície, com a sugestão do mesmo capítulo por ano.

**Na URL.** O estado vai depois do `#`, que não chega ao servidor, num texto legível e sempre igual para o mesmo estado:

```
v=1&p=m:internacoes;k:saude;t:r,3548500;f:capitulo_cid=10;p:2025-T1,2025-T4;g:linha
```

O limite é de 1.800 caracteres para o estado, que com 200 de endereço dá um link de até 2.000. Medido em 12/11/2026, com 12 painéis: 579 caracteres com 1 município em cada; 1.239 com 5 municípios e 3 capítulos; 1.443 com 10 municípios; e 2.559 no pior painel que o formato aceita (10 municípios, sexo e as 13 faixas de idade), do qual cabem 8. Acima do limite não sai link nenhum, nem truncado: a serialização devolve o tamanho e o limite, para a tela bloquear o painel novo (decidido em 12/11/2026). Link maior que o limite também não é lido.

Link que não abre vira mensagem, não gráfico vazio. Versão antiga sem conversão, versão mais nova que a do site aberto e texto ilegível têm cada um a sua. Painel cujo cruzamento saiu do catálogo ou da superfície volta como recusa, com as sugestões, e os outros painéis do mesmo link continuam.

### Página de consulta

A primeira tela do construtor, feita em 18/11/2026, em `consulta/` (endereço decidido em 18/11/2026). **Não está no ar:** só entra no build com `construtor: true` em `projeto.yaml`, que fica `false` até o OK do dono do projeto; no build local, `construir --construtor` liga sem mudar o arquivo e para no CI. Com a chave desligada, o site sai igual ao de antes, byte a byte e sem exceção (decidido em 20/11/2026): a regra do link fica em `web/serie-link.css`, que o build só acrescenta ao `estilo.css` com a chave ligada. Com a coleta de 18/11/2026, o build da versão anterior e o da nova têm os mesmos 5.610 arquivos, nenhum diferente; um teste compara o build de chave desligada com o gabarito da versão anterior, um SHA-256 por arquivo.

- **O caminho.** O estado vem do endereço. A página leva o catálogo embutido, só com as bases que vão para o site (decidido em 18/11/2026): sem o SIH, um link que pede internações recebe "a métrica não existe no catálogo", com a população como sugestão. A ficha `populacao_por_ano.json` é pedida sem cópia velha e dá a cobertura à validação; sem ela, a tela recusa com a mensagem da validação. O Parquet vem com a data da coleta da ficha no endereço e é lido por [`web/leitor.js`](../../web/leitor.js), o hyparquet 1.32.0 que o CI usa para conferir o site, empacotado por [`ferramentas/empacotar`](../../ferramentas/empacotar/empacotar.mjs). A consulta de modelo escolhe as linhas dos municípios e anos pedidos, sem somar nem calcular.
- **O que a tela mostra.** A placa traz o que foi entendido, em campos que corrigem a consulta: municípios (busca por nome, até 10), período e gráfico. Abaixo: número, linha, colunas ou barras, sempre com a tabela equivalente; as ressalvas da validação junto do gráfico (as quebras de 2018 e 2024, com os documentos do IBGE, os anos sem estimativa e a nota de município), mais a ausência de estimativa de um município num ano, que nunca vira zero; CSV, PNG e cópia do link; e "Fonte e método", com o link exato da SIDRA de cada município. A linha não atravessa quebra nem ano sem dado. Com mais de um município e vários anos, é um gráfico por município, cada um na própria escala.
- **Recusas.** Período fora da série, só com anos sem estimativa, número com mais de uma célula, papel de residência ou hospital, métrica fora do site, código fora da lista do IBGE, ficha ausente, ficha e Parquet de coletas diferentes (o que o cache pode juntar logo depois de uma publicação) e link ilegível, antigo, mais novo ou grande demais: cada um vira mensagem com sugestões que validam, nunca um gráfico vazio. Um link ilegível que ainda tenha códigos de município sugere a série deles.
- **Página de município.** Ganha só um link para a série dela, do primeiro ao último ano com valor no Parquet que foi ao ar. Se a série reprova e sai do site, o link não aparece.
- **Medido no build local de 18/11/2026**, com a coleta real do mesmo dia: página de 8,0 KB; JavaScript da consulta com 49,0 KB em gzip -9 (leitor 19,3, especificação 9,5, tela 20,3), mais 3,0 KB de CSS; a série com 389 KB, ou 241 KB em gzip -9. O gzip do host não foi medido para estes arquivos. No Chrome 157, em 390 e 1280 px, nos dois temas, nenhuma das 12 situações testadas rola na horizontal. Uma amostra de 8 valores mostrados e 1 ausência bateu com os links exatos da SIDRA. Não foi testado em celular de verdade nem em outro navegador.

### Por que leitor em JavaScript, e não motor

Com cruzamentos pré-calculados, a tela só precisa filtrar um arquivo por município e desenhar. Não há junção livre nem agregação nova, porque somar sobre células ocultas é justamente o que não pode acontecer.

**Formato: Parquet sem compressão, com o gzip do host, e leitor sem descompressor.** Medido com a maior tabela testada (residência x trimestre x capítulo, SP):

| | Sem compressão + leitor leve | zstd + leitor com descompressor |
|---|---:|---:|
| Código do leitor, com gzip | 18,6 KB | 94,7 KB |
| Arquivo em transferência, com gzip | 268,6 KB | 259,9 KB |
| Primeira carga: código + arquivo | 287 KB | 355 KB |
| Tempo em 4G lento, CPU 4x | 2,2 s | 2,6 s |
| Pico de memória | 87 a 90 MB | 91 a 95 MB |
| Publicado por ano (15 tabelas, por UF) | 22,9 MB | 11,6 MB |
| Maior arquivo por UF e ano | 488 KB (280 KB com gzip) | 279 KB |
| Igual ao motor | 12 de 12 tabelas inteiras, nos 3 navegadores | 72 de 72 comparações, mais 24 em Firefox e 24 em WebKit |

O preço é ocupar o dobro no site, o que cabe no orçamento. O risco é depender do gzip do host: o GitHub Pages comprimiu `.parquet` nos arquivos testados, e o site deste projeto confirmou em 07/11/2026: o `populacao.parquet` sem compressão, de 225.932 bytes, saiu com 116.787 em gzip nos três navegadores e no teste do CI. Se deixar de comprimir, o formato é um parâmetro do build, e basta voltar ao zstd.

**O motor (DuckDB-WASM) fica fora da v1.** Só volta se uma função concreta provar que precisa dele, por exemplo análise em várias tabelas que o leitor não consiga fazer em tempo razoável. Nesse caso: carregamento sob demanda, só naquela função, vindo do jsDelivr (6,2 a 7,1 MB com brotli, conforme a CDN que atende) em vez do site, porque o arquivo de 35,9 MB passa do limite de 25 MiB por arquivo. O teste de aceitação é o mesmo: dados iguais aos do leitor e tempo medido em 4G lento.

### Hospedagem: GitHub Pages

Limites confirmados na documentação oficial em 07/11/2026: site publicado de até 1 GB, 100 GB por mês de banda (limite flexível), deploy com timeout de 10 minutos, e proibição de uso como hospedagem de negócio. O Cloudflare Pages tem 25 MiB por arquivo e 20.000 arquivos no plano gratuito, sem limite de banda declarado na página.

O que decidiu foi o comportamento medido em navegador real:

| Teste com `Range` | GitHub Pages | Cloudflare Pages |
|---|---|---|
| Chrome 157, Firefox 159, WebKit 27.2 | `206`, bytes crus, tamanho real em `Content-Range` | `200`, arquivo inteiro |
| Amostras | 2 arquivos e 1 Parquet (leitor e motor) nos 3 navegadores | 1 arquivo nos 3 navegadores, mais 3 arquivos `.js` testados fora do navegador, de 4 sites |
| `HEAD` | Devolve o tamanho **comprimido** | Sem `Content-Length` |

O Cloudflare não documenta isso de forma oficial na página de limites; um relato no repositório das ferramentas dele descreve o mesmo. Como o leitor padrão baixa o arquivo inteiro, a escolha não depende mais de `Range`, e outro host continua possível. **Confirmado no deploy próprio em 07/11/2026**, com o Parquet do IBGE, em Chrome 157, Firefox 159 e WebKit 27.2: `206` com bytes crus e `Content-Range` sobre o tamanho real (225.932), também no rodapé do arquivo; `HEAD` com o tamanho do gzip (116.787); e o leitor leu as 5.571 linhas sem divergência da coleta. Fora do navegador há uma diferença: pedindo `Range` com `Accept-Encoding: gzip`, o teste do CI, em Python, recebeu `206` com uma fatia do gzip e `Content-Range` sobre o tamanho comprimido. O Firefox manda `Range` com `gzip, deflate, br, zstd` e recebe bytes crus; o WebKit manda `identity`. Os cabeçalhos de cada pedido estão em [`site_publicado_navegadores.json`](../medicoes/resultados/site_publicado_navegadores.json).

**Cloudflare na frente do GitHub Pages não deve ser usado com `Range`:** em `shell.duckdb.org`, que tem essa montagem, a leitura parcial falha com `No magic bytes found`, porque o tamanho informado é o do gzip em cache.

As medições de 07/11/2026 no site próprio foram feitas no endereço anterior à renomeação, descrita a seguir. O Parquet não mudou de conteúdo, e o CI refez gzip, `Range`, `HEAD` e leitor no endereço novo em 08/11/2026, com o mesmo resultado; nos navegadores, o teste não foi refeito.

### Endereço e buscadores

**Endereço.** O repositório passou de `explorador-dados-publicos` para `explorador-de-dados-publicos` em 08/11/2026, e o site agora mora em https://matheuscavalcantef.github.io/explorador-de-dados-publicos/. O GitHub redireciona os links do repositório, mas não os do site de projeto. Medido às 03:23 UTC, depois da execução 42442355751 do `Publicar`: no endereço antigo, a página inicial, a de Santos e o sitemap responderam `404`, com a página padrão do GitHub; no novo, as três responderam `200`, e um caminho inexistente devolveu a página 404 do próprio site ([`endereco_e_buscadores.json`](../medicoes/resultados/endereco_e_buscadores.json)). Medições e manifestos anteriores guardam o endereço da época.

O endereço do site e o do repositório ficam só em `projeto.yaml`. Link canônico, Open Graph, JSON-LD, sitemap, links do rodapé, a página 404 e a identificação dos pedidos às fontes saem dali, e um teste falha se o endereço aparecer escrito em outro arquivo do pipeline, do site ou dos workflows. A conferência de hospedagem também falha se o deploy publicar em outro endereço.

**O que cada página leva:**
- Link canônico e Open Graph (`og:title`, `og:description`, `og:url`, `og:site_name`, `og:locale`), sem imagem.
- JSON-LD do schema.org, do tipo `Dataset`. A página de município descreve o próprio município (nome, UF e código IBGE em `spatialCoverage`) e tem uma parte por fonte: a DTB, a estimativa e o Censo, cada uma com órgão, citação, licença da base, período de referência, a tabela oficial em `isBasedOn` e o link exato do valor em `distribution`. Município sem dado numa fonte não ganha a parte dela. As páginas de UF e a inicial seguem o mesmo modelo; a inicial também aponta para o Parquet e a lista de municípios.
- A licença sai do catálogo. Nas três bases do IBGE, que não declaram licença, o JSON-LD diz "Licença não declarada na origem", em vez de inventar uma. O catálogo recusa base sem licença, e licença declarada sem endereço.
- O pipeline valida cada bloco antes de gravar (descrição de 50 a 5.000 caracteres, como pede o Google; licença, órgão, fonte oficial, período ISO 8601, link canônico) e não publica um inválido. Nomes com `<`, `>` ou `&` saem escapados dentro do `<script>`.
- A página 404 fica sem link canônico e sem JSON-LD. O host já responde `404`, então `meta robots` não é necessária, e nenhuma página usa.

**Sitemap.** Gerado pelo pipeline em `sitemap.xml`, com as 5.599 páginas: a inicial, as 27 UFs e os 5.571 municípios. O `lastmod` é a data em que a coleta viu pela primeira vez os valores que a página mostra, não a data da execução: toda página é refeita a cada semana, com a data da coleta no rodapé, mas só muda de fato quando um valor muda. Para isso, o manifesto passou a guardar, por fonte, `valores_desde`, e na estimativa também `periodo_desde`. Um município revisado pelo IBGE ganha a data da revisão; os outros, não. Na primeira execução com o registro, todas as páginas ficaram com 08/11/2026, 02:36 UTC, a coleta mais antiga do manifesto. No site publicado, o sitemap tem exatamente as páginas esperadas pela lista de municípios, e o JSON-LD de Santos passou no validador com os mesmos valores do Parquet.

**robots.txt.** O GitHub Pages serve um site de projeto numa subpasta do domínio `matheuscavalcantef.github.io`, e os buscadores só leem `robots.txt` na raiz do domínio, que pertence a um site de usuário, outro repositório. Por isso não há `robots.txt`; o sitemap é enviado direto no Search Console. Decidido em 08/11/2026: o repositório do site de usuário não será criado só para isso, porque o endereço antigo ficou um dia no ar e não foi indexado.

**Google Search Console.** O suporte ao arquivo de verificação está pronto: com o código em `google_search_console`, no `projeto.yaml`, o pipeline grava `google<código>.html` na raiz do site, com o conteúdo que o Google pede. Hoje não há código nem arquivo. A propriedade a cadastrar é a do prefixo de URL do site, e não a do domínio inteiro.

**Peso.** Com o JSON-LD, o site passou de 31,9 MB para 58,1 MB (5.608 arquivos, no build local de 08/11/2026). A página de município foi de 5,6 KB para 10,1 KB de mediana, 3,7 KB deles de JSON-LD. Servida em gzip, a mediana de 25 páginas sorteadas pelo hash do código foi de 3,0 KB; antes, 2,1 KB, noutra amostra de 25. O sitemap tem 817 KB, ou 22,8 KB em gzip.

### Carregamento

**Peso em transferência:** leitor sem descompressor, 18,6 KB com gzip; leitor com descompressor, 94,7 KB; motor, 8,4 MB (8,1 MB do `.wasm` e 241 KB de JavaScript, com 35,9 MB brutos no `.wasm`).

**Dados iguais.** O leitor com zstd devolveu exatamente o mesmo que o motor em 72 de 72 comparações (4 tabelas, 6 municípios, 3 modos), inclusive os 146.671 valores ocultos de uma tabela inteira, e os 24 casos repetidos em Firefox e WebKit também bateram. O leitor leve, no formato escolhido, bateu em 4 tabelas inteiras de SP nos 3 navegadores. No GitHub Pages real, leu um Parquet público inteiro (231.083 linhas) e, por `Range`, o rodapé e as primeiras 1.000 linhas, com o mesmo resultado nos três navegadores.

**O motor não usa `Range` por padrão.** Sem configuração, baixa o arquivo inteiro. Com `forceFullHTTPReads: false`, o GitHub Pages direto entregou **17.934 bytes em vez de 800.249** para a mesma contagem. É mais um motivo para não depender dele.

**Tempos medidos.** Mediana de 3 execuções, sem cache salvo nas linhas de volta. Os arquivos são de residência x trimestre x capítulo, a maior tabela testada.

**Limite da emulação:** nada disso rodou em celular ou no site real. É Chrome em desktop com tela de celular (390 × 844), um servidor local que imita o comportamento medido no GitHub Pages, banda limitada no servidor, e CPU limitada por Job Object do Windows. A limitação de CPU das ferramentas do navegador não alcança workers: a 4x, a thread principal ficou 4,5 vezes mais lenta e o worker não mudou; com Job Object, os dois ficam 3,9 vezes mais lentos a 4x, e 5,9 vezes a 6x. O teto é pessimista quando motor e tela trabalham juntos, porque um celular real tem mais de um núcleo. O antivírus da máquina altera o tráfego do Chrome e não foi desligado. Cada pico de memória inclui cerca de 80 MB da página vazia.

| Cenário | Transferido | Desktop, sem limite | 4G bom (9 Mbps), CPU 4x | 4G lento (1,6 Mbps), CPU 4x | 4G lento, CPU 6x | Pico de memória |
|---|---:|---:|---:|---:|---:|---:|
| JSON do município (Bertioga) | 5,5 KB | 0,15 s | 0,09 s | 0,20 s | 0,20 s | 85 a 88 MB |
| JSON do município (São Paulo) | 7,8 KB | 0,06 s | 0,09 s | 0,21 s | 0,21 s | 86 a 91 MB |
| **Leitor leve + arquivo da UF sem compressão (padrão)** | 287 KB | 0,10 s | 0,56 s | **2,2 s** | **2,2 s** | 87 a 90 MB |
| Leitor com zstd + arquivo da UF | 355 KB | 0,08 s | 0,61 s | 2,6 s | 2,6 s | 91 a 95 MB |
| Leitor com zstd, outra cidade no mesmo arquivo | 0 | 0,02 s | 0,03 s | 0,03 s | 0,03 s | 103 a 108 MB |
| Leitor com zstd + leitura parcial, arquivo do Brasil | 430 KB | 0,10 s | 0,77 s | 3,3 s | 3,4 s | 99 MB |
| Leitor com zstd + ranking nacional (arquivo de 0,77 MB) | 864 KB | 0,22 s | 1,6 s | 6,5 s | 7,6 s | 145 a 147 MB |
| Motor, primeira carga, sem consulta | 8,4 MB (3 arquivos, `200`) | 0,6 s | 11,5 s | **54,9 s** | **56,2 s** | 248 a 323 MB |
| Motor, consulta com `Range`, arquivo do Brasil | 108 KB | 0,55 s | 1,6 s | 2,7 s | 3,3 s | 290 a 323 MB |
| Motor, outra cidade, arquivo do Brasil | 98 KB | 0,02 s | 0,41 s | 1,2 s | 1,3 s | 290 a 323 MB |
| Motor, consulta, arquivo da UF inteiro | 260 KB | 0,57 s | 1,7 s | 3,2 s | 3,8 s | 293 a 323 MB |
| Motor, volta com cache válido | 0, sem pedidos | não medido | 2,6 s | 3,2 s | 4,4 s | não medido |
| Motor, volta revalidando o cache | 0 (3 arquivos, `304`) | não medido | 3,3 s | 3,3 s | 5,1 s | não medido |

As linhas de consulta do motor somam-se à primeira carga dele. O que isso decide:

- **A página de município não precisa de nada além do JSON.** Os dados chegam em 0,2 s no 4G lento. O número considera só os dados: o JavaScript e os gráficos do site ainda entram na conta.
- **Para o construtor, o leitor resolve.** Em 4G lento, 2,2 s e cerca de 90 MB, contra cerca de 58 s (primeira carga + consulta) e cerca de 295 MB do motor. Mesmo o pior caso medido, um ranking lendo o país inteiro, leva 6,5 s.
- **O gargalo do motor é a rede.** CPU 4x e 6x quase empatam na primeira carga.
- **`Range` não compensa a dependência.** Para o motor, lendo o arquivo do Brasil, poupa 0,5 s em relação a baixar o da UF inteiro; para o leitor, leva mais tempo que o arquivo da UF. Grupos de linhas menores também não ajudam: de 50 mil para 2 mil linhas, o arquivo cresce 27%, e a primeira consulta sobe de 108 KB para 176 KB.

**O cenário de volta foi refeito.** A primeira versão deste teste mostrava 53 s e 8,1 MB na visita repetida. Era defeito do teste, medido assim: no contexto anônimo que o Playwright usa por padrão, o `.wasm` volta com `200` em recarga, em navegação nova e em aba nova, porque esse cache em memória guardou arquivos de até 4 MB e não guardou os de 8 MB ou mais. Com perfil em disco, são 0 bytes nos 4 tipos de navegação, inclusive com o navegador reaberto. O efeito é real para quem usa janela anônima, que baixaria o motor inteiro a cada navegação; o arquivo de dados do leitor fica abaixo desse limite.
