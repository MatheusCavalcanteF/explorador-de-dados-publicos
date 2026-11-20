"""Página de consulta: só entra com a chave, a página de município ganha só o link, o catálogo embutido não leva
o SIH, e o caminho do endereço até a consulta de modelo roda no Node, sem rede (tests/consulta.test.cjs)."""
import copy
import hashlib
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

# O site da versão anterior à consulta (746c0f4), construído com coleta_da_consulta() e o catálogo daquela
# versão, por quem extraiu a árvore com `git archive 746c0f4`: um SHA-256 por arquivo. Com a chave desligada, o
# build de agora precisa dar exatamente estes arquivos, com estes bytes. Uma mudança que altere o site de
# propósito refaz o gabarito, com a chave desligada, e diz no commit o que mudou. O gabarito e este teste saem
# no commit que liga a chave construtor (decidido em 20/11/2026).
SITE_SEM_A_CONSULTA = {
    "404.html":
        "ded936396a90fdd5e9ba418e8f0b931380aa4a72cbfa2703df74d4af89add29b",
    "busca.js":
        "bb21b1560d408a674b5d7e6a3371ea7a426bb8bf46aface8ef652c2b9f9cf81d",
    "dados/ibge/populacao.parquet":
        "dcf4194ff2b107ff2d3bd34d174eeeae944ef799552978c9b2366e5e5bf8cd6c",
    "dados/ibge/populacao_por_ano.json":
        "a069f8875187f098ea43709629800371e0b88c6f54d0b26937ac473f4c51d545",
    "dados/ibge/populacao_por_ano.parquet":
        "f191d41e03beb25d8bc50dc17452ccd8e86f40062a182e724a4bf5db68a8665b",
    "dados/municipios.json":
        "2c931403d139530de1f119baf5033ccb66cbd4e4cc5ef408ac2d947b5cb15f8a",
    "estilo.css":
        "26d613b1841bfb832ae0e2cda91f1110e6d31fe9331b3f2b1aa2dfb4880b1743",
    "fontes/OFL.txt":
        "948fc2278ccbc7e24c598095e3723d97b53666c02446b683e9cc6745e3760f6a",
    "fontes/overpass-latin-wght-normal.woff2":
        "29217ef8adef5f6c059feb461bdb029348205fc233dc9b6ca8604b0d068ac086",
    "index.html":
        "0a2ec8354fe866549b8b22d09cf47d48cf5d39f6ddd6b9a0445d848926d901c3",
    "municipio/3548500/index.html":
        "124dd3e8f5691b1dba68c40cdac844fe6a82781a0e068d9f7990a145650b052f",
    "municipio/4206504/index.html":
        "17e7509059ed586e920b82eef9eba4c51b854521511f8426288c78d3ade16926",
    "municipio/5101837/index.html":
        "9e692f1d49f0f7c7a2eba073620829cf79d24961926486347edbc7f1488d5eb8",
    "municipio/5107925/index.html":
        "c8b8e61548802af904267064fc01137d5161c197d8b0bdd2e1c0c8536be900b7",
    "sitemap.xml":
        "590155b9d3874382b14e71691f5eb731cb405ea706faac79c8b56e7ed10f7740",
    "uf/mt/index.html":
        "52c04acb6e1fe041ef07665fc29ad1121a2d4b0332241bc482ecaa04b3f95655",
    "uf/sc/index.html":
        "f965a684e775717c6ec721fc4109049a7b4baf14ace11ae2eb353347e496a607",
    "uf/sp/index.html":
        "c62b3bfc5ce27e9bc9d2ef6bfd6d0f603cbe1f50a6fdc7a07ad3a30c883f988d",
    "versao.json":
        "b965e98cf3ba1d71729a11094423a2b38f307986b8628abc28ad2f4bf968021a",
}


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


