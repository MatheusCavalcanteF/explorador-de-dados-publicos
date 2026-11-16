"""Falha isolada por base: a base que reprova não para o site, e o que foi ao ar fica no manifesto.

Cada execução passa pela linha de comando na ordem do Publicar (manter, construir, manifesto, exigir), com o
site no ar trocado por um falso que serve os arquivos de uma pasta. Nenhum teste usa rede."""
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import __main__ as cli
from pipeline import catalogo, ibge, isoladas, manifesto, projeto, rede, site
from pipeline.contrato import ErroDeContrato
from conftest import FonteFalsa, resposta, sidra
from test_populacao_por_ano import LISTA, REGRA, coleta_com_serie, fonte, valores_padrao
from test_site import coleta as coleta_do_site

SERIE = ["dados/ibge/populacao_por_ano.parquet", "dados/ibge/populacao_por_ano.json"]
REPROVACAO = {"ibge_pop_ano": {"erro": "ErroDeContrato", "motivo": "População por ano, 2021: a soma dos municípios "
                                                                    "(10) difere do total do Brasil da própria tabela (11)."}}


def arquivos(pasta: Path) -> dict[str, bytes]:
    return {p.relative_to(pasta).as_posix(): p.read_bytes() for p in sorted(pasta.rglob("*")) if p.is_file()}


class SiteNoAr:
    """O site publicado: serve os arquivos de `pasta` pelo endereço de projeto.yaml, com qualquer marca de
    consulta no fim. `trocar` muda a resposta de um caminho; registra os pedidos."""

    def __init__(self, pasta: Path | None, trocar: dict | None = None):
        self.pasta, self.trocar, self.pedidos = pasta, trocar or {}, []

    def __call__(self, url, **opcoes):
        self.pedidos.append(url)
        assert opcoes == {"descomprimir": False} and "?manter=" in url
        caminho = url.split("?")[0].removeprefix(projeto.SITE)
        if caminho in self.trocar:
            troca = self.trocar[caminho]
            if isinstance(troca, Exception):
                raise troca
            return troca
        alvo = self.pasta / caminho if self.pasta else None
        return resposta(alvo.read_bytes()) if alvo and alvo.is_file() else resposta(b"Not Found", status=404)


def publicar(raiz: Path, nome: str, coleta: dict, reprovadas: dict, manifesto_anterior: Path | None, no_ar,
             monkeypatch, execucao: str) -> Path:
    """Uma execução do Publicar, do fim da coleta ao manifesto. O manifesto da anterior é copiado antes, como
    o checkout do git o traria."""
    pasta = raiz / nome
    coleta_dir, manifesto_dir = pasta / "coleta", pasta / "manifesto"
    coleta_dir.mkdir(parents=True)
    if manifesto_anterior is not None:
        shutil.copytree(manifesto_anterior, manifesto_dir)
    else:
        manifesto_dir.mkdir()
    (coleta_dir / "ibge.json").write_text(json.dumps(coleta, ensure_ascii=False), encoding="utf-8")
    isoladas.registrar(coleta_dir, "coleta", reprovadas, coleta["coletado_em"])
    monkeypatch.setattr(rede, "pedir", no_ar)
    assert cli.main(["manter", "--coleta", str(coleta_dir), "--manifesto", str(manifesto_dir / "publicado.json"),
                     "--destino", str(pasta / "mantidos")]) == 0
    assert cli.main(["construir", "--coleta", str(coleta_dir), "--saida", str(pasta / "site"), "--execucao", execucao,
                     "--mantidos", str(pasta / "mantidos")]) == 0
    # As conferências do site publicado, todas certas: só a base isolada pode deixar a execução vermelha.
    for nome_conferencia, valor in (("alcance", {"fontes": {"datasus_ftp": {"ok": True}}}),
                                    ("hospedagem", {"testes": {"range": {"bate_com_o_plano": True}}}),
                                    ("leitor", {"ok": True})):
        (coleta_dir / f"{nome_conferencia}.json").write_text(json.dumps(valor), encoding="utf-8")
    assert cli.main(["manifesto", "--coleta", str(coleta_dir), "--saida", str(manifesto_dir)]) == 0
    return pasta


