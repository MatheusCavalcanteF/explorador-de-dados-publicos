"""Procura em documentos do IBGE a causa da mudança de patamar da população estimada entre 2017 e 2018.

Três documentos oficiais, baixados inteiros e lidos como texto (pypdf). Para cada um, o trecho que interessa
precisa estar no texto, com os espaços normalizados:

1. a publicação das estimativas de 2017 no Diário Oficial, cujas notas comparam os totais das UFs com a
   "Projeção da População para o Brasil e Unidades da Federação 2013";
2. a de 2018, cujas notas comparam com a "Projeção da População para o Brasil e Unidades da Federação 2018";
3. o esclarecimento da Diretoria de Pesquisas do IBGE de 27/08/2021: o método das estimativas municipais usa
   as Projeções das UFs como insumo, e elas foram revistas em 2018.

E dois números da SIDRA (tabela 6579): quantas UFs e quantos municípios têm em 2018 estimativa menor que a de
2017. Só contagens e os totais das UFs, que o IBGE já publica; nenhum valor por município.

Roda da raiz do repositório:

    python docs/medicoes/scripts/conferir_quebra_2018.py docs/medicoes/resultados/quebra_2018.json
"""
import hashlib
import io
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

AGENTE = "explorador-de-dados-publicos (medicao)"
SIDRA = "https://apisidra.ibge.gov.br/values/t/6579/{nivel}/all/v/9324/p/2017,2018"
DOCUMENTOS = [
    {"id": "dou_2017",
     "titulo": "IBGE, Estimativas da população residente com data de referência em 1º de julho de 2017, como publicadas no Diário Oficial",
     "url": "https://ftp.ibge.gov.br/Estimativas_de_Populacao/Estimativas_2017/estimativa_dou_2017.pdf",
     "trechos": ["com relação a Projeção da População para o Brasil e Unidades da Federação 2013, para o ano de 2017"]},
    {"id": "dou_2018",
     "titulo": "IBGE, Estimativas da população residente com data de referência em 1º de julho de 2018, como publicadas no Diário Oficial",
     "url": "https://ftp.ibge.gov.br/Estimativas_de_Populacao/Estimativas_2018/estimativa_dou_2018_20181019.pdf",
     "trechos": ["com relação a Projeção da População para o Brasil e Unidades da Federação 2018, para o ano de 2018"]},
    {"id": "esclarecimentos_dpe_2021",
     "titulo": "IBGE, Diretoria de Pesquisas: esclarecimentos sobre as Estimativas da População dos Municípios, de 27/08/2021",
     "url": "https://www.ibge.gov.br/np_download/novoportal/documentos_institucionais/Esclarecimentos_prestados_pela_DPE_estimativas_das_populacoes_municipais_20210827.pdf",
     "trechos": ["Esse método utiliza como insumos básicos as populações obtidas das Projeções da População para o Brasil e Unidades da Federação mais recentes",
                 "As Projeções da População das Unidades da Federação, insumo básico para cálculo das Estimativas da População dos Municípios, foram revisadas e publicadas em 2018, com a atualização da componente fecundidade até o ano de 2016",
                 "Foi a primeira vez que as Projeções da População foram atualizadas a partir dos registros de nascimentos observados (e ajustados) desde 2010"]},
]


def pedir(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENTE}), timeout=120) as r:
        return r.read()


def normalizar(texto: str) -> str:
    """Sem espaço nenhum: o texto extraído do PDF quebra linhas e às vezes palavras no meio."""
    return re.sub(r"\s+", "", texto)


def documento(item: dict) -> dict:
    corpo = pedir(item["url"])
    texto = normalizar("".join(pagina.extract_text() or "" for pagina in PdfReader(io.BytesIO(corpo)).pages[:8]))
    return {"titulo": item["titulo"], "url": item["url"], "bytes": len(corpo), "sha256": hashlib.sha256(corpo).hexdigest(),
            "trechos": [{"trecho": t, "encontrado": normalizar(t) in texto} for t in item["trechos"]]}


def por_ano(nivel: str, coluna: str) -> dict[str, dict[str, int]]:
    linhas = json.loads(pedir(SIDRA.format(nivel=nivel)))[1:]
    saida: dict[str, dict[str, int]] = {}
    for linha in linhas:
        if linha["V"].isdigit():
            saida.setdefault(linha[f"{coluna}C"], {})[linha["D3C"]] = int(linha["V"])
    return saida


def main() -> None:
    destino = Path(sys.argv[1])
    ufs = {codigo: valores for codigo, valores in por_ano("n3", "D1").items()}
    municipios = {c: v for c, v in por_ano("n6", "D1").items() if set(v) == {"2017", "2018"}}
    resultado = {
        "medido_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "documentos": [documento(item) for item in DOCUMENTOS],
        "sidra": {
            "url_ufs": SIDRA.format(nivel="n3"), "url_municipios": SIDRA.format(nivel="n6"),
            "ufs": len(ufs), "ufs_com_2018_menor": sum(1 for v in ufs.values() if v["2018"] < v["2017"]),
            "totais_das_ufs": {c: v for c, v in sorted(ufs.items())},
            "municipios_com_os_dois_anos": len(municipios),
            "municipios_com_2018_menor": sum(1 for v in municipios.values() if v["2018"] < v["2017"])},
    }
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    for d in resultado["documentos"]:
        print(d["url"], [t["encontrado"] for t in d["trechos"]])
    print({k: v for k, v in resultado["sidra"].items() if k != "totais_das_ufs"})


if __name__ == "__main__":
    main()
