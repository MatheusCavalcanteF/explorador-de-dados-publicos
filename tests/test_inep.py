"""Coleta do INEP (pipeline/inep.py): regras, contrato dos microdados e do Ideb, cache e linha de comando.

Os arquivos são montados aqui, no formato dos reais (o .zip com a pasta, o CSV em latin-1 e o MD5; a planilha com
título, cabeçalho na linha 10 e notas no fim). A origem é falsa: nenhum teste usa rede.
"""
import copy
import hashlib
import json
import zipfile
from datetime import datetime, timezone

import pytest
from conftest import xlsx_de_teste

from pipeline import inep, inep_cliente
from pipeline.contrato import ErroDeContrato

HOJE = datetime(2026, 11, 20, tzinfo=timezone.utc)
REGRAS = inep.carregar_regras()
CENSO, IDEB = REGRAS["bases"]["inep_censo_escolar"], REGRAS["bases"]["inep_ideb"]
MUNICIPIOS = {"1100015": "RO", "1200013": "AC", "5101837": "MT"}
NOME_DA_ESCOLA = "ESCOLA ESTADUAL SÃO JOÃO"
COLUNAS = ["NU_ANO_CENSO", "NO_ENTIDADE", "CO_ENTIDADE", "CO_MUNICIPIO", "TP_DEPENDENCIA", *CENSO["contagens"]]
LM = "Wed, 08 Jul 2026 12:22:27 GMT"


def escola(ano, entidade, municipio, dependencia="3", **valores):
    linha = {"NU_ANO_CENSO": str(ano), "NO_ENTIDADE": NOME_DA_ESCOLA, "CO_ENTIDADE": entidade, "CO_MUNICIPIO": municipio,
             "TP_DEPENDENCIA": dependencia, **{c: "10" for c in CENSO["contagens"]}, **valores}
    return [linha[c] for c in COLUNAS]


def censo_zip(caminho, ano, linhas=None, cabecalho=COLUNAS, md5=None, pasta=None, nome_csv=None, armazenar=False):
    """O .zip de um ano: pasta com o nome do ano, dados/ com o CSV e o MD5, e um leia-me."""
    if linhas is None:
        linhas = [escola(ano, "11000001", "1100015"), escola(ano, "11000002", "1100015", "2"),
                  escola(ano, "12000001", "1200013", "4", QT_MAT_BAS="")]
    dados = ("\r\n".join(";".join(l) for l in [cabecalho, *linhas]) + "\r\n").encode("latin-1")
    pasta = pasta if pasta is not None else f"microdados_censo_escolar_{ano}/dados/"
    declarado = md5 or hashlib.md5(dados).hexdigest().upper()
    with zipfile.ZipFile(caminho, "w", zipfile.ZIP_STORED if armazenar else zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"microdados_censo_escolar_{ano}/leia-me/Leia-me.pdf", b"%PDF-1.4")
        z.writestr(pasta + (nome_csv or f"microdados_ed_basica_{ano}.csv"), dados)
        z.writestr(pasta + f"md5_microdados_ed_basica_{ano}.txt", f"{declarado} *microdados_ed_basica_{ano}.csv\r\n")
    return caminho


CAB_IDEB = ["SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO", "REDE", "VL_OBSERVADO_2017", "VL_OBSERVADO_2019",
            "VL_OBSERVADO_2021", "VL_OBSERVADO_2023", "VL_PROJECAO_2021"]
DADOS_IDEB = [["RO", 1100015, "Município A", "Estadual", 4.1, 4.5, "-", 5.0, 4.7],
              ["RO", 1100015, "Município A", "Pública", 4.0, 4.4, 4.6, 5.1, 4.6],
              ["AC", 1200013, "Município B", "Municipal", "-", 5.1, 4.9, 5.3, 5.0]]


def ideb_xlsx(caminho, dados=None, cabecalho=CAB_IDEB, linha_do_cabecalho=10, notas=("Fonte: MEC/Inep", "Notas: ND - ...")):
    linhas = {1: [None, "Ministério da Educação"], 4: ["Ensino Médio Regular"],
              7: ["Sigla da UF", "Código do Município"], linha_do_cabecalho: cabecalho}
    numero = linha_do_cabecalho + 1
    for linha in DADOS_IDEB if dados is None else dados:
        linhas[numero], numero = linha, numero + 1
    for i, nota in enumerate(notas):
        linhas[numero + 1 + i] = [nota]
    return xlsx_de_teste(caminho, linhas)


