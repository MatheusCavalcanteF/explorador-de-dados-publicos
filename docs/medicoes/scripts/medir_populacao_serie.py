"""Série de população estimada por município e ano na SIDRA (tabela 6579, variável 9324): o que os dados mostram.

Responde, antes de a série entrar no catálogo: que anos a tabela tem; quantos municípios têm número em cada
um; que sinais de ausência aparecem; se algum código está fora da lista de municípios do projeto; se a soma
dos municípios bate com o total do Brasil da própria tabela; e, entre um ano e o seguinte disponível, quanto
cada município muda. É a última medida que mostra as quebras da série.

Roda da raiz do repositório, com a coleta do IBGE do pipeline (lista de municípios):

    python docs/medicoes/scripts/medir_populacao_serie.py build/coleta/ibge.json docs/medicoes/resultados/populacao_serie.json [pasta_das_respostas]

Com a pasta, as respostas ficam guardadas e uma segunda rodada não pede de novo.
"""
import hashlib
import json
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://apisidra.ibge.gov.br/values/t/6579/{nivel}/all/v/9324/p/{periodo}"
AGENTE = "explorador-de-dados-publicos (medicao)"


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


def faixa(valores: list[float], limites=(1, 2, 5, 10, 20)) -> dict:
    """Quantos municípios mudaram mais do que cada limite, em módulo, e os percentis da mudança."""
    modulo = sorted(abs(v) for v in valores)
    return {"municipios": len(valores),
            "mediana_pct": round(statistics.median(valores), 2),
            "mediana_do_modulo_pct": round(statistics.median(modulo), 2),
            "p90_do_modulo_pct": round(modulo[int(len(modulo) * 0.9)], 2),
            "p99_do_modulo_pct": round(modulo[int(len(modulo) * 0.99)], 2),
            "caem": sum(1 for v in valores if v < 0),
            **{f"acima_de_{limite}_pct": sum(1 for v in modulo if v > limite) for limite in limites}}


def main() -> None:
    coleta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    saida = Path(sys.argv[2])
    pasta = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    lista = {m["codigo"] for m in coleta["municipios"]}
    inicio = datetime.now(timezone.utc)

    # Os períodos saem da própria tabela, no nível Brasil: nenhum ano é suposto.
    brasil = json.loads(pedir(BASE.format(nivel="n1", periodo="all"), pasta / "brasil.json" if pasta else None))
    total_brasil = {linha["D3C"]: linha["V"] for linha in brasil[1:]}
    anos = sorted(total_brasil)

    por_ano, valores = {}, {}
    for ano in anos:
        url = BASE.format(nivel="n6", periodo=ano)
        corpo = pedir(url, pasta / f"{ano}.json" if pasta else None)
        linhas = json.loads(corpo)[1:]
        numeros = {l["D1C"]: int(l["V"]) for l in linhas if l["V"].isdigit() and int(l["V"]) > 0}
        sinais: dict[str, int] = {}
        for l in linhas:
            if l["D1C"] not in numeros:
                sinais[l["V"]] = sinais.get(l["V"], 0) + 1
        codigos = {l["D1C"] for l in linhas}
        soma = sum(numeros.values())
        valores[ano] = numeros
        por_ano[ano] = {
            "url": url, "bytes": len(corpo), "sha256": hashlib.sha256(corpo).hexdigest(),
            "linhas": len(linhas), "codigos_repetidos": len(linhas) - len(codigos),
            "com_numero": len(numeros), "sinais_de_ausencia": sinais,
            "fora_da_lista_do_projeto": sorted(codigos - lista),
            "da_lista_sem_numero": len(lista - set(numeros)),
            "soma_dos_municipios": soma, "total_brasil_na_tabela": int(total_brasil[ano]),
            "soma_menos_total_brasil": soma - int(total_brasil[ano]),
            "unidade": sorted({l["MN"] for l in linhas}), "variavel": sorted({l["D2C"] for l in linhas}),
        }

    transicoes = []
    for antes, depois in zip(anos, anos[1:]):
        comuns = sorted(set(valores[antes]) & set(valores[depois]))
        passo = int(depois) - int(antes)
        # Por ano de distância, para comparar um salto de três anos com um de um.
        mudancas = [((valores[depois][c] / valores[antes][c]) ** (1 / passo) - 1) * 100 for c in comuns]
        transicoes.append({"de": antes, "ate": depois, "anos_de_distancia": passo,
                           "variacao_brasil_pct": round((int(total_brasil[depois]) / int(total_brasil[antes]) - 1) * 100, 2),
                           "por_municipio_ao_ano": faixa(mudancas)})

    resultado = {
        "medido_em": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tabela": 6579, "variavel": 9324,
        "lista_de_municipios": {"origem": "coleta do pipeline (DTB)", "periodo": (coleta["fontes"].get("dtb") or {}).get("periodo"),
                                "municipios": len(lista)},
        "anos_na_tabela": anos,
        "anos_ausentes_entre_o_primeiro_e_o_ultimo": [str(a) for a in range(int(anos[0]), int(anos[-1]) + 1)
                                                      if str(a) not in anos],
        "por_ano": por_ano,
        "transicoes": transicoes,
    }
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    for ano in anos:
        p = por_ano[ano]
        print(ano, p["com_numero"], p["sinais_de_ausencia"], len(p["fora_da_lista_do_projeto"]), p["soma_menos_total_brasil"])
    for t in transicoes:
        m = t["por_municipio_ao_ano"]
        print(t["de"], t["ate"], t["variacao_brasil_pct"], m["mediana_do_modulo_pct"], m["p90_do_modulo_pct"],
              m["acima_de_5_pct"], m["caem"])


if __name__ == "__main__":
    main()
