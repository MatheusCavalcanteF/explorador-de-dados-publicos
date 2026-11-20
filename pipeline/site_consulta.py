"""Página de consulta (consulta/): a primeira tela do construtor, e o link de cada página de município para ela.

A página é estática. O estado vem do endereço, depois do "#", e o navegador faz o resto: valida a
especificação no catálogo embutido (web/especificacao.js), junta a cobertura da ficha publicada, lê o Parquet
da série com o leitor (web/leitor.js) e desenha (web/consulta.js). Só entra no build com a chave `construtor`
de projeto.yaml, ou com `construir --construtor` no build local.

O catálogo embutido tem só o que vai para o site: base com `publicacao.site: false` fica de fora, com as
métricas, cruzamentos e dimensões dela. Para a página, uma métrica dessas não existe.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

import pyarrow.parquet as pq

from . import site

CAMINHO = "consulta/"
# Os arquivos de web/ que só a página de consulta usa: com a chave desligada, ficam fora do build.
ARQUIVOS_WEB = ("especificacao.js", "leitor.js", "consulta.js", "consulta.css")
SCRIPTS = ("busca.js", "especificacao.js", "leitor.js", "consulta.js")

CAMPOS_DA_BASE = ("nome", "orgao", "licenca", "publicacao", "superficie", "supressao")
CAMPOS_DA_METRICA = ("tema", "etiquetas", "base", "rotulo", "unidade", "formula", "fontes", "cruzamentos", "fonte", "ressalvas")
CAMPOS_DO_CRUZAMENTO = ("id", "base", "mae", "dimensoes", "tempo", "metricas", "arquivo", "anos", "quebras", "notas")


def _no_site(base: dict) -> bool:
    return (base.get("publicacao") or {}).get("site") is not False


def _bases_da_metrica(catalogo: dict, nome: str, vistas: set | None = None) -> list[str]:
    """Como basesDaMetrica, em especificacao.js: a calculada depende das bases de cada parcela."""
    vistas = set() if vistas is None else vistas
    metrica = catalogo["metricas"].get(nome)
    if metrica is None or nome in vistas:
        return []
    vistas.add(nome)
    if metrica.get("formula"):
        return [b for parcela in metrica.get("fontes") or [] for b in _bases_da_metrica(catalogo, parcela, vistas)]
    return [metrica["base"]]


def catalogo_publicado(catalogo: dict) -> dict:
    """O que a validação no navegador precisa, das bases que vão para o site, e nada mais."""
    bases = {b: {k: v for k, v in x.items() if k in CAMPOS_DA_BASE}
             for b, x in catalogo["bases"].items() if _no_site(x)}
    metricas = {nome: {k: v for k, v in m.items() if k in CAMPOS_DA_METRICA}
                for nome, m in catalogo["metricas"].items()
                if all(b in bases for b in _bases_da_metrica(catalogo, nome))}

    def cruzamento(c: dict) -> dict | None:
        if c.get("base") is not None and c["base"] not in bases:
            return None
        medidas = [m for m in c["metricas"] if m in metricas]
        if not medidas:
            return None
        return {k: v for k, v in c.items() if k in CAMPOS_DO_CRUZAMENTO} | {"metricas": medidas}

    cruzamentos = [x for x in map(cruzamento, catalogo.get("cruzamentos") or []) if x]
    da_origem = [x for x in map(cruzamento, catalogo.get("cruzamentos_da_origem") or []) if x]
    usadas = {d for c in cruzamentos + da_origem for d in c["dimensoes"]}
    dimensoes = {d: x for d, x in (catalogo.get("dimensoes") or {}).items()
                 if d in usadas or x.get("base") in bases}
    publicado = {"versao": catalogo["versao"], "temas": catalogo["temas"], "bases": bases, "metricas": metricas,
                 "dimensoes": dimensoes, "cruzamentos": cruzamentos, "cruzamentos_da_origem": da_origem}
    if "capitulo_cid" in dimensoes and "referencias" in catalogo:
        publicado["referencias"] = {"cid10_capitulos": catalogo["referencias"]["cid10_capitulos"]}
    return publicado


def _serie(catalogo: dict) -> dict | None:
    """O cruzamento da série por município e ano: o que a página de município liga à consulta."""
    return next((c for c in catalogo.get("cruzamentos_da_origem") or []
                 if c.get("anos") and c["tempo"] == "ano" and c["dimensoes"] == ["municipio"]), None)


def estado(metrica: str, codigo: str, de: int, ate: int) -> str:
    """O texto depois do "#", igual ao que serializar() de especificacao.js dá para o mesmo painel.

    Um ano só vira o número em destaque; mais de um, a linha. O teste da página confere a ida e a volta no
    JavaScript, para os dois lados não divergirem.
    """
    periodo, grafico = (str(de), "numero") if de == ate else (f"{de},{ate}", "linha")
    return f"v=1&p=m:{metrica};t:n,{codigo};p:{periodo};g:{grafico}"


def links_da_serie(saida: Path, catalogo: dict) -> dict[str, dict]:
    """Por município, o estado da consulta da série dele, do primeiro ao último ano com valor, lido do Parquet
    que está no site. Sem a série no site (reprovou e saiu), ou município sem nenhum valor: sem link."""
    cruzamento = _serie(catalogo)
    if cruzamento is None or not (Path(saida) / cruzamento["arquivo"]).exists():
        return {}
    tabela = pq.read_table(Path(saida) / cruzamento["arquivo"], columns=["codigo", "ano", "populacao"]).to_pydict()
    anos: dict[str, list[int]] = {}
    for codigo, ano, valor in zip(tabela["codigo"], tabela["ano"], tabela["populacao"]):
        if valor is not None:
            anos.setdefault(codigo, []).append(ano)
    metrica = cruzamento["metricas"][0]
    return {codigo: {"de": min(lista), "ate": max(lista), "estado": estado(metrica, codigo, min(lista), max(lista))}
            for codigo, lista in anos.items()}


def link_do_municipio(serie: dict) -> str:
    """O parágrafo que a página de município ganha. É a única mudança nela."""
    texto = (f"População estimada ano a ano, de {serie['de']} a {serie['ate']}" if serie["de"] != serie["ate"]
             else f"População estimada em {serie['de']}")
    return (f'<p class="serie-link"><a href="../../{CAMINHO}#{escape(serie["estado"])}">{escape(texto)}'
            f'{site.SETA}</a></p>')


def _json_no_html(valor) -> str:
    """JSON dentro de <script>: "</" e "<!--" não podem aparecer como texto, ou o navegador fecha o elemento.
    Todo "<" vai como \\u003c, que o JSON lê como o mesmo caractere."""
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"), sort_keys=True).replace("<", "\\u003c")


def rodape(coleta: dict) -> str:
    """O rodapé das outras páginas diz que a fonte fica logo abaixo de cada número; aqui ela fica em "Fonte e
    método", abaixo da tabela, com a coleta de cada ano. A data do rodapé é a da lista de municípios."""
    d = coleta["fontes"]["dtb"]
    return (f"Municípios da Divisão Territorial Brasileira {d['periodo']} do IBGE, com data-base em {site.data(d['data_base'])}, "
            f"coletada em {site.data_hora(d['coletado_em'])}. A fonte, o método e a data da coleta de cada consulta "
            f"ficam em “Fonte e método”, abaixo da tabela.")