# --- Regras ---------------------------------------------------------------------------------------------------


def test_regras_do_catalogo_seguem_o_plano():
    nomes = [i["nome"] for i in inep.esperados(REGRAS)]
    assert nomes == [f"microdados_censo_escolar_{a}.zip" for a in range(2019, 2025)] + [
        f"divulgacao_{e}_municipios_2023.xlsx" for e in ("anos_iniciais", "anos_finais", "ensino_medio")]
    assert IDEB["anos"] == [2019, 2021, 2023] and IDEB["marcas"] == ["-"]


@pytest.mark.parametrize("estragar, mensagem", [
    (lambda r: r["bases"]["inep_censo_escolar"].update(anos=[2020, 2019]), "crescente"),
    (lambda r: r["bases"]["inep_ideb"].update(edicao=2021), "edicao"),
    (lambda r: r["bases"]["inep_censo_escolar"].update(arquivo="dados_abertos/fixo.zip"), "{ano}"),
    (lambda r: r["bases"]["inep_censo_escolar"].update(total="QT_OUTRA"), "total"),
    (lambda r: r["bases"].update(inep_saeb={}), "as bases são"),
    (lambda r: r.update(host="inep.gov.br"), "host"),
])
def test_regras_que_se_contradizem_param(estragar, mensagem):
    regras = copy.deepcopy(REGRAS)
    estragar(regras)
    with pytest.raises(inep.ErroDeRegras, match=mensagem.replace("{", "\\{").replace("}", "\\}")):
        inep.validar(regras)


# --- Contrato dos microdados ----------------------------------------------------------------------------------


def test_microdados_que_seguem_o_contrato(tmp_path):
    resumo = inep.conferir_censo(censo_zip(tmp_path / "a.zip", 2024), 2024, CENSO, MUNICIPIOS)
    assert resumo == {"csv": "microdados_ed_basica_2024.csv", "bytes_csv": resumo["bytes_csv"],
                      "md5": "confere com o declarado", "colunas": len(COLUNAS), "linhas": 3, "municipios": 2,
                      "municipios_da_dtb_sem_linha": {"total": 1, "codigos": ["5101837"]}, "total_qt_mat_bas": 20}


def test_csv_achado_pelo_nome_em_qualquer_pasta_e_com_extensao_maiuscula(tmp_path):
    arquivo = censo_zip(tmp_path / "a.zip", 2020, pasta="Microdados do Censo Escolar 2020/dados/", nome_csv="microdados_ed_basica_2020.CSV",
                        md5=None)
    assert inep.conferir_censo(arquivo, 2020, CENSO, MUNICIPIOS)["linhas"] == 3


def test_colunas_sao_lidas_pelo_nome_e_nao_pela_posicao(tmp_path):
    ordem = list(reversed(COLUNAS))
    linhas = [list(reversed(escola(2022, "1", "1100015"))), list(reversed(escola(2022, "2", "1200013")))]
    assert inep.conferir_censo(censo_zip(tmp_path / "a.zip", 2022, linhas, ordem), 2022, CENSO, MUNICIPIOS)["municipios"] == 2


@pytest.mark.parametrize("opcoes, mensagem", [
    ({"md5": "0" * 32}, "MD5 de microdados_ed_basica_2024.csv é .* e o .zip declara 0000"),
    ({"nome_csv": "microdados_ed_basica_2023.csv"}, "esperado um microdados_ed_basica_2024.csv .* achados 0 e 1"),
    ({"cabecalho": [c for c in COLUNAS if c != "QT_MAT_MED"]}, "faltam \\['QT_MAT_MED'\\]"),
    ({"cabecalho": COLUNAS[:-1] + ["CO_MUNICIPIO"]}, "se repetem \\['CO_MUNICIPIO'\\]"),
    ({"linhas": []}, "CSV sem linhas de dados"),
])
def test_arquivo_fora_do_formato_para(tmp_path, opcoes, mensagem):
    linhas = opcoes.pop("linhas", None)
    if "cabecalho" in opcoes:
        linhas = [[("2024" if c == "NU_ANO_CENSO" else "1100015" if c == "CO_MUNICIPIO" else "1") for c in opcoes["cabecalho"]]]
    with pytest.raises(ErroDeContrato, match=mensagem):
        inep.conferir_censo(censo_zip(tmp_path / "a.zip", 2024, linhas, **opcoes), 2024, CENSO, MUNICIPIOS)


