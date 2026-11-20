"""Página de consulta: só entra com a chave, a página de município ganha só o link, o catálogo embutido não leva
o SIH, e o caminho do endereço até a consulta de modelo roda no Node, sem rede (tests/consulta.test.cjs)."""
import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import catalogo, projeto, site, site_consulta
from pipeline.contrato import sha256_valores
from test_populacao_por_ano import coleta_com_serie
from test_site import coleta as coleta_do_site

RAIZ = Path(__file__).resolve().parent.parent
TESTE = Path(__file__).with_name("consulta.test.cjs")
SORRISO = {"codigo": "5107925", "nome": "Sorriso", "uf": "MT", "uf_nome": "Mato Grosso",
           "populacao_estimada": 110635, "populacao_censo": 110635}
LINK = re.compile(r'<p class="serie-link">.*?</p>')


def coleta_da_consulta():
    """A coleta com a série dos testes do site, mais Sorriso, que tem nota do IBGE em 2024."""
    coleta, cat = coleta_com_serie()
    coleta = copy.deepcopy(coleta)
    coleta["municipios"].append(dict(SORRISO))
    serie = coleta["populacao_por_ano"]
    for ano, valores in serie["valores"].items():
        valores[SORRISO["codigo"]] = SORRISO["populacao_estimada"] - (2026 - int(ano)) * 100
        serie["anos"][ano].update(municipios=len(valores), soma=sum(valores.values()), sha256_valores=sha256_valores(valores))
    return coleta, cat


