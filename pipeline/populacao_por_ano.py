"""População estimada por município e ano: a série da tabela 6579 da SIDRA, do primeiro ano do catálogo em diante.

Os anos que a tabela tem saem dela mesma, num pedido no nível Brasil, que também traz o total do país em
cada ano. Depois, cada ano é um pedido com todos os municípios, com a própria URL, o próprio hash e a
própria data de coleta. Nada é suprimido: é um agregado oficial que o IBGE já publica, e o motivo fica no
catálogo (bases.ibge_estimativa.supressao).

O contrato para a coleta quando:
- a tabela ganha um ano que o catálogo declara sem dado, ou perde um que ele espera;
- o último ano da tabela não é o da estimativa que a página mostra, ou os valores dos dois diferem;
- um valor não é contagem positiva nem o sinal de ausência "...", ou um município está fora da DTB;
- a soma dos municípios difere do total do Brasil da própria tabela naquele ano;
- uma passagem entre dois anos muda de patamar sem estar na lista de quebras do catálogo.

A série tem quebras: o valor de depois não se compara com o de antes. Elas ficam no catálogo, com a ressalva
que a tela mostra, e cada linha do arquivo publicado diz desde que ano o valor é comparável.
"""
from __future__ import annotations

from . import ibge
from .contrato import ErroDeContrato, sha256_valores

CRUZAMENTO = "ibge_pop_ano"
VARIAVEL = ibge.ESTIMATIVA["variavel"]
URL_PERIODOS = f"https://apisidra.ibge.gov.br/values/t/{ibge.ESTIMATIVA['tabela']}/n1/all/v/{VARIAVEL}/p/all"
URL_POR_ANO = ibge.URL_SIDRA.format(tabela=ibge.ESTIMATIVA["tabela"], variavel=VARIAVEL, periodo="{ano}")
# "Valor não disponível" na SIDRA: o município ainda não existia, ou ainda não tinha estimativa.
SINAL_DE_AUSENCIA = "..."
ONDE = "População por ano"


def regra(catalogo: dict) -> dict:
    """O cruzamento do catálogo que descreve a série: anos, quebras e o limite da quebra não declarada."""
    return next(c for c in catalogo["cruzamentos_da_origem"] if c["id"] == CRUZAMENTO)


def ler_periodos(corpo: bytes, onde: str = ONDE) -> dict[int, int]:
    """Os anos que a tabela tem, com o total do Brasil em cada um. Colunas achadas pelo cabeçalho."""
    dados = ibge._json(corpo, onde)
    if not isinstance(dados, list) or len(dados) < 2 or not isinstance(dados[0], dict):
        raise ErroDeContrato(f"{onde}: lista de períodos sem cabeçalho ou sem linhas")
    cabecalho, *linhas = dados
    colunas = {nome: chave for chave, rotulo in cabecalho.items() if len(chave) == 3 and chave[0] == "D" and chave[2] == "C"
               for nome, prefixo in (("brasil", "Brasil"), ("variavel", "Variável"), ("periodo", "Ano"))
               if str(rotulo).startswith(prefixo)}
    if set(colunas) != {"brasil", "variavel", "periodo"} or cabecalho.get("V") != "Valor":
        raise ErroDeContrato(f"{onde}: cabeçalho inesperado na lista de períodos {sorted(cabecalho)}")
    totais: dict[int, int] = {}
    for linha in linhas:
        periodo, valor = str(linha.get(colunas["periodo"])), str(linha.get("V"))
        if linha.get("NC") != "1" or linha.get(colunas["variavel"]) != str(VARIAVEL):
            raise ErroDeContrato(f"{onde}: linha de outro nível ou variável na lista de períodos ({periodo})")
        if not (len(periodo) == 4 and periodo.isdigit()) or int(periodo) in totais:
            raise ErroDeContrato(f"{onde}: período {periodo!r} não é um ano, ou veio repetido")
        if not valor.isdigit() or int(valor) <= 0:
            raise ErroDeContrato(f"{onde}: total do Brasil {valor!r} em {periodo} não é contagem positiva")
        totais[int(periodo)] = int(valor)
    return totais


def conferir_anos(na_tabela: list[int], anos: dict, periodo_da_estimativa: str) -> list[int]:
    """Os anos a coletar: do primeiro do catálogo ao último da tabela, menos os declarados sem dado.

    O catálogo e a tabela precisam concordar, porque a validação das consultas lê o catálogo: um ano a mais
    ou a menos na tabela para a coleta, com o que mudar no catálogo.
    """
    primeiro, sem_dado = anos["primeiro"], set(anos.get("sem_dado") or [])
    onde = f"{ONDE}, cruzamento {CRUZAMENTO} do catálogo"
    if primeiro not in na_tabela:
        raise ErroDeContrato(f"{onde}: a tabela não tem {primeiro}, o primeiro ano da série (anos.primeiro)")
    ultimo = max(na_tabela)
    if str(ultimo) != periodo_da_estimativa:
        raise ErroDeContrato(f"{onde}: o último ano da tabela é {ultimo}, e a estimativa que a página mostra é de "
                             f"{periodo_da_estimativa}. As duas respostas da SIDRA se contradizem; nada foi publicado.")
    presentes = {a for a in na_tabela if a >= primeiro}
    apareceram = sorted(presentes & sem_dado)
    if apareceram:
        raise ErroDeContrato(
            f"{onde}: a tabela passou a ter {apareceram}, que o catálogo declara sem dado. Nada foi publicado. "
            f"Tire o ano de anos.sem_dado e veja se ele pede uma quebra nova antes de publicar.")
    esperados = [a for a in range(primeiro, ultimo + 1) if a not in sem_dado]
    faltam = sorted(set(esperados) - presentes)
    if faltam:
        raise ErroDeContrato(
            f"{onde}: a tabela não tem {faltam}, que o catálogo espera. Nada foi publicado. Se o IBGE não publicou "
            f"estimativa nesse ano, declare-o em anos.sem_dado, com a ressalva.")
    return esperados