def pagina(catalogo: dict, coleta: dict) -> str:
    publicado = catalogo_publicado(catalogo)
    cruzamento = _serie(publicado)
    rotulo = publicado["metricas"][cruzamento["metricas"][0]]["rotulo"] if cruzamento else "Consulta"
    descricao = (f"{rotulo} pelo IBGE, por município e ano, em gráfico e tabela, com fonte, período, data da coleta "
                 "e as ressalvas de cada consulta. O estado fica no endereço, para compartilhar.")
    cabeca = "".join(f'\n<script src="../{nome}" defer></script>' for nome in SCRIPTS)
    cabeca += '\n<link rel="stylesheet" href="../consulta.css">'
    corpo = f"""<header class="placa placa--consulta" id="conteudo">
<div class="placa__face">
<nav class="trilha" aria-label="Trilha"><a href="../">Brasil</a></nav>
<h1 class="placa__nome" id="titulo">{escape(rotulo)}</h1>
<p class="placa__lead" id="lead">Por município e ano, em gráfico e tabela, com a fonte e as ressalvas de cada número.</p>
<div id="entendido"></div>
</div>
</header>
<main class="coluna" id="paineis">
<noscript><p class="nota">A consulta monta o gráfico no navegador e precisa de JavaScript. A população estimada de cada município está na página dele, a partir da <a href="../">página inicial</a>.</p></noscript>
</main>
<p class="sr-only" id="anuncio" aria-live="polite"></p>
<script type="application/json" id="catalogo">{_json_no_html(publicado)}</script>"""
    return site.pagina(f"Consulta · {site.NOME}", descricao, "../", corpo, rodape(coleta),
                       endereco=CAMINHO, cabeca_extra=cabeca)
