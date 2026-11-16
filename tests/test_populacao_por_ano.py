"""População por município e ano: contrato da série, um registro de coleta por ano e o arquivo do site.

As listas são pequenas (30 municípios): o contrato não depende do tamanho, e 5.571 linhas por ano pesariam."""
import copy
import json
from datetime import datetime, timezone

import pyarrow.parquet as pq
import pytest

from pipeline import catalogo, ibge, manifesto, populacao_por_ano as serie, site
from pipeline.contrato import ErroDeContrato, sha256_valores
from conftest import FonteFalsa, codigos, resposta, sidra
from test_site import coleta as coleta_do_site

AGORA = "2026-11-15T10:00:00Z"
LISTA = sorted(codigos(30))
NOVO = LISTA[-1]   # município criado depois: sem estimativa até 2024
ANOS = [2019, 2020, 2021, 2024, 2025, 2026]
REGRA = {"id": "ibge_pop_ano", "base": "ibge_estimativa",
         "anos": {"primeiro": 2019, "sem_dado": [2022, 2023], "sem_dado_ressalva": "sem estimativa"},
         "quebras": [{"a_partir_de": 2024, "ressalva": "troca de base"}],
         "quebra_nao_declarada": {"variacao_anual_pct": 5, "municipios_max": 3}}


def valores_padrao():
    """Cresce 1% ao ano, cai 20% na quebra declarada de 2024, e o município novo só aparece em 2025."""
    saida = {}
    for ano in ANOS:
        fator = 1.01 ** (ano - 2019) * (0.8 if ano >= 2024 else 1)
        saida[ano] = {c: int((10_000 + 100 * i) * fator) for i, c in enumerate(LISTA) if c != NOVO or ano >= 2025}
    return saida


def periodos(totais, variavel=9324, nivel="1"):
    cabecalho = {"NC": "Nível Territorial (Código)", "NN": "Nível Territorial", "MC": "Unidade de Medida (Código)",
                 "MN": "Unidade de Medida", "V": "Valor", "D1C": "Brasil (Código)", "D1N": "Brasil",
                 "D2C": "Variável (Código)", "D2N": "Variável", "D3C": "Ano (Código)", "D3N": "Ano"}
    return [cabecalho] + [{"NC": nivel, "NN": "Brasil", "MC": "45", "MN": "Pessoas", "V": str(total), "D1C": "1",
                           "D1N": "Brasil", "D2C": str(variavel), "D2N": "x", "D3C": str(ano), "D3N": str(ano)}
                          for ano, total in totais.items()]


def fonte(valores=None, totais=None, sinal="...", extras=None):
    """Respostas da SIDRA para a série: a lista de períodos e um pedido por ano, com o sinal de ausência
    para quem está na lista e não tem valor."""
    valores = valores_padrao() if valores is None else valores
    totais = {ano: sum(v.values()) for ano, v in valores.items()} if totais is None else totais
    urls = {serie.URL_PERIODOS: resposta(periodos({2001: 5, 2012: 7, **totais}))}
    for ano, numeros in valores.items():
        linhas = {c: numeros.get(c, sinal) for c in LISTA} | {c: v for c, v in numeros.items() if c not in LISTA}
        urls[serie.URL_POR_ANO.format(ano=ano)] = resposta(sidra(linhas, 9324, str(ano)))
    return FonteFalsa(urls | (extras or {}))


def coletar(falsa=None, valores=None, anterior=None, regra=REGRA, quando=AGORA, periodo="2026", estimativa=None):
    valores = valores_padrao() if valores is None else valores
    return serie.coletar(regra, set(LISTA), "DTB 2025", periodo, valores[2026] if estimativa is None else estimativa,
                         anterior, falsa or fonte(valores), quando)