def comparavel_desde(quebras: list[dict], ano: int) -> int | None:
    """O início do trecho da série em que o ano está: a última quebra até ele. Antes da primeira, None."""
    inicios = [q["a_partir_de"] for q in quebras if q["a_partir_de"] <= ano]
    return max(inicios) if inicios else None


def conferir_quebras(valores: dict[int, dict[str, int]], regra_da_serie: dict) -> dict[str, dict]:
    """Quantos municípios mudam mais do que o limite, ao ano, em cada passagem. Passagem fora da lista de
    quebras que passe do máximo para a coleta: a tela não teria a ressalva."""
    limite = regra_da_serie["quebra_nao_declarada"]
    declaradas = {q["a_partir_de"] for q in regra_da_serie.get("quebras") or []}
    anos, passagens = sorted(valores), {}
    for antes, depois in zip(anos, anos[1:]):
        distancia = depois - antes
        mudaram = sum(1 for codigo, valor in valores[depois].items() if codigo in valores[antes]
                      and abs((valor / valores[antes][codigo]) ** (1 / distancia) - 1) * 100 > limite["variacao_anual_pct"])
        passagens[f"{antes}-{depois}"] = {"municipios_acima_do_limite": mudaram, "quebra_declarada": depois in declaradas}
        if depois not in declaradas and mudaram > limite["municipios_max"]:
            raise ErroDeContrato(
                f"{ONDE}: de {antes} para {depois}, {mudaram} municípios mudam mais de {limite['variacao_anual_pct']}% ao "
                f"ano, acima do máximo de {limite['municipios_max']} de uma passagem comum. Parece uma quebra de série "
                f"que o catálogo não declara. Nada foi publicado: confira na SIDRA e, se for quebra, declare-a em "
                f"cruzamentos_da_origem.{CRUZAMENTO}.quebras, com a ressalva.")
    return passagens


def coletar(regra_da_serie: dict, codigos: set[str], lista: str, periodo_da_estimativa: str,
            estimativa: dict[str, int], anterior: dict | None, pedir, coletado_em: str) -> dict:
    """A série inteira, com um registro por ano: URL, data da coleta, hash da resposta e dos valores, desde
    quando a coleta vê aqueles valores e o que mudou em relação à coleta anterior (`anterior`, do manifesto)."""
    resposta = ibge.baixar(URL_PERIODOS, pedir, ONDE)
    totais = ler_periodos(resposta.corpo)
    anos = conferir_anos(sorted(totais), regra_da_serie["anos"], periodo_da_estimativa)
    quebras = regra_da_serie.get("quebras") or []
    antes = (anterior or {}).get("anos") or {}
    valores: dict[int, dict[str, int]] = {}
    por_ano: dict[str, dict] = {}
    for ano in anos:
        onde, url = f"{ONDE}, {ano}", URL_POR_ANO.format(ano=ano)
        r = ibge.baixar(url, pedir, onde)
        periodo, numeros, _ = ibge.ler_sidra_com_ausencia(r.corpo, VARIAVEL, onde, (SINAL_DE_AUSENCIA,))
        if periodo != str(ano):
            raise ErroDeContrato(f"{onde}: a resposta é do período {periodo}")
        ausentes = ibge.conferir_cobertura(codigos, numeros, onde, lista)
        soma = sum(numeros.values())
        if soma != totais[ano]:
            raise ErroDeContrato(f"{onde}: a soma dos municípios ({soma}) difere do total do Brasil da própria tabela "
                                 f"({totais[ano]}). Nada foi publicado.")
        valores[ano] = numeros
        hash_valores, velho = sha256_valores(numeros), antes.get(str(ano)) or {}
        if not velho.get("sha256_valores"):
            relacao = "sem_coleta_anterior"
        else:
            relacao = "igual" if velho["sha256_valores"] == hash_valores else "valores_mudaram"
        por_ano[str(ano)] = ibge.origem(url, r, coletado_em) | {
            "sha256_valores": hash_valores, "municipios": len(numeros), "ausentes": ausentes, "soma": soma,
            "comparavel_desde": comparavel_desde(quebras, ano) or anos[0],
            "em_relacao_a_coleta_anterior": relacao,
            "valores_desde": (velho.get("valores_desde") or velho["coletado_em"]) if relacao == "igual" else coletado_em}
    if valores[anos[-1]] != estimativa:
        raise ErroDeContrato(f"{ONDE}, {anos[-1]}: os valores da série diferem da estimativa que a página mostra, pedida "
                             f"minutos antes. A SIDRA pode estar no meio de uma atualização; nada foi publicado.")
    return {"base": regra_da_serie["base"], "cruzamento": CRUZAMENTO, "coletado_em": coletado_em,
            "periodos": ibge.origem(URL_PERIODOS, resposta, coletado_em) | {"na_tabela": sorted(totais)},
            "anos": por_ano, "passagens": conferir_quebras(valores, regra_da_serie),
            "valores": {str(ano): valores[ano] for ano in anos}}