def test_zip_com_csv_corrompido_ou_que_nao_e_zip(tmp_path):
    arquivo = censo_zip(tmp_path / "a.zip", 2024, armazenar=True)
    dados = bytearray(arquivo.read_bytes())
    posicao = dados.index(b"11000002")
    dados[posicao] = ord("9")
    arquivo.write_bytes(bytes(dados))
    with pytest.raises(ErroDeContrato, match="corrompido dentro do .zip"):
        inep.conferir_censo(arquivo, 2024, CENSO, MUNICIPIOS)
    (tmp_path / "b.zip").write_bytes(b"<html>Em manutencao</html>")
    with pytest.raises(ErroDeContrato, match="não é um .zip"):
        inep.conferir_censo(tmp_path / "b.zip", 2024, CENSO, MUNICIPIOS)


def test_cada_desvio_das_linhas_entra_num_erro_so_sem_valor_da_escola(tmp_path):
    linhas = [escola(2024, "1", "1100015"),
              escola(2023, "2", "1100015"),                         # ano errado
              escola(2024, "3", "9999999"),                         # fora da DTB
              escola(2024, "4", "1200013", "5"),                    # dependência desconhecida
              escola(2024, "5", "1200013", QT_MAT_MED="-37"),       # contagem negativa
              escola(2024, "6", "1200013", QT_DOC_BAS="12,5"),      # contagem que não é inteira
              escola(2024, "1", "1200013"),                         # escola repetida
              escola(2024, "8", "1200013")[:-2]]                    # linha curta
    with pytest.raises(ErroDeContrato) as erro:
        inep.conferir_censo(censo_zip(tmp_path / "a.zip", 2024, linhas), 2024, CENSO, MUNICIPIOS)
    texto = str(erro.value)
    for trecho in ("NU_ANO_CENSO diferente de 2024: 1 (por exemplo, linha 3: '2023')", "CO_MUNICIPIO fora da DTB: 1 (por exemplo, 9999999)",
                   "TP_DEPENDENCIA fora de ['1', '2', '3', '4']: 1 (por exemplo, linha 5: '5')",
                   "QT_MAT_MED que não é inteiro de 0 para cima: 1 (por exemplo, linha 6)",
                   "QT_DOC_BAS que não é inteiro de 0 para cima: 1 (por exemplo, linha 7)",
                   "CO_ENTIDADE repetido: 1 (por exemplo, linha 8)", "linha com outro número de campos: 1 (por exemplo, linha 9: 13 de 15)"):
        assert trecho in texto, trecho
    assert "-37" not in texto and "12,5" not in texto and NOME_DA_ESCOLA not in texto


# --- Contrato do Ideb -----------------------------------------------------------------------------------------


def test_planilha_do_ideb_que_segue_o_contrato(tmp_path):
    resumo = inep.conferir_ideb(ideb_xlsx(tmp_path / "a.xlsx"), "ensino_medio", IDEB, MUNICIPIOS)
    assert resumo == {"etapa": "ensino_medio", "edicao": 2023, "cabecalho_na_linha": 10, "colunas": len(CAB_IDEB),
                      "linhas": 3, "municipios": 2, "redes": {"Estadual": 1, "Municipal": 1, "Pública": 1},
                      "anos": {"2019": {"com_valor": 3}, "2021": {"-": 1, "com_valor": 2}, "2023": {"com_valor": 3}}}


def test_codigo_do_municipio_como_texto_tambem_vale(tmp_path):
    dados = [["RO", "1100015", "Município A", "Estadual", 4.1, 4.5, 4.6, 5.0, 4.7]]
    assert inep.conferir_ideb(ideb_xlsx(tmp_path / "a.xlsx", dados), "x", IDEB, MUNICIPIOS)["municipios"] == 1