def test_cada_ano_tem_a_propria_fonte_data_de_coleta_e_hash():
    falsa = fonte()
    r = coletar(falsa)
    assert list(r["anos"]) == ["2019", "2020", "2021", "2024", "2025", "2026"]
    assert [url for url, _ in falsa.pedidos] == [serie.URL_PERIODOS] + [serie.URL_POR_ANO.format(ano=a) for a in ANOS]
    assert r["periodos"]["na_tabela"] == [2001, 2012, *ANOS] and r["periodos"]["url"] == serie.URL_PERIODOS
    for ano in ANOS:
        item, numeros = r["anos"][str(ano)], valores_padrao()[ano]
        assert item["url"] == f"https://apisidra.ibge.gov.br/values/t/6579/n6/all/v/9324/p/{ano}"
        assert item["coletado_em"] == item["valores_desde"] == AGORA and len(item["sha256"]) == 64 and item["bytes"] > 0
        assert item["sha256_valores"] == sha256_valores(numeros) and item["soma"] == sum(numeros.values())
        assert item["em_relacao_a_coleta_anterior"] == "sem_coleta_anterior"
        assert r["valores"][str(ano)] == numeros
    # Sem valor não é zero: o município novo fica na lista de ausentes até ter estimativa.
    assert r["anos"]["2024"]["ausentes"] == [NOVO] and r["anos"]["2024"]["municipios"] == 29
    assert r["anos"]["2025"]["ausentes"] == [] and NOVO not in r["valores"]["2024"]
    # Cada ano diz desde quando é comparável: a quebra declarada começa um trecho novo.
    assert [r["anos"][str(a)]["comparavel_desde"] for a in ANOS] == [2019, 2019, 2019, 2024, 2024, 2024]
    assert r["passagens"]["2021-2024"] == {"municipios_acima_do_limite": 29, "quebra_declarada": True}
    assert r["passagens"]["2019-2020"] == {"municipios_acima_do_limite": 0, "quebra_declarada": False}


def test_segunda_coleta_diz_o_que_mudou_em_cada_ano():
    primeira = coletar()
    anterior = {k: v for k, v in primeira.items() if k != "valores"}
    valores = valores_padrao()
    valores[2020][LISTA[0]] += 1   # o IBGE revisou um ano já coletado
    segunda = coletar(valores=valores, anterior=anterior, quando="2026-11-22T10:00:00Z")
    assert segunda["anos"]["2020"]["em_relacao_a_coleta_anterior"] == "valores_mudaram"
    assert segunda["anos"]["2020"]["valores_desde"] == "2026-11-22T10:00:00Z"
    for ano in ("2019", "2021", "2024", "2025", "2026"):
        assert segunda["anos"][ano]["em_relacao_a_coleta_anterior"] == "igual"
        assert segunda["anos"][ano]["valores_desde"] == AGORA and segunda["anos"][ano]["coletado_em"] == "2026-11-22T10:00:00Z"


def _com(mudar):
    valores = valores_padrao()
    mudar(valores)
    return valores