@pytest.fixture(scope="module")
def sites(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("consulta")
    coleta, cat = coleta_da_consulta()
    site.construir(coleta, cat, pasta / "com", execucao="1", construtor=True)
    site.construir(coleta, cat, pasta / "sem", execucao="1", construtor=False)
    return pasta / "com", pasta / "sem", coleta, cat


def arquivos(pasta):
    return {p.relative_to(pasta).as_posix() for p in pasta.rglob("*") if p.is_file()}


def test_chave_do_projeto_fica_desligada_e_recusa_valor_que_nao_e_booleano(tmp_path):
    assert projeto.CONSTRUTOR is False
    arquivo = tmp_path / "projeto.yaml"
    arquivo.write_text("repositorio: https://github.com/dono/nome\nsite: https://dono.github.io/nome/\nconstrutor: sim\n",
                       encoding="utf-8")
    with pytest.raises(projeto.ErroDeConfiguracao, match="construtor"):
        projeto.carregar(arquivo)


def test_sem_a_chave_nada_da_consulta_vai_para_o_site(sites):
    _, sem, coleta, _ = sites
    assert not any(a.startswith(site_consulta.CAMINHO) for a in arquivos(sem))
    assert not set(site_consulta.ARQUIVOS_WEB) & arquivos(sem)
    for m in coleta["municipios"]:
        assert "consulta/" not in (sem / f"municipio/{m['codigo']}/index.html").read_text(encoding="utf-8")


def test_com_a_chave_entram_a_pagina_os_scripts_e_o_link_e_mais_nada(sites):
    com, sem, coleta, _ = sites
    assert arquivos(com) - arquivos(sem) == {"consulta/index.html", *site_consulta.ARQUIVOS_WEB}
    assert arquivos(sem) <= arquivos(com)
    paginas = {f"municipio/{m['codigo']}/index.html" for m in coleta["municipios"]}
    for nome in sorted(arquivos(sem)):
        antes, depois = (sem / nome).read_bytes(), (com / nome).read_bytes()
        if nome in paginas:
            # A página de município muda só no parágrafo do link, um por página.
            texto = depois.decode("utf-8")
            assert len(LINK.findall(texto)) == 1, nome
            assert LINK.sub("", texto).encode("utf-8") == antes, nome
        else:
            assert depois == antes, nome


def test_link_vai_do_primeiro_ao_ultimo_ano_com_valor_do_municipio(sites):
    com, _, _, _ = sites
    ler = lambda codigo: (com / f"municipio/{codigo}/index.html").read_text(encoding="utf-8")
    assert ('<a href="../../consulta/#v=1&amp;p=m:populacao_estimada;t:n,3548500;p:2013,2026;g:linha">'
            'População estimada ano a ano, de 2013 a 2026') in ler("3548500")
    # Boa Esperança do Norte só tem estimativa de 2025 em diante.
    assert "t:n,5101837;p:2025,2026;g:linha" in ler("5101837") and "de 2025 a 2026" in ler("5101837")
    assert site_consulta.estado("populacao_estimada", "5101837", 2026, 2026) == "v=1&p=m:populacao_estimada;t:n,5101837;p:2026;g:numero"


def test_pagina_de_consulta_cabe_no_limite_e_o_catalogo_embutido_nao_leva_o_sih(sites):
    com, _, _, cat = sites
    pagina = (com / "consulta/index.html").read_text(encoding="utf-8")
    assert len(pagina.encode("utf-8")) <= cat["limites"]["pagina_html_kb"] * 1000
    embutido = json.loads(re.search(r'<script type="application/json" id="catalogo">(.*?)</script>', pagina, re.S).group(1))
    assert set(embutido["bases"]) == {"ibge_dtb", "ibge_estimativa", "ibge_censo_2022"}
    assert set(embutido["metricas"]) == {"populacao_estimada", "populacao_censo"}
    assert embutido["cruzamentos"] == [] and [c["id"] for c in embutido["cruzamentos_da_origem"]] == ["ibge_pop_ano"]
    assert set(embutido["dimensoes"]) == {"municipio"} and "referencias" not in embutido
    # A regra interna da coleta não é o que a tela precisa; as quebras, com os documentos, sim.
    serie = embutido["cruzamentos_da_origem"][0]
    assert "quebra_nao_declarada" not in serie and serie["quebras"][0]["documentos"]
    for proibido in ("sih", "SIH", "DATASUS", "internac", "Internaç"):
        assert proibido not in pagina, proibido
    assert f'<link rel="canonical" href="{projeto.SITE}consulta/">' in pagina
    scripts = re.findall(r'<script src="\.\./([\w.]+)" defer>', pagina)
    assert scripts == list(site_consulta.SCRIPTS)


def test_ressalva_neutra_no_catalogo_e_completa_so_na_pagina_de_municipio(sites):
    com, sem, _, cat = sites
    neutra = "É estimativa do IBGE, não contagem. A contagem mais recente é a do Censo 2022."
    assert cat["metricas"]["populacao_estimada"]["ressalvas"] == [neutra]
    for pasta in (com, sem):
        pagina = (pasta / "municipio/3548500/index.html").read_text(encoding="utf-8")
        assert "<li>É estimativa do IBGE, não contagem. A contagem mais recente é a do Censo 2022, logo abaixo.</li>" in pagina
    consulta = (com / "consulta/index.html").read_text(encoding="utf-8")
    assert neutra in consulta and "logo abaixo" not in consulta
    assert "ficam em “Fonte e método”, abaixo da tabela." in consulta


def test_json_embutido_nao_fecha_o_script():
    texto = site_consulta._json_no_html({"x": "</script><!-- y"})
    assert "</" not in texto and "<!--" not in texto and json.loads(texto) == {"x": "</script><!-- y"}


def test_serie_que_saiu_do_site_tira_o_link_e_a_pagina_fica(tmp_path):
    reprovada = {"ibge_pop_ano": {"erro": "ErroDeContrato", "motivo": "teste", "etapa": "coleta", "em": "2026-11-07T23:23:20Z"}}
    versao = site.construir(coleta_do_site(), catalogo.carregar(), tmp_path / "site", construtor=True, reprovadas=reprovada)
    assert versao["isoladas"]["ibge_pop_ano"]["situacao"] == "fora"
    assert (tmp_path / "site/consulta/index.html").exists()
    assert "serie-link" not in (tmp_path / "site/municipio/3548500/index.html").read_text(encoding="utf-8")


def test_construtor_na_linha_de_comando_para_no_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(site.ErroBuildLocal, match="--construtor .*chave construtor"):
        site.exigir_build_local("--construtor")


def test_leitor_do_site_e_o_mesmo_que_confere_o_site_publicado():
    """web/leitor.js é gerado por ferramentas/empacotar e vai para o git: a versão do cabeçalho é a das duas
    instalações, e quem confere o Parquet publicado no CI lê com o mesmo leitor que o navegador."""
    cabecalho = (RAIZ / "web/leitor.js").read_text(encoding="utf-8").splitlines()[0]
    versao = re.match(r"/\* hyparquet (\d+\.\d+\.\d+), empacotado por ferramentas/empacotar/empacotar\.mjs\.", cabecalho).group(1)
    for pasta in ("ferramentas/leitor", "ferramentas/empacotar"):
        trava = json.loads((RAIZ / pasta / "package-lock.json").read_text(encoding="utf-8"))
        assert trava["packages"]["node_modules/hyparquet"]["version"] == versao, pasta
    pacote = json.loads((RAIZ / "ferramentas/empacotar/package.json").read_text(encoding="utf-8"))
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in pacote["dependencies"].values())


def test_do_endereco_ate_a_consulta_de_modelo_no_node(sites, tmp_path):
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node não encontrado: os testes de web/consulta.js não rodaram")
        pytest.skip("Node não instalado nesta máquina")
    com, _, _, cat = sites
    inteiro = tmp_path / "catalogo.json"
    inteiro.write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([node, str(TESTE), str(com), str(inteiro)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr + r.stdout