@pytest.mark.parametrize("opcoes, mensagem", [
    ({"linha_do_cabecalho": 25}, "cabeçalho com SG_UF não encontrado até a linha 20"),
    ({"cabecalho": [c for c in CAB_IDEB if c != "VL_OBSERVADO_2021"]}, "faltam \\['VL_OBSERVADO_2021'\\]"),
    ({"cabecalho": CAB_IDEB + ["VL_OBSERVADO_2025"]}, "a última coluna VL_OBSERVADO_\\* é de 2025, e a edição esperada é 2023"),
    ({"dados": []}, "planilha sem linhas de dados"),
])
def test_planilha_fora_do_formato_para(tmp_path, opcoes, mensagem):
    with pytest.raises(ErroDeContrato, match=mensagem):
        inep.conferir_ideb(ideb_xlsx(tmp_path / "a.xlsx", **opcoes), "x", IDEB, MUNICIPIOS)


def test_cada_desvio_do_ideb_entra_num_erro_so(tmp_path):
    dados = [["RO", 1100015, "A", "Estadual", 4.1, 4.5, "ND", 5.0, 4.7],      # marca que o catálogo não lista
             ["RO", 1100015, "A", "Pública", 4.0, 11.2, 4.6, 5.1, 4.6],       # fora da escala
             ["AC", 1100015, "A", "Municipal", 4.0, 4.4, 4.6, 5.1, 4.6],      # UF errada
             ["AC", 9999999, "Z", "Municipal", 4.0, 4.4, 4.6, 5.1, 4.6],      # fora da DTB
             ["AC", 1200013, "B", "Privada", 4.0, 4.4, 4.6, 5.1, 4.6],        # rede desconhecida
             ["RO", 1100015, "A", "Estadual", 4.0, 4.4, 4.6, 5.1, 4.6],       # município e rede repetidos
             [None], ["Fonte: MEC/Inep"],
             ["AC", 1200013, "B", "Municipal", 4.0, 4.4, 4.6, 5.1, 4.6]]      # dado depois das notas
    with pytest.raises(ErroDeContrato) as erro:
        inep.conferir_ideb(ideb_xlsx(tmp_path / "a.xlsx", dados, notas=()), "x", IDEB, MUNICIPIOS)
    texto = str(erro.value)
    for trecho in ("VL_OBSERVADO_2021 com marca que o catálogo não lista: 1 (por exemplo, 'ND')",
                   "VL_OBSERVADO_2019 fora de 0 a 10: 1 (por exemplo, linha 12)",
                   "SG_UF diferente da UF do município na DTB: 1 (por exemplo, linha 13: 1100015 em AC)",
                   "CO_MUNICIPIO fora da DTB: 1 (por exemplo, 9999999)",
                   "REDE fora de ['Estadual', 'Municipal', 'Pública', 'Federal']: 1 (por exemplo, linha 15: 'Privada')",
                   "município e rede repetidos: 1 (por exemplo, linha 16)", "linha de dados depois das notas: 1 (por exemplo, linha 19)"):
        assert trecho in texto, trecho
    assert "11.2" not in texto


# --- Consulta, cache e coleta ---------------------------------------------------------------------------------

CADEIA = [{"assunto": "*.inep.gov.br", "emissor": "RNP ICPEdu GR46 OV TLS CA 2025", "autoassinado": False, "sha256": "f" * 64},
          {"assunto": "RNP ICPEdu GR46 OV TLS CA 2025", "emissor": "GlobalSign Root R46", "autoassinado": False,
           "sha256": inep_cliente.INTERMEDIARIO_SHA256},
          {"assunto": "GlobalSign Root R46", "emissor": "GlobalSign Root R46", "autoassinado": True, "sha256": "a" * 64}]


class Inep:
    """A origem falsa: o conteúdo e o Last-Modified de cada caminho, e os pedidos que chegaram."""

    def __init__(self, pasta):
        pasta.mkdir()
        self.conteudo, self.last_modified, self.pedidos, self.ausentes = {}, {}, [], set()
        for item in inep.esperados(REGRAS):
            destino = pasta / item["nome"]
            if item["base"] == "inep_censo_escolar":
                censo_zip(destino, item["ano"])
            else:
                ideb_xlsx(destino)
            self.conteudo[item["caminho"]], self.last_modified[item["caminho"]] = destino.read_bytes(), LM

    def mudar(self, caminho, conteudo, last_modified="Fri, 20 Nov 2026 10:00:00 GMT"):
        self.conteudo[caminho], self.last_modified[caminho] = conteudo, last_modified

    def __call__(self, caminho, ctx, intermediario, metodo="HEAD", destino=None, esperado=None):
        self.pedidos.append((metodo, caminho))
        url = f"https://{inep_cliente.HOST}/{caminho}"
        if caminho in self.ausentes:
            return {"url": url, "status": 404, "tentativas": [], "s": 0.0, "cadeia": CADEIA}
        dados = self.conteudo[caminho]
        resposta = {"url": url, "status": 200, "bytes": len(dados), "last_modified": self.last_modified[caminho],
                    "tentativas": [], "s": 0.01, "cadeia": CADEIA}
        if metodo == "GET":
            assert (esperado["bytes"], esperado["last_modified"]) == (len(dados), self.last_modified[caminho])
            destino.write_bytes(dados)
            resposta["sha256"] = hashlib.sha256(dados).hexdigest()
        return resposta

    def gets(self):
        return [c for m, c in self.pedidos if m == "GET"]


