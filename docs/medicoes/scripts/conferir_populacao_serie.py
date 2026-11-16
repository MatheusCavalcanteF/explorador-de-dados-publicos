"""Confere a população por município e ano coletada pelo pipeline com o que o IBGE publica por outros caminhos.

Duas conferências, nenhuma pelo pedido que o pipeline faz (um ano, todos os municípios):

1. SIDRA, pedido ao contrário: um município, todos os anos da tabela. Numa amostra sorteada com semente
   fixa, cada valor da série precisa ser o que a SIDRA devolve para aquele município.
2. Planilhas das Estimativas de População no FTP do IBGE (`Estimativas_AAAA/`), para os anos pedidos: a
   publicada no Diário Oficial (`estimativa_dou_AAAA`) e as revisões posteriores (`POPAAAA_data`). Todos os
   municípios, não uma amostra. Diz com qual das planilhas a SIDRA bate.

Roda da raiz do repositório, com a coleta do pipeline:

    python docs/medicoes/scripts/conferir_populacao_serie.py build/coleta/ibge.json docs/medicoes/resultados/conferencia_populacao_serie.json [pasta_das_respostas] [anos das planilhas...]

Sem anos, confere as planilhas de 2018, 2021, 2024 e 2025. O resultado não lista valores por município: só
contagens, e os códigos dos que diferem.
"""
import hashlib
import io
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SIDRA = "https://apisidra.ibge.gov.br/values/t/6579/n6/{municipio}/v/9324/p/all"
FTP = "https://ftp.ibge.gov.br/Estimativas_de_Populacao/Estimativas_{ano}/"
AGENTE = "explorador-de-dados-publicos (medicao)"
AMOSTRA, SEMENTE = 30, "conferencia-populacao-por-ano"
TABELA = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXTO = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def pedir(url: str, guardar: Path | None) -> bytes:
    if guardar and guardar.exists():
        return guardar.read_bytes()
    for tentativa in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENTE}), timeout=120) as r:
                corpo = r.read()
            break
        except Exception:
            if tentativa == 2:
                raise
            time.sleep(5 * (tentativa + 1))
    time.sleep(1)
    if guardar:
        guardar.parent.mkdir(parents=True, exist_ok=True)
        guardar.write_bytes(corpo)
    return corpo


def linhas_de_todas_as_folhas(ods: bytes) -> list[list[str]]:
    """Todas as linhas com texto, de todas as folhas: os municípios ficam na segunda."""
    with zipfile.ZipFile(io.BytesIO(ods)) as z:
        raiz = ET.fromstring(z.read("content.xml"))
    linhas = []
    for linha in raiz.iter(f"{{{TABELA}}}table-row"):
        celulas = []
        for celula in linha:
            if not celula.tag.endswith("table-cell"):
                continue
            texto = " ".join("".join(p.itertext()) for p in celula.iter(f"{{{TEXTO}}}p")).strip()
            repetir = int(celula.get(f"{{{TABELA}}}number-columns-repeated", "1"))
            celulas.extend([texto] * (repetir if texto else min(repetir, 3)))
        if any(celulas):
            linhas.append(celulas)
    return linhas


def municipios_da_planilha(ods: bytes) -> tuple[dict[str, int], int]:
    """Código de 7 dígitos e população de cada linha de município. A população vem como texto, às vezes com
    ponto de milhar e com a marca de uma nota, como "1.234 (1)". Devolve também quantas tinham nota."""
    valores, com_nota = {}, 0
    for linha in linhas_de_todas_as_folhas(ods):
        celulas = [c for c in linha if c]
        if len(celulas) < 5 or not re.fullmatch(r"[A-Z]{2}", celulas[0]):
            continue
        uf, municipio, populacao = celulas[1], celulas[2], celulas[4]
        if not (re.fullmatch(r"\d{2}", uf) and re.fullmatch(r"\d{5}", municipio)):
            continue
        nota = re.search(r"\(\d+\)", populacao)
        numero = re.sub(r"\(\d+\)|[.\s]", "", populacao)
        if not numero.isdigit():
            raise ValueError(f"população ilegível em {uf}{municipio}: {populacao!r}")
        com_nota += 1 if nota else 0
        valores[uf + municipio] = int(numero)
    return valores, com_nota