@pytest.mark.parametrize("preparar, mensagem", [
    # A tabela ganha um ano declarado sem dado, ou perde um esperado: o catálogo precisa mudar antes.
    (lambda: dict(falsa=fonte(_com(lambda v: v.update({2023: dict(v[2021])}))), estimativa=valores_padrao()[2026]),
     r"passou a ter \[2023\], que o catálogo declara sem dado"),
    (lambda: dict(falsa=fonte(_com(lambda v: v.pop(2020))), estimativa=valores_padrao()[2026]),
     r"não tem \[2020\], que o catálogo espera"),
    (lambda: dict(regra=REGRA | {"anos": REGRA["anos"] | {"primeiro": 2018}}), "não tem 2018, o primeiro ano"),
    # O último ano da tabela é o da estimativa da página, com os mesmos valores.
    (lambda: dict(periodo="2027"), "o último ano da tabela é 2026"),
    (lambda: dict(estimativa=valores_padrao()[2026] | {LISTA[0]: 1}), "diferem da estimativa que a página mostra"),
    # Sinal que não é o de ausência, município fora da DTB, soma diferente do total do Brasil.
    (lambda: dict(falsa=fonte(sinal="X")), "não é contagem positiva"),
    (lambda: dict(falsa=fonte(sinal="-")), "não é contagem positiva"),
    (lambda: dict(falsa=fonte(sinal="0")), "não é contagem positiva"),
    (lambda: dict(valores=_com(lambda v: v[2021].update({"9900001": 5}))), "fora da lista da DTB 2025"),
    (lambda: dict(falsa=fonte(totais={a: sum(v.values()) + (1 if a == 2021 else 0) for a, v in valores_padrao().items()})),
     "2021: a soma dos municípios .* difere do total do Brasil"),
    # Resposta de outro ano, de outra variável, ou fora do ar.
    (lambda: dict(falsa=fonte(extras={serie.URL_POR_ANO.format(ano=2020): resposta(sidra(valores_padrao()[2021], 9324, "2021"))})),
     "2020: a resposta é do período 2021"),
    (lambda: dict(falsa=fonte(extras={serie.URL_PERIODOS: resposta(periodos({2019: 1, 2026: 1}, variavel=93))})), "outro nível ou variável"),
    (lambda: dict(falsa=fonte(extras={serie.URL_PERIODOS: resposta(periodos({2019: 1, 2026: 1}, nivel="6"))})), "outro nível ou variável"),
    (lambda: dict(falsa=fonte(extras={serie.URL_PERIODOS: resposta(periodos({2019: "...", 2026: 1}))})), "total do Brasil"),
    (lambda: dict(falsa=fonte(extras={serie.URL_PERIODOS: resposta(b"<html>manutencao</html>")})), "não é JSON"),
    (lambda: dict(falsa=fonte(extras={serie.URL_POR_ANO.format(ano=2024): resposta(b"", status=503)})), "HTTP 503"),
])
def test_contrato_para_a_coleta_com_erro_claro(preparar, mensagem):
    with pytest.raises(ErroDeContrato, match=mensagem):
        coletar(**preparar())


def _degrau(municipios):
    """Os primeiros municípios da lista caem 10% em 2020 e ficam no patamar novo dali em diante."""
    return _com(lambda v: [v[a].update({c: int(v[a][c] * 0.9) for c in LISTA[:municipios]}) for a in ANOS if a >= 2020])


def test_quebra_que_o_catalogo_nao_declara_para_a_coleta():
    # Quatro municípios mudam de patamar de 2019 para 2020: acima do máximo de 3 de uma passagem comum.
    with pytest.raises(ErroDeContrato, match="de 2019 para 2020, 4 municípios mudam mais de 5% ao ano.*quebra de série"):
        coletar(valores=_degrau(4))
    # Três, não: é o que uma passagem comum tem. E a mesma mudança numa quebra declarada passa.
    assert coletar(valores=_degrau(3))["passagens"]["2019-2020"]["municipios_acima_do_limite"] == 3
    declarada = REGRA | {"quebras": [{"a_partir_de": 2020, "ressalva": "x"}, *REGRA["quebras"]]}
    assert coletar(valores=_degrau(4), regra=declarada)["anos"]["2021"]["comparavel_desde"] == 2020


def test_a_mudanca_e_medida_por_ano_de_distancia():
    # De 2021 para 2024 são três anos: 12% no período são 3,8% ao ano, abaixo do limite de 5%.
    sem_quebra = REGRA | {"quebras": []}
    valores = _com(lambda v: [v[a].update({c: int(v[2021][c] * 1.12) for c in LISTA[:29]}) for a in (2024, 2025, 2026)])
    assert coletar(valores=valores, regra=sem_quebra)["passagens"]["2021-2024"]["municipios_acima_do_limite"] == 0


def test_sinal_de_ausencia_so_e_aceito_quando_pedido():
    linhas = json.dumps(sidra({"3548500": 10, "3550308": "..."}, 9324, "2024")).encode()
    assert ibge.ler_sidra_com_ausencia(linhas, 9324, "teste", ("...",)) == ("2024", {"3548500": 10}, ["3550308"])
    with pytest.raises(ErroDeContrato, match="não é contagem positiva"):
        ibge.ler_sidra(linhas, 9324, "teste")
    repetido = sidra({"3548500": "..."}, 9324, "2024") + sidra({"3548500": "..."}, 9324, "2024")[1:]
    with pytest.raises(ErroDeContrato, match="repetido"):
        ibge.ler_sidra_com_ausencia(json.dumps(repetido).encode(), 9324, "teste", ("...",))