@pytest.fixture
def origem(tmp_path):
    return Inep(tmp_path / "origem")


@pytest.fixture
def intermediario():
    return inep_cliente.carregar_intermediario(agora=HOJE)


def rodada(origem, intermediario, cache, anterior=None):
    """Consulta e coleta, como o workflow: (manifesto, medição)."""
    origem.pedidos.clear()
    remoto, consulta = inep.consultar(REGRAS, None, intermediario, pedir=origem)
    return inep.coletar(REGRAS, remoto, cache, MUNICIPIOS, anterior, None, intermediario, pedir=origem, agora=HOJE,
                        consulta=consulta)


def test_consulta_grava_so_o_que_identifica_cada_arquivo(origem, intermediario):
    remoto, medicao = inep.consultar(REGRAS, None, intermediario, pedir=origem)
    assert remoto == inep.consultar(REGRAS, None, intermediario, pedir=origem)[0]
    assert set(remoto) == {"host", "arquivos"} and len(remoto["arquivos"]) == 9
    item = remoto["arquivos"]["microdados_censo_escolar_2019.zip"]
    assert item == {"caminho": "dados_abertos/microdados_censo_escolar_2019.zip",
                    "bytes": len(origem.conteudo[item["caminho"]]), "last_modified": LM}
    assert all(m == "HEAD" for m, _ in origem.pedidos) and medicao["microdados_censo_escolar_2019.zip"]["cadeia"] == CADEIA


def test_arquivo_esperado_que_some_da_origem_para_a_consulta_com_todos_os_que_faltam(origem, intermediario):
    origem.ausentes = {"dados_abertos/microdados_censo_escolar_2019.zip", "ideb/resultados/divulgacao_anos_finais_municipios_2023.xlsx"}
    with pytest.raises(ErroDeContrato, match="2 arquivo\\(s\\) .* respondem 404: .*2019.zip, .*anos_finais.*Nada foi baixado"):
        inep.consultar(REGRAS, None, intermediario, pedir=origem)


def test_primeira_coleta_baixa_tudo_e_a_segunda_nao_baixa_nada(origem, intermediario, tmp_path):
    cache = tmp_path / "cache"
    manifesto, medicao = rodada(origem, intermediario, cache)
    assert len(origem.gets()) == 9 and medicao["cache"]["baixados"] == 9 and medicao["cache"]["do_cache"] == 0
    assert {a["em_relacao_a_coleta_anterior"] for a in manifesto["arquivos"].values()} == {"primeira_coleta"}
    item = manifesto["arquivos"]["microdados_censo_escolar_2024.zip"]
    caminho = "dados_abertos/microdados_censo_escolar_2024.zip"
    assert item["sha256"] == hashlib.sha256(origem.conteudo[caminho]).hexdigest() and item["bytes"] == len(origem.conteudo[caminho])
    assert item["last_modified"] == LM and item["modificado_na_origem"] == "2026-07-08T12:22:27Z" and item["ano"] == 2024
    assert item["url"] == f"https://download.inep.gov.br/{caminho}" and item["contrato"]["linhas"] == 3
    assert manifesto["tls"]["intermediario_versionado_na_cadeia"] and manifesto["tls"]["raizes"] == ["GlobalSign Root R46"]
    assert sorted(p.name for p in cache.iterdir()) == sorted([*manifesto["arquivos"], "indice.json"])

    manifesto2, medicao2 = rodada(origem, intermediario, cache, anterior=manifesto)
    assert origem.gets() == [] and medicao2["cache"] == {"do_cache": 9, "baixados": 0, "bytes_baixados": 0,
                                                         "bytes_no_cache": medicao["cache"]["bytes_no_cache"], "removidos": []}
    assert {a["em_relacao_a_coleta_anterior"] for a in manifesto2["arquivos"].values()} == {"igual"}
    assert {n: a["sha256"] for n, a in manifesto2["arquivos"].items()} == {n: a["sha256"] for n, a in manifesto["arquivos"].items()}


