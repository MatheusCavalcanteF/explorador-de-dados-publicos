"""Manifesto no git: o que foi baixado, de onde, quando e com que hash, e o resultado das conferências.

Só metadados e totais. Os valores por município ficam no site e na Release, não aqui. A exceção são
as revisões da estimativa do IBGE: para cada município revisado, o valor antes e depois, por código.
Da população por ano, cada ano tem a própria URL, data de coleta e hash. Em publicado.json, o que foi ao ar
de cada base isolada (pipeline/isoladas.py), com o SHA-256 de cada arquivo.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import isoladas, populacao_por_ano

CONFERENCIAS = ("alcance", "hospedagem", "links", "leitor")


def _gravar(caminho: Path, valor) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(valor, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def resumo_ibge(coleta: dict) -> dict:
    por_uf: dict[str, dict] = {}
    for m in coleta["municipios"]:
        uf = por_uf.setdefault(m["uf"], {"municipios": 0, "populacao_estimada": 0, "populacao_censo_2022": 0})
        uf["municipios"] += 1
        uf["populacao_estimada"] += m["populacao_estimada"] or 0
        uf["populacao_censo_2022"] += m["populacao_censo"] or 0
    resumo = {"coletado_em": coleta["coletado_em"], "fontes": coleta["fontes"], "municipios": len(coleta["municipios"]),
              "por_uf": dict(sorted(por_uf.items()))}
    if "populacao_por_ano" in coleta:
        # De cada ano, de onde veio, quando e com que hash; os valores ficam no arquivo do site.
        resumo["populacao_por_ano"] = {k: v for k, v in coleta["populacao_por_ano"].items() if k != "valores"}
    return resumo


def gravar(coleta_dir: Path, destino: Path) -> list[str]:
    """`destino` é a pasta do manifesto no git, ainda com o da execução anterior. Do que vai ao ar de cada base
    isolada (publicado.json, do build), a série por ano mantida no site leva junto o registro anterior dela:
    é ele que descreve os arquivos que continuam no ar, e é com ele que a coleta seguinte compara."""
    coleta = json.loads((coleta_dir / "ibge.json").read_text(encoding="utf-8"))
    resumo = resumo_ibge(coleta)
    publicado = coleta_dir / isoladas.PUBLICADO
    if publicado.exists():
        registro = json.loads(publicado.read_text(encoding="utf-8"))
        serie = registro["bases"].get(populacao_por_ano.CRUZAMENTO) or {}
        anterior = destino / "ibge.json"
        if serie.get("situacao") == "mantida" and anterior.exists():
            antes = json.loads(anterior.read_text(encoding="utf-8")).get("populacao_por_ano")
            if antes is not None:
                resumo["populacao_por_ano"] = antes
    _gravar(destino / "ibge.json", resumo)
    gravados = ["ibge.json"]
    if publicado.exists():
        _gravar(destino / isoladas.PUBLICADO, json.loads(publicado.read_text(encoding="utf-8")))
        gravados.append(isoladas.PUBLICADO)
    for nome in CONFERENCIAS:
        origem = coleta_dir / f"{nome}.json"
        if origem.exists():
            _gravar(destino / f"{nome}.json", json.loads(origem.read_text(encoding="utf-8")))
            gravados.append(f"{nome}.json")
    return gravados


def falhas(coleta_dir: Path) -> list[str]:
    """O que deve deixar a execução vermelha: fonte sem alcance, hospedagem fora do plano, leitor divergente e
    base isolada que reprovou, com o que foi ao ar no lugar dela."""
    problemas = []
    ler = lambda nome: json.loads((coleta_dir / f"{nome}.json").read_text(encoding="utf-8"))
    if not (coleta_dir / "alcance.json").exists():
        problemas.append("Teste de alcance não rodou")
    else:
        problemas += [f"Alcance: {nome} falhou ({item.get('erro')})"
                      for nome, item in ler("alcance")["fontes"].items() if not item["ok"]]
    if not (coleta_dir / "hospedagem.json").exists():
        problemas.append("Testes de hospedagem não rodaram")
    else:
        problemas += [f"Hospedagem: {nome} não bate com o plano"
                      for nome, item in ler("hospedagem")["testes"].items() if item["bate_com_o_plano"] is False]
    if not (coleta_dir / "leitor.json").exists():
        problemas.append("Leitor em JavaScript não rodou")
    elif not ler("leitor").get("ok"):
        problemas.append("Leitor em JavaScript divergiu da coleta")
    if not (coleta_dir / isoladas.PUBLICADO).exists():
        problemas.append("O build não registrou o que foi ao ar de cada base isolada (publicado.json)")
    else:
        problemas += [isoladas.mensagem(base, item) for base, item in sorted(ler("publicado")["bases"].items())
                      if item.get("reprovada")]
    return problemas