def test_catalogo_aponta_para_onde_a_serie_e_coletada():
    cat = catalogo.carregar()
    coleta = cat["bases"]["ibge_estimativa"]["coleta"]
    assert (coleta["periodos"], coleta["por_ano"]) == (serie.URL_PERIODOS, serie.URL_POR_ANO)
    regra = serie.regra(cat)
    assert regra["base"] == "ibge_estimativa" and regra["metricas"] == ["populacao_estimada"]
    assert regra["arquivo"] == "dados/ibge/populacao_por_ano.parquet"
    assert (regra["anos"]["primeiro"], regra["anos"]["sem_dado"]) == (2013, [2022, 2023])
    assert [q["a_partir_de"] for q in regra["quebras"]] == [2018, 2024]
    assert "Censo 2022" in regra["quebras"][1]["ressalva"] and "Não são comparáveis" in regra["quebras"][1]["ressalva"]
    # Sem supressão, com o motivo escrito no catálogo.
    assert cat["bases"]["ibge_estimativa"]["supressao"]["aplica"] is False
    assert "Agregado oficial" in cat["bases"]["ibge_estimativa"]["supressao"]["motivo"]


def test_coleta_do_ibge_traz_a_serie_so_quando_pedida(tmp_path, monkeypatch):
    estimativa = valores_padrao()[2026]
    censo = {c: 900 + i for i, c in enumerate(LISTA[1:])}
    # A DTB só aceita a lista inteira do país; aqui ela entra pronta, com os 30 municípios do teste.
    municipios = [{"codigo": c, "nome": f"Município {c}", "uf": "SP", "uf_nome": "São Paulo"} for c in LISTA]
    monkeypatch.setattr(ibge.dtb, "coletar", lambda pedir, coletado_em: (
        {"base": "ibge_dtb", "periodo": "2025", "data_base": "2025-12-31", "coletado_em": coletado_em,
         "sha256_valores": "d" * 64}, copy.deepcopy(municipios)))
    urls = fonte().respostas | {
        ibge.URL_SIDRA.format(**ibge.ESTIMATIVA): resposta(sidra(estimativa, 9324, "2026")),
        ibge.URL_SIDRA.format(**ibge.CENSO): resposta(sidra(censo, 93, "2022"))}
    agora = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
    sem = ibge.coletar(tmp_path / "sem", pedir=FonteFalsa(urls), agora=agora)
    assert "populacao_por_ano" not in sem
    com = ibge.coletar(tmp_path / "com", pedir=FonteFalsa(urls), agora=agora, serie=REGRA)
    assert com["fontes"] == sem["fontes"] and com["municipios"] == sem["municipios"]
    assert list(com["populacao_por_ano"]["anos"]) == [str(a) for a in ANOS]
    gravado = json.loads((tmp_path / "com" / "ibge.json").read_text(encoding="utf-8"))
    assert gravado["populacao_por_ano"]["valores"]["2026"] == estimativa
    # O manifesto guarda o registro de cada ano, sem os valores; a coleta seguinte compara com ele.
    resumo = manifesto.resumo_ibge(gravado)
    assert "valores" not in resumo["populacao_por_ano"] and resumo["populacao_por_ano"]["anos"]["2024"]["url"].endswith("/p/2024")
    assert "populacao_por_ano" not in manifesto.resumo_ibge(sem)
    de_novo = ibge.coletar(tmp_path / "de_novo", pedir=FonteFalsa(urls), agora=agora, serie=REGRA,
                           anterior_da_serie=resumo["populacao_por_ano"])
    assert {i["em_relacao_a_coleta_anterior"] for i in de_novo["populacao_por_ano"]["anos"].values()} == {"igual"}


# ---------------------------------------------------------------- o arquivo do site