def coleta_nova():
    """A coleta da semana seguinte, sem a série: outra data, então as páginas mudam."""
    return coleta_do_site() | {"coletado_em": "2026-11-22T07:13:00Z"}


def ler(caminho: Path):
    return json.loads(caminho.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def anterior(tmp_path_factory):
    """A publicação de 22/11 em que tudo passou: o site com a série, e o manifesto que ela gravou."""
    raiz = tmp_path_factory.mktemp("anterior")
    no_ar = SiteNoAr(None)
    with pytest.MonkeyPatch.context() as mp:
        pasta = publicar(raiz, "1", coleta_com_serie()[0], {}, None, no_ar, mp, "1")
    assert no_ar.pedidos == []
    return pasta


@pytest.fixture
def construir_sem_mecanismo(tmp_path):
    """O site da mesma coleta pelo caminho de antes do mecanismo, para comparar byte a byte."""
    def construir(coleta, execucao):
        saida = tmp_path / f"direto-{execucao}"
        site.construir(coleta, catalogo.carregar(), saida, execucao=execucao)
        return arquivos(saida)
    return construir


# ---------------------------------------------------------------- tudo passa

def test_tudo_passa_e_nada_muda(anterior, tmp_path, monkeypatch, construir_sem_mecanismo, capsys):
    coleta = coleta_com_serie()[0]
    no_ar = SiteNoAr(anterior / "site")
    pasta = publicar(tmp_path, "2", coleta, {}, anterior / "manifesto", no_ar, monkeypatch, "2")
    # Sem reprovação, nada é pedido ao site, e o site é o mesmo, byte a byte, do caminho sem o mecanismo.
    assert no_ar.pedidos == []
    assert arquivos(pasta / "site") == construir_sem_mecanismo(coleta, "2")
    registro = ler(pasta / "manifesto" / "publicado.json")
    item = registro["bases"]["ibge_pop_ano"]
    assert (registro["execucao"], item["situacao"], item["execucao"], item["reprovada"]) == ("2", "publicada", "2", None)
    for caminho in SERIE:
        corpo = (pasta / "site" / caminho).read_bytes()
        assert item["arquivos"][caminho] == {"sha256": rede.sha256(corpo), "bytes": len(corpo)}
    assert manifesto.falhas(pasta / "coleta") == []
    capsys.readouterr()
    assert cli.main(["exigir", "--coleta", str(pasta / "coleta")]) == 0


# ---------------------------------------------------------------- a série reprova

def test_serie_reprova_e_o_site_sai_com_os_arquivos_anteriores_iguais_byte_a_byte(anterior, tmp_path, monkeypatch,
                                                                                   construir_sem_mecanismo, capsys):
    coleta = coleta_nova()
    no_ar = SiteNoAr(anterior / "site")
    pasta = publicar(tmp_path, "2", coleta, REPROVACAO, anterior / "manifesto", no_ar, monkeypatch, "2")
    novo, velho = arquivos(pasta / "site"), arquivos(anterior / "site")
    # Os dois arquivos da série vêm do site no ar, iguais byte a byte; um pedido por arquivo.
    assert sorted(u.split("?")[0].removeprefix(projeto.SITE) for u in no_ar.pedidos) == sorted(SERIE)
    for caminho in SERIE:
        assert novo[caminho] == velho[caminho], caminho
    # O resto do site é o da coleta nova, como sem o mecanismo: a página de Santos mudou com a data.
    assert {k: v for k, v in novo.items() if k not in SERIE} == construir_sem_mecanismo(coleta, "2")
    assert novo["municipio/3548500/index.html"] != velho["municipio/3548500/index.html"]
    # O manifesto registra a versão mantida, da coleta de antes, e continua com o registro da série dela:
    # é com ele que a coleta seguinte compara.
    item = ler(pasta / "manifesto" / "publicado.json")["bases"]["ibge_pop_ano"]
    antes = ler(anterior / "manifesto" / "publicado.json")["bases"]["ibge_pop_ano"]
    assert item["situacao"] == "mantida" and item["arquivos"] == antes["arquivos"]
    assert (item["coletado_em"], item["execucao"]) == (antes["coletado_em"], "1")
    assert item["reprovada"]["motivo"] == REPROVACAO["ibge_pop_ano"]["motivo"] and item["reprovada"]["etapa"] == "coleta"
    assert ler(pasta / "manifesto" / "ibge.json")["populacao_por_ano"] == ler(anterior / "manifesto" / "ibge.json")["populacao_por_ano"]
    assert ler(pasta / "manifesto" / "ibge.json")["coletado_em"] == coleta["coletado_em"]
    # A execução termina vermelha, no mesmo passo das outras falhas, dizendo o que reprovou e o que foi ao ar.
    capsys.readouterr()
    assert cli.main(["exigir", "--coleta", str(pasta / "coleta")]) == 1
    saida = capsys.readouterr().out.strip().splitlines()
    assert saida == [f"::error::População por município e ano (ibge_pop_ano) reprovou na coleta, e o resto do site foi "
                     f"publicado; o site saiu com a versão anterior dela, coletada em 07/11/2026, igual byte a byte à "
                     f"registrada no manifesto. Motivo: {REPROVACAO['ibge_pop_ano']['motivo']}"]

    # Na semana seguinte, se reprovar de novo, a versão mantida é mantida outra vez, pelo mesmo manifesto.
    de_novo = publicar(tmp_path, "3", coleta_nova(), REPROVACAO, pasta / "manifesto", SiteNoAr(pasta / "site"),
                       monkeypatch, "3")
    assert all(arquivos(de_novo / "site")[c] == velho[c] for c in SERIE)
    assert ler(de_novo / "manifesto" / "publicado.json")["bases"]["ibge_pop_ano"]["execucao"] == "1"


def test_serie_reprova_sem_versao_anterior_e_sai_do_site(tmp_path, monkeypatch, construir_sem_mecanismo, capsys):
    # 22/11/2026: a primeira coleta da série no runner. O manifesto ainda não tem publicado.json, e o site, a série.
    coleta = coleta_nova()
    no_ar = SiteNoAr(None)
    pasta = publicar(tmp_path, "1", coleta, REPROVACAO, None, no_ar, monkeypatch, "1")
    assert no_ar.pedidos == []
    novo = arquivos(pasta / "site")
    assert not set(SERIE) & set(novo)
    assert novo == construir_sem_mecanismo(coleta, "1")
    item = ler(pasta / "manifesto" / "publicado.json")["bases"]["ibge_pop_ano"]
    assert item["situacao"] == "fora" and item["ultima_publicada"] is None and "arquivos" not in item
    assert "populacao_por_ano" not in ler(pasta / "manifesto" / "ibge.json")
    capsys.readouterr()
    assert cli.main(["exigir", "--coleta", str(pasta / "coleta")]) == 1
    assert capsys.readouterr().out.strip() == (
        "::error::População por município e ano (ibge_pop_ano) reprovou na coleta, e o resto do site foi publicado; o "
        "site saiu sem ela, porque o manifesto da última publicação não tem versão desta base no site. Motivo: "
        f"{REPROVACAO['ibge_pop_ano']['motivo']}")


def _adulterado(pasta_anterior):
    parquet = bytearray((pasta_anterior / "site" / SERIE[0]).read_bytes())
    parquet[100] ^= 1
    return resposta(bytes(parquet))


@pytest.mark.parametrize("trocar, porque", [
    (lambda p: {SERIE[0]: _adulterado(p)}, "a cópia de dados/ibge/populacao_por_ano.parquet no site não confere com o "
                                           "manifesto: tem .* bytes e SHA-256 [0-9a-f]{64}, e o manifesto registra"),
    (lambda p: {SERIE[1]: resposta(b"{}")}, "a cópia de dados/ibge/populacao_por_ano.json no site não confere"),
    (lambda p: {SERIE[1]: resposta(b"Not Found", status=404)}, "o site respondeu HTTP 404 para https://"),
    (lambda p: {SERIE[0]: rede.ErroDeRede("timeout")}, r"o site não respondeu para dados/ibge/populacao_por_ano.parquet \(timeout\)"),
])
def test_copia_do_site_que_nao_confere_tira_a_base_com_erro_claro(anterior, tmp_path, monkeypatch, construir_sem_mecanismo,
                                                                  capsys, trocar, porque):
    import re
    coleta = coleta_nova()
    pasta = publicar(tmp_path, "2", coleta, REPROVACAO, anterior / "manifesto", SiteNoAr(anterior / "site", trocar(anterior)),
                     monkeypatch, "2")
    # Nada da cópia ruim, nem a outra metade da base, chega ao site; o resto é publicado normalmente.
    assert arquivos(pasta / "site") == construir_sem_mecanismo(coleta, "2")
    assert not any((pasta / "mantidos" / c).exists() for c in SERIE)
    item = ler(pasta / "manifesto" / "publicado.json")["bases"]["ibge_pop_ano"]
    assert item["situacao"] == "fora" and re.search(porque, item["porque"]), item["porque"]
    # O que estava no ar fica registrado, para quem vier depois saber que a base já foi publicada.
    assert item["ultima_publicada"]["execucao"] == "1"
    capsys.readouterr()
    assert cli.main(["exigir", "--coleta", str(pasta / "coleta")]) == 1
    linha = capsys.readouterr().out.strip()
    assert linha.startswith("::error::População por município e ano (ibge_pop_ano) reprovou na coleta, e o resto do "
                            "site foi publicado; o site saiu sem ela, porque ") and re.search(porque, linha)


# ---------------------------------------------------------------- a coleta isola só falha de fonte

def _coletar(tmp_path, monkeypatch, urls_da_serie, regra=REGRA):
    estimativa = valores_padrao()[2026]
    municipios = [{"codigo": c, "nome": f"Município {c}", "uf": "SP", "uf_nome": "São Paulo"} for c in LISTA]
    monkeypatch.setattr(ibge.dtb, "coletar", lambda pedir, coletado_em: (
        {"base": "ibge_dtb", "periodo": "2025", "data_base": "2025-12-31", "coletado_em": coletado_em,
         "sha256_valores": "d" * 64}, copy.deepcopy(municipios)))
    urls = urls_da_serie | {
        ibge.URL_SIDRA.format(**ibge.ESTIMATIVA): resposta(sidra(estimativa, 9324, "2026")),
        ibge.URL_SIDRA.format(**ibge.CENSO): resposta(sidra({c: 900 for c in LISTA}, 93, "2022"))}
    return ibge.coletar(tmp_path, pedir=FonteFalsa(urls), agora=datetime(2026, 11, 22, 7, 13, tzinfo=timezone.utc),
                        serie=regra)


@pytest.mark.parametrize("mudar, erro, motivo", [
    # Contrato: a soma dos municípios difere do total do Brasil.
    (lambda u: u.update({k: v for k, v in fonte(totais={a: sum(v.values()) + 1 for a, v in valores_padrao().items()})
                         .respostas.items() if "/n1/" in k}), "ErroDeContrato", "difere do total do Brasil"),
    # A SIDRA não responde para um ano da série.
    (lambda u: u.pop(next(k for k in u if k.endswith("/p/2024"))), "ErroDeRede", "/p/2024 fora do ar no teste"),
])
def test_coleta_segue_sem_a_serie_que_reprova_e_registra_o_motivo(tmp_path, monkeypatch, mudar, erro, motivo):
    urls = dict(fonte().respostas)
    mudar(urls)
    resultado = _coletar(tmp_path, monkeypatch, urls)
    assert "populacao_por_ano" not in resultado and resultado["fontes"]["estimativa"]["periodo"] == "2026"
    assert len(resultado["municipios"]) == len(LISTA)
    reprovadas = ler(tmp_path / "reprovadas.json")
    assert list(reprovadas) == ["ibge_pop_ano"] and reprovadas["ibge_pop_ano"]["erro"] == erro
    assert motivo in reprovadas["ibge_pop_ano"]["motivo"]
    assert reprovadas["ibge_pop_ano"]["etapa"] == "coleta" and reprovadas["ibge_pop_ano"]["em"] == "2026-11-22T07:13:00Z"
    assert "populacao_por_ano" not in ler(tmp_path / "ibge.json")


def test_coleta_que_passa_registra_nenhuma_reprovacao_e_comeca_o_arquivo_do_zero(tmp_path, monkeypatch):
    (tmp_path / "reprovadas.json").write_text(json.dumps(REPROVACAO), encoding="utf-8")   # de uma execução antiga
    resultado = _coletar(tmp_path, monkeypatch, dict(fonte().respostas))
    assert "populacao_por_ano" in resultado and ler(tmp_path / "reprovadas.json") == {}


def test_falha_que_nao_e_da_fonte_continua_parando_a_coleta(tmp_path, monkeypatch):
    # Um erro de programação na série (aqui, a regra sem a chave que a coleta lê) não é reprovação: sobe.
    with pytest.raises(KeyError, match="variacao_anual_pct"):
        _coletar(tmp_path, monkeypatch, dict(fonte().respostas), regra=REGRA | {"quebra_nao_declarada": {}})
    assert not (tmp_path / "reprovadas.json").exists()


def test_falha_da_lista_da_estimativa_ou_do_censo_para_tudo(tmp_path, monkeypatch):
    urls = dict(fonte().respostas)
    estimativa = ibge.URL_SIDRA.format(**ibge.ESTIMATIVA)
    monkeypatch.setattr(ibge.dtb, "coletar", lambda pedir, coletado_em: ({"periodo": "2025"}, []))
    with pytest.raises(ErroDeContrato, match="Estimativa de população: HTTP 503"):
        ibge.coletar(tmp_path, pedir=FonteFalsa(urls | {estimativa: resposta(b"", status=503)}), serie=REGRA)


# ---------------------------------------------------------------- problemas do próprio build param tudo

def test_estado_que_o_mecanismo_nao_espera_para_o_build(anterior, tmp_path, monkeypatch):
    cat = catalogo.carregar()
    coleta, _ = coleta_com_serie()
    # Sem reprovadas.json, o passo de manter não sabe o que a coleta decidiu.
    with pytest.raises(isoladas.ErroDeIsolamento, match="não registrou as bases que reprovaram"):
        isoladas.manter(tmp_path, cat, tmp_path / "publicado.json", tmp_path / "m", pedir=SiteNoAr(None))
    # Base reprovada que o catálogo não lista como isolada.
    isoladas.registrar(tmp_path, "coleta", {"ibge_estimativa": {"erro": "x", "motivo": "y"}}, "agora")
    with pytest.raises(isoladas.ErroDeIsolamento, match="não estão em isoladas no catálogo"):
        isoladas.manter(tmp_path, cat, tmp_path / "publicado.json", tmp_path / "m", pedir=SiteNoAr(None))
    # A base reprovou, mas o build gravou os arquivos dela.
    isoladas.registrar(tmp_path, "coleta", REPROVACAO, "agora")
    isoladas.manter(tmp_path, cat, tmp_path / "nao-existe.json", tmp_path / "m", pedir=SiteNoAr(None))
    with pytest.raises(isoladas.ErroDeIsolamento, match="ibge_pop_ano reprovou, mas o build gravou"):
        site.construir(coleta, cat, tmp_path / "s1", reprovadas=REPROVACAO, mantidos=tmp_path / "m")
    # No Publicar, base que não reprovou e não foi construída é erro, não base de fora.
    isoladas.registrar(tmp_path, "coleta", {}, "agora")
    isoladas.manter(tmp_path, cat, tmp_path / "nao-existe.json", tmp_path / "m", pedir=SiteNoAr(None))
    with pytest.raises(isoladas.ErroDeIsolamento, match="não reprovou, e o build não gravou"):
        site.construir(coleta_do_site(), cat, tmp_path / "s2", reprovadas={}, mantidos=tmp_path / "m")
    # O passo de manter tratou de outras bases que não as reprovadas.
    with pytest.raises(isoladas.ErroDeIsolamento, match="mas reprovaram"):
        site.construir(coleta_do_site(), cat, tmp_path / "s3", reprovadas=REPROVACAO, mantidos=tmp_path / "m")
    # Sem o passo de manter.
    with pytest.raises(isoladas.ErroDeIsolamento, match="não rodou"):
        site.construir(coleta_do_site(), cat, tmp_path / "s4", reprovadas=REPROVACAO, mantidos=tmp_path / "vazia")
    # O arquivo mantido mudou entre a conferência e o build.
    isoladas.registrar(tmp_path, "coleta", REPROVACAO, "agora")
    isoladas.manter(tmp_path, cat, anterior / "manifesto" / "publicado.json", tmp_path / "m", pedir=SiteNoAr(anterior / "site"))
    (tmp_path / "m" / SERIE[1]).write_text("{}", encoding="utf-8")
    with pytest.raises(isoladas.ErroDeIsolamento, match="mudou entre a conferência com o manifesto e o build"):
        site.construir(coleta_do_site(), cat, tmp_path / "s5", reprovadas=REPROVACAO, mantidos=tmp_path / "m")


def test_build_local_sem_o_passo_de_manter_deixa_a_base_reprovada_de_fora(tmp_path):
    cat = catalogo.carregar()
    versao = site.construir(coleta_do_site(), cat, tmp_path / "s", reprovadas=REPROVACAO)
    assert versao["isoladas"]["ibge_pop_ano"]["situacao"] == "fora"
    assert "build local" in versao["isoladas"]["ibge_pop_ano"]["porque"]
    # Sem a série e sem reprovação, como nos testes do site: nada registrado, nada exigido.
    assert site.construir(coleta_do_site(), cat, tmp_path / "t")["isoladas"] == {}


def test_exigir_aponta_build_sem_registro_das_bases(tmp_path):
    for nome, valor in (("alcance", {"fontes": {}}), ("hospedagem", {"testes": {}}), ("leitor", {"ok": True})):
        (tmp_path / f"{nome}.json").write_text(json.dumps(valor), encoding="utf-8")
    assert manifesto.falhas(tmp_path) == ["O build não registrou o que foi ao ar de cada base isolada (publicado.json)"]


def test_publicar_mantem_as_bases_reprovadas_logo_antes_do_site_e_exige_no_fim():
    import yaml
    texto = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publicar.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(texto)["jobs"]
    passos = jobs["construir"]["steps"]
    comandos = [p.get("run", "") for p in passos]
    coleta = next(i for i, c in enumerate(comandos) if "pipeline coletar" in c)
    sih = next(i for i, c in enumerate(comandos) if "pipeline sih " in c)
    manter = next(i for i, c in enumerate(comandos) if "pipeline manter" in c)
    construir = next(i for i, c in enumerate(comandos) if "pipeline construir" in c)
    # Depois de toda etapa que possa reprovar uma base, e logo antes do site, sem condição e sem seguir em erro.
    assert coleta < sih < manter == construir - 1
    assert "if" not in passos[manter] and "continue-on-error" not in passos[manter]
    assert "--manifesto manifesto/publicado.json --destino build/mantidos" in comandos[manter]
    assert "--mantidos build/mantidos" in comandos[construir]
    # O manifesto leva o que foi ao ar, e o último passo deixa a execução vermelha.
    conferir = jobs["conferir"]["steps"]
    assert "pipeline exigir --coleta build/coleta" in conferir[-1]["run"] and "continue-on-error" not in conferir[-1]
    assert any("git add manifesto" in p.get("run", "") for p in conferir)