def test_so_o_arquivo_que_mudou_na_origem_e_baixado(origem, intermediario, tmp_path):
    cache = tmp_path / "cache"
    manifesto, _ = rodada(origem, intermediario, cache)
    caminho = "dados_abertos/microdados_censo_escolar_2024.zip"
    origem.mudar(caminho, censo_zip(tmp_path / "novo.zip", 2024, [escola(2024, "1", "1100015")]).read_bytes())
    manifesto2, medicao2 = rodada(origem, intermediario, cache, anterior=manifesto)
    assert origem.gets() == [caminho]
    assert medicao2["arquivos"]["microdados_censo_escolar_2024.zip"]["motivo"] == "tamanho ou Last-Modified mudou na origem"
    assert manifesto2["arquivos"]["microdados_censo_escolar_2024.zip"]["em_relacao_a_coleta_anterior"] == "mudou"
    assert manifesto2["arquivos"]["microdados_censo_escolar_2024.zip"]["contrato"]["linhas"] == 1
    assert json.loads((cache / "indice.json").read_text())["microdados_censo_escolar_2024.zip"]["last_modified"] == origem.last_modified[caminho]


def test_copia_estragada_no_cache_e_baixada_de_novo(origem, intermediario, tmp_path):
    cache = tmp_path / "cache"
    manifesto, _ = rodada(origem, intermediario, cache)
    copia = cache / "divulgacao_anos_iniciais_municipios_2023.xlsx"
    dados = bytearray(copia.read_bytes())
    dados[-1] ^= 0xFF
    copia.write_bytes(bytes(dados))
    manifesto2, medicao2 = rodada(origem, intermediario, cache, anterior=manifesto)
    assert origem.gets() == ["ideb/resultados/divulgacao_anos_iniciais_municipios_2023.xlsx"]
    assert medicao2["arquivos"][copia.name]["motivo"] == "cópia do cache não confere com o SHA-256 do índice"
    assert manifesto2["arquivos"][copia.name]["em_relacao_a_coleta_anterior"] == "igual"


def test_indice_estragado_baixa_tudo_de_novo(origem, intermediario, tmp_path):
    cache = tmp_path / "cache"
    rodada(origem, intermediario, cache)
    for estragado in ("{nao e json", "[1, 2]", '{"microdados_censo_escolar_2019.zip": "texto"}'):
        (cache / "indice.json").write_text(estragado)
        rodada(origem, intermediario, cache)
        assert len(origem.gets()) == 9, estragado


def test_copia_que_nao_bate_com_o_manifesto_anterior_nao_e_usada(origem, intermediario, tmp_path):
    """Mesmo tamanho e Last-Modified do manifesto, outro SHA-256: a cópia é descartada, e a diferença aparece."""
    cache = tmp_path / "cache"
    manifesto, _ = rodada(origem, intermediario, cache)
    anterior = copy.deepcopy(manifesto)
    anterior["arquivos"]["microdados_censo_escolar_2019.zip"]["sha256"] = "0" * 64
    manifesto2, medicao2 = rodada(origem, intermediario, cache, anterior=anterior)
    assert origem.gets() == ["dados_abertos/microdados_censo_escolar_2019.zip"]
    assert medicao2["arquivos"]["microdados_censo_escolar_2019.zip"]["motivo"] == \
        "cópia do cache não confere com o SHA-256 do manifesto anterior"
    assert manifesto2["arquivos"]["microdados_censo_escolar_2019.zip"]["em_relacao_a_coleta_anterior"] == "mudou_sem_mudar_last_modified"


def test_arquivo_que_saiu_do_catalogo_e_apagado_do_cache(origem, intermediario, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "microdados_censo_escolar_2018.zip").write_bytes(b"ano que saiu")
    _, medicao = rodada(origem, intermediario, cache)
    assert medicao["cache"]["removidos"] == ["microdados_censo_escolar_2018.zip"]
    assert not (cache / "microdados_censo_escolar_2018.zip").exists()