def coleta_com_serie():
    """A coleta de três municípios dos testes do site, com a série no formato que a coleta grava."""
    cat = catalogo.carregar()
    regra, base = serie.regra(cat), coleta_do_site()
    anos = [a for a in range(regra["anos"]["primeiro"], 2027) if a not in regra["anos"]["sem_dado"]]
    valores = {str(a): {m["codigo"]: m["populacao_estimada"] - (2026 - a) * 10 for m in base["municipios"]
                        if m["codigo"] != "5101837" or a >= 2025} for a in anos}
    por_ano = {str(a): {"url": serie.URL_POR_ANO.format(ano=a), "coletado_em": base["coletado_em"], "bytes": 1508611,
                        "sha256": f"{a:064d}", "sha256_valores": sha256_valores(valores[str(a)]),
                        "municipios": len(valores[str(a)]), "ausentes": sorted({"5101837"} - set(valores[str(a)])),
                        "soma": sum(valores[str(a)].values()),
                        "comparavel_desde": serie.comparavel_desde(regra["quebras"], a) or anos[0],
                        "em_relacao_a_coleta_anterior": "igual", "valores_desde": "2026-11-01T10:00:00Z"} for a in anos}
    return base | {"populacao_por_ano": {"base": "ibge_estimativa", "cruzamento": "ibge_pop_ano",
                                        "coletado_em": base["coletado_em"], "anos": por_ano, "valores": valores}}, cat


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("serie")
    coleta, cat = coleta_com_serie()
    site.construir(coleta, cat, pasta / "com", execucao="1")
    site.construir(coleta_do_site(), cat, pasta / "sem", execucao="1")
    return pasta / "com", pasta / "sem", coleta, cat


def test_paginas_nao_mudam_com_a_serie_so_entram_os_dois_arquivos(sites):
    com, sem, _, _ = sites
    arquivos = lambda pasta: {p.relative_to(pasta).as_posix() for p in pasta.rglob("*") if p.is_file()}
    assert arquivos(com) - arquivos(sem) == {"dados/ibge/populacao_por_ano.parquet", "dados/ibge/populacao_por_ano.json"}
    assert arquivos(sem) <= arquivos(com)
    for nome in sorted(arquivos(sem)):
        assert (com / nome).read_bytes() == (sem / nome).read_bytes(), nome
    assert "municipio/3548500/index.html" in arquivos(sem)


def test_parquet_da_serie_tem_uma_linha_por_municipio_e_ano_com_ausencia_nula(sites):
    com, _, coleta, _ = sites
    arquivo = pq.ParquetFile(com / "dados/ibge/populacao_por_ano.parquet")
    assert arquivo.metadata.row_group(0).column(0).compression == "UNCOMPRESSED"
    assert [(c.name, str(c.type)) for c in arquivo.schema_arrow] == [
        ("codigo", "string"), ("ano", "int16"), ("populacao", "int32"), ("comparavel_desde", "int16")]
    linhas = arquivo.read().to_pylist()
    anos = sorted(int(a) for a in coleta["populacao_por_ano"]["anos"])
    assert [(l["codigo"], l["ano"]) for l in linhas] == [(c, a) for c in ("3548500", "4206504", "5101837") for a in anos]
    assert 2022 not in anos and 2023 not in anos
    santos = {l["ano"]: l for l in linhas if l["codigo"] == "3548500"}
    assert santos[2026]["populacao"] == 429528 and santos[2013]["populacao"] == 429528 - 130
    # A quebra vai em cada linha: 2021 e 2024 estão em trechos diferentes.
    assert {a: santos[a]["comparavel_desde"] for a in (2013, 2017, 2018, 2021, 2024, 2026)} == {
        2013: 2013, 2017: 2013, 2018: 2018, 2021: 2018, 2024: 2024, 2026: 2024}
    novo = {l["ano"]: l["populacao"] for l in linhas if l["codigo"] == "5101837"}
    assert novo[2024] is None and novo[2025] == 5983 - 10 and 0 not in novo.values()