def conferir_sidra(serie: dict, municipios: list[dict], pasta: Path | None) -> dict:
    amostra = sorted(municipios, key=lambda m: hashlib.sha256(f"{SEMENTE}:{m['codigo']}".encode()).hexdigest())[:AMOSTRA]
    anos = sorted(serie["valores"])
    comparados = iguais = 0
    diferentes = []
    for m in amostra:
        codigo = m["codigo"]
        linhas = json.loads(pedir(SIDRA.format(municipio=codigo), pasta / f"sidra_{codigo}.json" if pasta else None))[1:]
        na_origem = {l["D3C"]: l["V"] for l in linhas if l["D1C"] == codigo and l["D2C"] == "9324"}
        for ano in anos:
            coletado = serie["valores"][ano].get(codigo)
            # Sem valor na coleta só confere com o sinal de ausência da SIDRA.
            esperado = str(coletado) if coletado is not None else "..."
            comparados += 1
            if na_origem.get(ano) == esperado:
                iguais += 1
            else:
                diferentes.append({"codigo": codigo, "ano": ano})
    return {"como": "um pedido por município, com todos os anos da tabela (n6/{municipio}, p/all)",
            "amostra": [m["codigo"] for m in amostra], "ufs_na_amostra": len({m["uf"] for m in amostra}),
            "anos": anos, "comparados": comparados, "iguais": iguais, "diferentes": diferentes}


def conferir_planilhas(serie: dict, anos: list[str], pasta: Path | None) -> dict:
    saida = {}
    for ano in anos:
        base = FTP.format(ano=ano)
        indice = pedir(base, pasta / f"ftp_{ano}.html" if pasta else None).decode("latin-1")
        arquivos = sorted(set(re.findall(r'href="([^"/?]+\.ods)"', indice)))
        coletado, planilhas = serie["valores"][ano], []
        for arquivo in arquivos:
            corpo = pedir(base + arquivo, pasta / f"ftp_{ano}_{arquivo}" if pasta else None)
            valores, com_nota = municipios_da_planilha(corpo)
            comuns = sorted(set(valores) & set(coletado))
            diferentes = [c for c in comuns if valores[c] != coletado[c]]
            so_na_planilha = sorted(set(valores) - set(coletado))
            planilhas.append({"arquivo": arquivo, "url": base + arquivo, "bytes": len(corpo),
                              "sha256": hashlib.sha256(corpo).hexdigest(), "municipios": len(valores),
                              "com_marca_de_nota": com_nota, "na_coleta_e_na_planilha": len(comuns),
                              "iguais": len(comuns) - len(diferentes), "diferentes": len(diferentes),
                              "codigos_diferentes": diferentes[:40],
                              # Quando as duas somas são iguais, a diferença é de território: a planilha já separa
                              # um município que a SIDRA ainda conta dentro dos outros.
                              "soma_coleta_menos_planilha_nos_diferentes": sum(coletado[c] - valores[c] for c in diferentes),
                              "soma_dos_que_so_estao_na_planilha": sum(valores[c] for c in so_na_planilha),
                              "so_na_planilha": so_na_planilha[:10],
                              "so_na_coleta": sorted(set(coletado) - set(valores))[:10]})
        saida[ano] = {"pasta": base, "planilhas": planilhas,
                      "bate_inteira_com": [p["arquivo"] for p in planilhas
                                           if p["diferentes"] == 0 and p["municipios"] == p["na_coleta_e_na_planilha"] == len(coletado)]}
    return saida


def main() -> None:
    coleta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    saida = Path(sys.argv[2])
    pasta = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    anos = sys.argv[4:] or ["2018", "2021", "2024", "2025"]
    serie = coleta["populacao_por_ano"]
    resultado = {
        "conferido_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coleta_do_pipeline": {"coletado_em": serie["coletado_em"],
                               "sha256_valores_por_ano": {a: item["sha256_valores"] for a, item in serie["anos"].items()}},
        "sidra_por_municipio": conferir_sidra(serie, coleta["municipios"], pasta),
        "planilhas_do_ftp": conferir_planilhas(serie, anos, pasta),
    }
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    s = resultado["sidra_por_municipio"]
    print(f"SIDRA por município: {s['iguais']} de {s['comparados']} iguais, em {len(s['amostra'])} municípios de {s['ufs_na_amostra']} UFs")
    for ano, item in resultado["planilhas_do_ftp"].items():
        for p in item["planilhas"]:
            print(ano, p["arquivo"], p["municipios"], "iguais", p["iguais"], "diferentes", p["diferentes"], "notas", p["com_marca_de_nota"])


if __name__ == "__main__":
    main()
