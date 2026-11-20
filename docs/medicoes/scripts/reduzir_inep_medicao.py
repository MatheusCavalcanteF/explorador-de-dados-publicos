"""Junta as execuções do workflow `INEP (medição)` num resultado só: tempos, tentativas, memória, cache e hashes.

Cada pasta, com o número da execução no nome, é o artefato `inep-medicao` dela, baixado com `gh run download`, com o
`jobs.json` da API ao lado (`gh api repos/<repo>/actions/runs/<id>/jobs > jobs.json`), de onde saem os tempos de cada
passo. O artefato só tem metadados e contagens; o resultado também.

Com `--alcance`, junta também o INEP de execuções leves do `Alcance das fontes` (opção `inep`): cada pasta, com o
número da execução no nome, é o artefato `diagnostico` dela. Esse diagnóstico não usa o intermediário versionado.

Uso: python reduzir_inep_medicao.py <saida.json> <pasta da execução> [<pasta> ...] [--local inep.json]
                                    [--alcance <pasta> ...]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def ler(caminho: Path):
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else None


def passos(jobs: dict | None) -> dict:
    if not jobs:
        return {}
    saida = {}
    for passo in jobs["jobs"][0]["steps"]:
        if passo.get("started_at") and passo.get("completed_at"):
            inicio = datetime.fromisoformat(passo["started_at"].replace("Z", "+00:00"))
            fim = datetime.fromisoformat(passo["completed_at"].replace("Z", "+00:00"))
            saida[passo["name"]] = {"s": round((fim - inicio).total_seconds()), "conclusao": passo["conclusion"]}
    return saida


def execucao(pasta: Path, identificador: str) -> dict:
    resumo, coleta = ler(pasta / "resumo.json") or {}, ler(pasta / "coleta.json") or {}
    consulta, manifesto, jobs = ler(pasta / "consulta.json") or {}, ler(pasta / "inep.json") or {}, ler(pasta / "jobs.json")
    return {
        "execucao": identificador,
        "inicio": jobs["jobs"][0]["started_at"] if jobs else None,
        "sem_cache": resumo.get("sem_cache"), "cache_hit": resumo.get("cache_hit"), "chave_restaurada": resumo.get("chave_restaurada"),
        "chave": resumo.get("chave"),
        "consulta": {"s_total": consulta.get("s_total"), "pico_rss_mb": (resumo.get("consulta") or {}).get("pico_rss_mb"),
                     "tentativas": {n: p["tentativas"] for n, p in (consulta.get("pedidos") or {}).items() if p["tentativas"]},
                     "pedidos_sem_falha": sum(1 for p in (consulta.get("pedidos") or {}).values() if not p["tentativas"])},
        "coleta": {"s_total": coleta.get("s_total"), "pico_rss_mb": (resumo.get("coleta") or {}).get("pico_rss_mb"),
                   "cache": coleta.get("cache"), "avisos": coleta.get("avisos"),
                   "arquivos": {n: {k: v for k, v in a.items() if k in ("origem", "motivo", "bytes", "s_download", "tentativas",
                                                                       "s_sha256_da_copia", "s_contrato")}
                                for n, a in (coleta.get("arquivos") or {}).items()}},
        "cache_bytes": {"antes": resumo.get("cache_antes_bytes"), "depois": resumo.get("cache_depois_bytes")},
        "pico_memoria_maquina_mb": resumo.get("pico_memoria_maquina_mb"),
        "total_s": resumo.get("total_s"),
        "passos": passos(jobs),
        "maquina": coleta.get("maquina"),
        "tls": {k: manifesto.get("tls", {}).get(k) for k in ("intermediario_versionado_na_cadeia", "raizes")},
        "cadeia": [c["assunto"] for c in (manifesto.get("tls", {}).get("cadeias") or [[]])[0]],
        "sha256": {n: a["sha256"] for n, a in (manifesto.get("arquivos") or {}).items()},
        "em_relacao_a_coleta_anterior": {n: a["em_relacao_a_coleta_anterior"] for n, a in (manifesto.get("arquivos") or {}).items()},
        "contrato": {n: {k: v for k, v in a["contrato"].items() if k in ("linhas", "municipios", "total_qt_mat_bas", "colunas")}
                     for n, a in (manifesto.get("arquivos") or {}).items()},
    }


def alcance(pasta: Path) -> dict:
    """O INEP no diagnóstico leve: em que passo cada pedido parou e quanto levou. Sem IP do runner."""
    item = ler(pasta / "diagnostico.json")["fontes"]["inep"]
    resumo = lambda passos: {nome: {k: v for k, v in p.items() if k in ("ok", "ms", "erro")} for nome, p in passos.items()}
    curl = [linha.split("|")[-2].strip()[:90] for linha in (pasta / "curl.md").read_text(encoding="utf-8").splitlines()
            if "download.inep.gov.br" in linha and linha.startswith("|")]
    return {"execucao": pasta.name, "ok": item["ok"], "falhou_em": item.get("falhou_em"), "passos": resumo(item["passos"]),
            "repeticoes": {n: {"falhou_em": r.get("falhou_em"), "passos": resumo(r["passos"])}
                           for n, r in (item.get("repeticoes") or {}).items()},
            "curl": curl}


def main(argv: list[str]) -> None:
    pastas_do_alcance = []
    if "--alcance" in argv:
        i = argv.index("--alcance")
        pastas_do_alcance, argv = argv[i + 1:], argv[:i]
    local = None
    if "--local" in argv:
        i = argv.index("--local")
        local, argv = ler(Path(argv[i + 1])), argv[:i] + argv[i + 2:]
    saida = Path(argv[0])
    execucoes = [execucao(Path(pasta), Path(pasta).name) for pasta in argv[1:]]
    hashes = [e["sha256"] for e in execucoes if e["sha256"]]
    resultado = {"execucoes": execucoes,
                 "sha256_iguais_entre_execucoes": all(h == hashes[0] for h in hashes) if hashes else None,
                 "sha256_iguais_aos_da_maquina_local": (all(h == {n: a["sha256"] for n, a in local["arquivos"].items()} for h in hashes)
                                                        if local and hashes else None),
                 "alcance_leve": [alcance(Path(p)) for p in pastas_do_alcance]}
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    for e in execucoes:
        print(e["execucao"], "cache_hit", e["cache_hit"], "coleta", e["coleta"]["s_total"], "s", e["coleta"]["cache"])


if __name__ == "__main__":
    main(sys.argv[1:])