def test_ficha_da_serie_tem_fonte_e_coleta_por_ano_quebras_e_o_motivo_de_nao_suprimir(sites):
    com, _, coleta, cat = sites
    ficha = json.loads((com / "dados/ibge/populacao_por_ano.json").read_text(encoding="utf-8"))
    regra = serie.regra(cat)
    assert ficha["arquivo"] == "populacao_por_ano.parquet" and ficha["metrica"] == "populacao_estimada"
    assert ficha["fonte"]["orgao"] == "IBGE" and ficha["fonte"]["tabela"] == "https://sidra.ibge.gov.br/tabela/6579"
    assert list(ficha["anos"]) == list(coleta["populacao_por_ano"]["anos"])
    assert ficha["anos"]["2024"] == {
        "url": "https://apisidra.ibge.gov.br/values/t/6579/n6/all/v/9324/p/2024", "coletado_em": "2026-11-07T23:23:20Z",
        "valores_desde": "2026-11-01T10:00:00Z", "sha256": f"{2024:064d}", "bytes": 1508611,
        "municipios_com_valor": 2, "municipios_sem_valor": 1, "comparavel_desde": 2024}
    assert ficha["quebras"] == [{"a_partir_de": q["a_partir_de"], "ressalva": q["ressalva"]} for q in regra["quebras"]]
    assert [q["a_partir_de"] for q in ficha["quebras"]] == [2018, 2024]
    assert ficha["sem_dado"] == {"anos": [2022, 2023], "ressalva": regra["anos"]["sem_dado_ressalva"]}
    assert ficha["notas"] == regra["notas"]
    assert ficha["supressao"] == {"aplica": False, "motivo": cat["bases"]["ibge_estimativa"]["supressao"]["motivo"]}
    assert set(ficha["colunas"]) == {"codigo", "ano", "populacao", "comparavel_desde"}


# ---------------------------------------------------------------- catálogo

@pytest.mark.parametrize("mudanca, mensagem", [
    (lambda c, x: x.update(id="sih_res_ano"), "id ausente ou repetido"),
    (lambda c, x: x.update(base="sih"), "supressao.aplica false"),
    (lambda c, x: x.update(base="ibge_dtb"), "supressao.aplica false"),
    (lambda c, x: x.update(dimensoes=["municipio_residencia"]), "dimensão fora do catálogo ou do SIH"),
    (lambda c, x: x.update(metricas=["populacao_censo"]), "sair da base"),
    (lambda c, x: x.update(metricas=["internacoes"]), "sair da base"),
    (lambda c, x: x.update(arquivo="../populacao.parquet"), "caminho .parquet relativo"),
    (lambda c, x: x.update(tempo="mes"), "tempo ano"),
    (lambda c, x: x["anos"].pop("primeiro"), "anos.primeiro"),
    (lambda c, x: x["anos"].update(sem_dado=[2023, 2022]), "lista crescente"),
    (lambda c, x: x["anos"].update(sem_dado=[2013]), "lista crescente"),
    (lambda c, x: x["anos"].pop("sem_dado_ressalva"), "precisa da ressalva"),
    (lambda c, x: x["quebras"][1].update(a_partir_de=2022), "cada quebra"),
    (lambda c, x: x["quebras"][1].update(ressalva=" "), "cada quebra"),
    (lambda c, x: x["quebras"].reverse(), "cada quebra"),
    (lambda c, x: x["notas"][0].update(municipios=[5107925]), "cada nota"),
    (lambda c, x: x["notas"][0].update(anos=[2022]), "cada nota"),
    (lambda c, x: x["notas"][0].update(ressalva=""), "cada nota"),
    (lambda c, x: x["quebra_nao_declarada"].update(municipios_max=0), "quebra_nao_declarada"),
    (lambda c, x: c["bases"]["ibge_estimativa"]["supressao"].pop("motivo"), "precisa dizer o motivo"),
    (lambda c, x: c["bases"]["ibge_censo_2022"]["supressao"].update(motivo=""), "precisa dizer o motivo"),
])
def test_cruzamento_da_origem_quebrado_e_recusado(mudanca, mensagem):
    cat = copy.deepcopy(catalogo.carregar())
    mudanca(cat, cat["cruzamentos_da_origem"][0])
    with pytest.raises(catalogo.ErroDeCatalogo, match=mensagem):
        catalogo.validar(cat)