def test_remoto_de_outro_catalogo_para(origem, intermediario, tmp_path):
    remoto, _ = inep.consultar(REGRAS, None, intermediario, pedir=origem)
    del remoto["arquivos"]["microdados_censo_escolar_2019.zip"]
    with pytest.raises(ErroDeContrato, match="remoto.json não corresponde"):
        inep.coletar(REGRAS, remoto, tmp_path / "cache", MUNICIPIOS, None, None, intermediario, pedir=origem)


def test_contrato_reprovado_para_a_coleta(origem, intermediario, tmp_path):
    origem.mudar("dados_abertos/microdados_censo_escolar_2021.zip",
                 censo_zip(tmp_path / "errado.zip", 2021, [escola(2020, "1", "1100015")]).read_bytes())
    with pytest.raises(ErroDeContrato, match="microdados_censo_escolar_2021.zip não passa no contrato. NU_ANO_CENSO diferente de 2021"):
        rodada(origem, intermediario, tmp_path / "cache")


def test_cadeia_sem_raiz_para_e_outro_intermediario_vira_aviso(intermediario):
    with pytest.raises(inep_cliente.ErroDeCertificado, match="não é raiz"):
        inep.tls({"a": {"cadeia": CADEIA[:2]}}, intermediario)
    outra = [CADEIA[0], {**CADEIA[1], "assunto": "RNP ICPEdu GR46 OV TLS CA 2026", "sha256": "b" * 64}, CADEIA[2]]
    resumo, avisos = inep.tls({"a": {"cadeia": outra}}, intermediario)
    assert not resumo["intermediario_versionado_na_cadeia"]
    assert avisos and "RNP ICPEdu GR46 OV TLS CA 2026" in avisos[0] and "confira se ele deve ser trocado" in avisos[0]


# --- Linha de comando -----------------------------------------------------------------------------------------


def test_linha_de_comando_consulta_coleta_e_coleta_de_novo_sem_baixar(origem, intermediario, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inep_cliente, "pedir", origem)
    monkeypatch.setattr(inep_cliente, "carregar_intermediario", lambda: intermediario)
    ibge = tmp_path / "ibge.json"
    ibge.write_text(json.dumps({"municipios": [{"codigo": c, "uf": u} for c, u in MUNICIPIOS.items()]}))
    saida, cache = tmp_path / "build" / "inep", tmp_path / "cache"
    assert inep.main(["consultar", "--saida", str(saida)]) == 0
    assert json.loads((saida / "consulta.json").read_text(encoding="utf-8"))["tls"]["raizes"] == ["GlobalSign Root R46"]
    coletar = ["coletar", "--remoto", str(saida / "remoto.json"), "--cache", str(cache), "--municipios", str(ibge),
               "--saida", str(saida), "--anterior", str(tmp_path / "inep.json"), "--medicao", str(tmp_path / "medicao")]
    assert inep.main(coletar) == 0
    (tmp_path / "inep.json").write_bytes((saida / "inep.json").read_bytes())
    assert inep.main(coletar) == 0
    medicao = json.loads((tmp_path / "medicao" / "coleta.json").read_text(encoding="utf-8"))
    assert medicao["cache"]["do_cache"] == 9 and "maquina" in medicao
    saida_texto = capsys.readouterr().out
    assert "Cache: 0 do cache, 9 baixados" in saida_texto and "Cache: 9 do cache, 0 baixados" in saida_texto
    assert "em relação à coleta anterior: igual" in saida_texto
    # Nada dos arquivos aparece no log nem nos JSON: nem o nome da escola, nem os nomes de município da planilha.
    publicados = saida_texto + "".join(p.read_text(encoding="utf-8") for p in [*saida.iterdir(), *(tmp_path / "medicao").iterdir()])
    assert NOME_DA_ESCOLA not in publicados and "Município A" not in publicados and "11000001" not in publicados


def test_linha_de_comando_para_com_erro_claro(monkeypatch, capsys, tmp_path):
    def sem_intermediario():
        raise inep_cliente.ErroDeCertificado("O intermediário da RNP não está em x.pem")
    monkeypatch.setattr(inep_cliente, "carregar_intermediario", sem_intermediario)
    assert inep.main(["consultar", "--saida", str(tmp_path)]) == 1
    assert "::error title=INEP, ErroDeCertificado::O intermediário da RNP não está em x.pem" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