def test_chave_desligada_da_o_site_anterior_igual_byte_a_byte_sem_excecao(sites):
    _, sem, _, _ = sites
    agora = {p.relative_to(sem).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sem.rglob("*")) if p.is_file()}
    assert agora == SITE_SEM_A_CONSULTA


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
    assert site_consulta.ESTILO_DO_LINK not in arquivos(com) | arquivos(sem)
    paginas = {f"municipio/{m['codigo']}/index.html" for m in coleta["municipios"]}
    for nome in sorted(arquivos(sem)):
        antes, depois = (sem / nome).read_bytes(), (com / nome).read_bytes()
        if nome == "estilo.css":
            # Com a chave, a regra do link vai ao fim de estilo.css, e só ela.
            assert depois == antes + (RAIZ / "web" / site_consulta.ESTILO_DO_LINK).read_bytes()
            assert b".serie-link" not in antes
        elif nome in paginas:
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


PASTAS_DO_LEITOR = ("ferramentas/leitor", "ferramentas/empacotar")
MESMA_VERSAO = ("O hyparquet do site (web/leitor.js) e o da conferência do site publicado no CI (ferramentas/leitor) "
                "precisam ser a mesma versão. Suba ferramentas/leitor e ferramentas/empacotar juntas e gere web/leitor.js "
                "de novo: npm ci --prefix ferramentas/empacotar && node ferramentas/empacotar/empacotar.mjs.")


def versoes_do_hyparquet(raiz: Path) -> dict[str, str]:
    """Cada lugar que fixa a versão do hyparquet: o cabeçalho de web/leitor.js e, nas duas pastas, o package.json,
    o que o package-lock.json declara e o que ele instalou."""
    cabecalho = (raiz / "web/leitor.js").read_text(encoding="utf-8").splitlines()[0]
    achado = re.match(r"/\* hyparquet (\S+), empacotado por ferramentas/empacotar/empacotar\.mjs\.", cabecalho)
    versoes = {"web/leitor.js": achado.group(1) if achado else "sem cabeçalho"}
    for pasta in PASTAS_DO_LEITOR:
        pacote = json.loads((raiz / pasta / "package.json").read_text(encoding="utf-8"))
        trava = json.loads((raiz / pasta / "package-lock.json").read_text(encoding="utf-8"))
        versoes[f"{pasta}/package.json"] = pacote["dependencies"]["hyparquet"]
        versoes[f"{pasta}/package-lock.json, declarada"] = trava["packages"][""]["dependencies"]["hyparquet"]
        versoes[f"{pasta}/package-lock.json, instalada"] = trava["packages"]["node_modules/hyparquet"]["version"]
    return versoes


def test_hyparquet_do_site_e_da_conferencia_na_mesma_versao():
    """web/leitor.js é gerado por ferramentas/empacotar e vai para o git; ferramentas/leitor confere no CI o Parquet
    publicado. Os dois leem com a mesma versão, fixa, sem faixa: quem confere lê como o navegador."""
    versoes = versoes_do_hyparquet(RAIZ)
    assert len(set(versoes.values())) == 1, f"{MESMA_VERSAO} Versões: {versoes}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", versoes["web/leitor.js"])
    pacote = json.loads((RAIZ / "ferramentas/empacotar/package.json").read_text(encoding="utf-8"))
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in pacote["dependencies"].values())


def test_subir_so_a_conferencia_e_barrado(tmp_path):
    """O que o Dependabot propõe para ferramentas/leitor (1.32.0 para 1.33.0, no package.json e no lock), sem gerar
    web/leitor.js de novo: o teste de cima reprova."""
    for nome in ["web/leitor.js", *(f"{p}/{a}" for p in PASTAS_DO_LEITOR for a in ("package.json", "package-lock.json"))]:
        (tmp_path / nome).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / nome).write_bytes((RAIZ / nome).read_bytes())
    atual = versoes_do_hyparquet(tmp_path)["web/leitor.js"]
    for nome in ("ferramentas/leitor/package.json", "ferramentas/leitor/package-lock.json"):
        texto = (tmp_path / nome).read_text(encoding="utf-8")
        (tmp_path / nome).write_text(texto.replace(f"hyparquet-{atual}", "hyparquet-1.33.0")
                                     .replace(f'"{atual}"', '"1.33.0"'), encoding="utf-8")
    versoes = versoes_do_hyparquet(tmp_path)
    assert versoes["ferramentas/leitor/package.json"] == versoes["ferramentas/leitor/package-lock.json, instalada"] == "1.33.0"
    assert versoes["web/leitor.js"] == atual and len(set(versoes.values())) == 2


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
