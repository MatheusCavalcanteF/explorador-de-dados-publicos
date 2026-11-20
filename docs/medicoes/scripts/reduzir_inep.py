"""Junta o diagnóstico do INEP de uma máquina local e do runner num resultado só, com a comparação dos hashes.

Entradas (todas saídas de `diagnosticar_inep.py`, de `inep_openssl_curl.sh` e do passo de captura do workflow):
  --local     JSONs da máquina local (sondas e rotas; downloads), na ordem em que rodaram
  --runner    JSONs do artefato `diagnostico` do workflow (inep_tls.json, inep_baixar.json)
  --curl-local, --curl-runner   tabelas de inep_openssl_curl.sh
  --pacotes   inep_pacotes.txt do runner (tcpdump -v, só cabeçalhos)
  --segunda   diagnostico.json e curl.md de outra execução do workflow, com a opção `inep` (6 pedidos, sem download)

Sai um JSON sem corpo de resposta, sem caminho local e sem o IP interno do runner.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Com -i any, cada pacote aparece em eth0 e na interface virtual do Azure: conta só a eth0.
CABECA = re.compile(r"^(\d\d:\d\d:\d\d\.\d+) (\S+)\s+(?:In|Out)\s+IP \(.*?ttl (\d+),")
CORPO = re.compile(r"^\s+(\d+\.\d+\.\d+\.\d+)\.(\d+) > (\d+\.\d+\.\d+\.\d+)\.(\d+): Flags \[([^\]]+)\].*?length (\d+)")
FAIXA_INEP = "200.130.24."


def carregar(caminhos: list[Path]) -> dict:
    """Junta os JSONs de uma máquina: as etapas de cada um entram no mesmo dicionário."""
    junto = {}
    for caminho in caminhos:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        for chave, valor in dados.items():
            if chave == "baixar" and chave in junto:
                for arquivo, rotas in valor.items():
                    for esquema, r in rotas.items():
                        # Um download refeito substitui o que falhou; o que falhou fica nas tentativas.
                        anterior = junto[chave].setdefault(arquivo, {}).get(esquema)
                        if anterior and not anterior.get("ok"):
                            r = {**r, "tentativas": [*anterior.get("tentativas", [_resumo(anterior)]),
                                                     *r.get("tentativas", [])]}
                        junto[chave][arquivo][esquema] = r
            else:
                junto.setdefault(chave, valor)
    return junto


def _resumo(r: dict) -> dict:
    return {k: r.get(k) for k in ("em", "ok", "tipo", "bytes", "s")}


def resultado_curto(r: dict) -> dict:
    campos = ("em", "ok", "tipo", "versao", "cifra", "ms_conexao", "ms_tls", "ms_resposta", "http", "primeira_linha",
              "passo", "pausa_s")
    return {k: r[k] for k in campos if r.get(k) is not None}


def maquina(dados: dict) -> dict:
    saida = {"conferido_em": dados.get("conferido_em"), "ambiente": dados.get("ambiente"),
             "dns": dados.get("dns"), "dns_alias": dados.get("dns_alias")}
    if "sondas" in dados:
        saida["sondas"] = {k: resultado_curto(v) for k, v in dados["sondas"].items()}
        certificados = {v.get("certificado_sha256") for v in dados["sondas"].values()} - {None}
        saida["certificado_sha256"] = sorted(certificados)
    if "sequencia" in dados:
        saida["sequencia"] = [resultado_curto(r) for r in dados["sequencia"]]
    if "hosts" in dados:
        saida["hosts"] = {k: {"host": v["host"], "dns": v["dns"], "tls": resultado_curto(v.get("tls", {})),
                              "http_80": resultado_curto(v.get("http_80", {}))} for k, v in dados["hosts"].items()}
    if "rotas" in dados:
        arquivos = {}
        for nome, rotas in dados["rotas"]["arquivos"].items():
            arquivos[nome] = {esquema: {
                "head": {**{k: r["head"].get(k) for k in ("status", "tipo") if r["head"].get(k) is not None},
                         **r["head"].get("cabecalhos", {})},
                "primeiro_kb": {k: r["primeiro_kb"].get(k) for k in ("status", "tipo", "bytes", "sha256")
                                if r["primeiro_kb"].get(k) is not None}} for esquema, r in rotas.items()}
        paginas = {nome: {k: r.get(k) for k in ("url", "status", "tipo", "bytes", "links_download_inep") if r.get(k) is not None}
                   for nome, r in dados["rotas"]["paginas"].items()}
        saida["rotas"] = {"arquivos": arquivos, "paginas": paginas}
    if "baixar" in dados:
        saida["baixar"] = {nome: {esquema: {k: r.get(k) for k in ("ok", "tipo", "bytes", "sha256", "md5", "s", "etag",
                                                                    "last_modified", "tentativas") if r.get(k) is not None}
                                  for esquema, r in rotas.items()} for nome, rotas in dados["baixar"].items()}
    return saida


def pacotes(texto: str, interface: str = "eth0") -> dict:
    """Por conexão TCP com a faixa do INEP: TTL do SYN-ACK, TTL do reset e o que veio antes dele."""
    conexoes = defaultdict(lambda: {"eventos": []})
    linhas = texto.splitlines()
    for i, linha in enumerate(linhas[:-1]):
        cabeca, corpo = CABECA.match(linha), CORPO.match(linhas[i + 1])
        if not (cabeca and corpo) or cabeca.group(2) != interface:
            continue
        hora, ttl = cabeca.group(1), int(cabeca.group(3))
        origem, porta_o, destino, porta_d, flags, tamanho = corpo.groups()
        saindo = destino.startswith(FAIXA_INEP)
        servidor = f"{destino}:{porta_d}" if saindo else f"{origem}:{porta_o}"
        chave = (servidor, porta_o if saindo else porta_d)
        conexoes[chave]["eventos"].append((hora, "sai" if saindo else "entra", flags, ttl, int(tamanho)))

    def segundos(hora: str) -> float:
        return (datetime.strptime(hora, "%H:%M:%S.%f") - datetime(1900, 1, 1)).total_seconds()

    resumo = []
    for (servidor, _), c in sorted(conexoes.items(), key=lambda item: item[1]["eventos"][0][0]):
        ev = c["eventos"]
        syn_ack = [e[3] for e in ev if e[1] == "entra" and "S" in e[2]]
        resets = [e for e in ev if e[1] == "entra" and "R" in e[2]]
        primeiro_dado = next((e for e in ev if e[1] == "sai" and e[4] > 0), None)
        dados_recebidos = sum(e[4] for e in ev if e[1] == "entra")
        dados_ttl = sorted({e[3] for e in ev if e[1] == "entra" and e[4] > 0})
        fim = "reset" if resets else ("fin" if any("F" in e[2] for e in ev if e[1] == "entra") else "sem fim na captura")
        item = {"inicio": ev[0][0], "servidor": servidor, "syn_ack_ttl": sorted(set(syn_ack)),
                "ttl_dos_dados_recebidos": dados_ttl, "bytes_recebidos": dados_recebidos, "fim": fim,
                "reset_ttl": sorted({e[3] for e in resets})}
        if resets and primeiro_dado:
            item["ms_do_primeiro_envio_ao_reset"] = round((segundos(resets[0][0]) - segundos(primeiro_dado[0])) * 1000)
        resumo.append(item)
    por_servidor = defaultdict(lambda: defaultdict(int))
    for item in resumo:
        por_servidor[item["servidor"]][item["fim"]] += 1
    return {"conexoes": resumo, "por_servidor": {s: dict(v) for s, v in por_servidor.items()}}


def hashes(local: dict, runner: dict) -> dict:
    saida = {}
    for nome in sorted(set(local.get("baixar", {})) | set(runner.get("baixar", {}))):
        linha = {}
        for origem, dados in (("local", local), ("runner", runner)):
            for esquema, r in dados.get("baixar", {}).get(nome, {}).items():
                if r.get("ok"):
                    linha[f"{origem}_{esquema}"] = r["sha256"]
        saida[nome] = {"sha256": linha, "iguais": len(set(linha.values())) == 1 and len(linha) > 1,
                       "rotas_com_hash": sorted(linha)}
    return saida


def tabela(caminho: Path | None) -> list[str]:
    if not caminho:
        return []
    return [l for l in caminho.read_text(encoding="utf-8").splitlines() if l.startswith(("| openssl", "| curl", "| cifra", "- "))]


def segunda_execucao(execucao: str, diagnostico: Path, curl: Path) -> dict:
    """O INEP pelo diagnóstico do pipeline (um pedido e as repetições que ele faz) e pelo curl, noutro runner."""
    dados = json.loads(diagnostico.read_text(encoding="utf-8"))
    linhas = [l for l in curl.read_text(encoding="utf-8").splitlines() if "inep" in l]
    return {"execucao_do_workflow": execucao, "conferido_em": dados.get("conferido_em"),
            "ambiente": dados.get("ambiente"), "inep": dados["fontes"]["inep"],
            "curl_inep": linhas,
            "ibge_servicodados": {k: dados["fontes"][k].get("ok") for k in ("ibge_localidades", "ibge_agregados")}}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--local", nargs="+", type=Path, required=True)
    p.add_argument("--runner", nargs="+", type=Path, required=True)
    p.add_argument("--curl-local", type=Path)
    p.add_argument("--curl-runner", type=Path)
    p.add_argument("--pacotes", type=Path)
    p.add_argument("--execucao", required=True, help="id da execução do workflow")
    p.add_argument("--segunda", nargs=3, metavar=("EXECUCAO", "DIAGNOSTICO_JSON", "CURL_MD"))
    p.add_argument("--diagnostico-geral", type=Path, help="diagnostico.json da mesma execução de --runner")
    p.add_argument("--saida", type=Path, required=True)
    a = p.parse_args(argv)
    local, runner = carregar(a.local), carregar(a.runner)
    resultado = {
        "descricao": "INEP: variações de TLS, sequência de pedidos, hosts vizinhos, rotas https e http e hash do "
                     "download completo, numa máquina local e no runner. Sem corpo de resposta.",
        "execucao_do_workflow": a.execucao,
        "local": maquina(local), "runner": maquina(runner),
        "openssl_curl": {"local": tabela(a.curl_local), "runner": tabela(a.curl_runner)},
        "hashes": hashes(local, runner),
    }
    if a.diagnostico_geral:
        geral = json.loads(a.diagnostico_geral.read_text(encoding="utf-8"))["fontes"]
        resultado["runner"]["ibge_servicodados"] = {k: geral[k].get("ok") for k in ("ibge_localidades", "ibge_agregados")}
    if a.segunda:
        resultado["segunda_execucao"] = segunda_execucao(a.segunda[0], Path(a.segunda[1]), Path(a.segunda[2]))
    if a.pacotes:
        resultado["pacotes_runner"] = pacotes(a.pacotes.read_text(encoding="utf-8", errors="replace"))
    # LF também no Windows, para o arquivo sair igual em qualquer máquina.
    a.saida.write_bytes((json.dumps(resultado, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
