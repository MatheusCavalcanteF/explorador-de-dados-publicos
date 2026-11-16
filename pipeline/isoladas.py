"""Falha isolada por base: uma base que reprova não para o site inteiro.

Uma base isolada tem arquivos próprios no site, listados no catálogo (`isoladas`). Quando ela reprova no
contrato, ou a fonte dela não responde, a execução segue sem ela:

1. a etapa que a coleta grava o motivo em `reprovadas.json`, na pasta da coleta (`registrar`);
2. `manter` baixa do site no ar os arquivos dela e confere o SHA-256 de cada um com o manifesto da última
   publicação (`manifesto/publicado.json`). Se todos baterem, eles vão para o site novo como estão, byte a
   byte. Sem versão anterior no manifesto, com cópia que não confere ou com o site sem responder, a base sai
   do site, e o motivo vai junto;
3. `aplicar`, no fim da construção do site, põe os arquivos mantidos no lugar e registra o que foi ao ar de
   cada base, com o SHA-256 de cada arquivo. Esse registro é o `manifesto/publicado.json` seguinte;
4. o último passo do `Publicar` (`exigir`) deixa a execução vermelha, dizendo o que reprovou e o que foi ao ar.

Só falha de fonte é isolada: `ErroDeContrato` e `ErroDeRede`. Qualquer outro erro é do próprio build e continua
parando tudo, como a falha dos testes sem rede, o site acima do limite de tamanho, ou um estado que estas
funções não esperam (`ErroDeIsolamento`).

A lista de municípios, a estimativa e o Censo não são isolados: todas as páginas saem deles, e a série por
ano também (a cobertura confere com a DTB, e o último ano, com a estimativa). Se um deles falhar, nada é
publicado, e o site inteiro fica como está: é a mesma regra, aplicada a todos os arquivos de uma vez.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import projeto, rede
from .contrato import ErroDeContrato

FALHAS_DE_FONTE = (ErroDeContrato, rede.ErroDeRede)
REPROVADAS = "reprovadas.json"
MANTIDAS = "mantidas.json"
PUBLICADO = "publicado.json"
SITUACOES = ("publicada", "mantida", "fora")


class ErroDeIsolamento(RuntimeError):
    """Estado que o mecanismo não espera: é problema do build, e para tudo."""


def _ler(caminho: Path):
    return json.loads(caminho.read_text(encoding="utf-8"))


def _gravar(caminho: Path, valor) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(valor, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def bases(catalogo: dict) -> dict[str, dict]:
    """As bases isoladas do catálogo: {id: {rotulo, arquivos}}."""
    return catalogo.get("isoladas") or {}


def tentar(funcao):
    """Roda `funcao`. Falha de fonte volta como (None, reprovação); qualquer outro erro sobe."""
    try:
        return funcao(), None
    except FALHAS_DE_FONTE as erro:
        return None, {"erro": type(erro).__name__, "motivo": str(erro)}


def registrar(pasta: Path, etapa: str, reprovadas: dict[str, dict], quando: str, juntar: bool = False) -> dict:
    """Grava as bases que reprovaram nesta etapa em `reprovadas.json`, mesmo sem nenhuma: quem vem depois sabe
    que a etapa rodou. A primeira etapa começa o arquivo do zero; as seguintes juntam (`juntar`)."""
    caminho = Path(pasta) / REPROVADAS
    atual = _ler(caminho) if juntar and caminho.exists() else {}
    for base, item in reprovadas.items():
        atual[base] = item | {"etapa": etapa, "em": quando}
    _gravar(caminho, atual)
    return atual


def ler_reprovadas(pasta: Path, catalogo: dict, exigir: bool = False) -> dict:
    """As reprovações gravadas na pasta da coleta. Sem o arquivo: nenhuma, ou erro com `exigir`. Base que o
    catálogo não lista como isolada é erro: ela não poderia ter seguido sem parar o build."""
    caminho = Path(pasta) / REPROVADAS
    if not caminho.exists():
        if exigir:
            raise ErroDeIsolamento(f"{caminho} não existe: a coleta não registrou as bases que reprovaram")
        return {}
    reprovadas = _ler(caminho)
    fora_da_lista = sorted(set(reprovadas) - set(bases(catalogo)))
    if fora_da_lista:
        raise ErroDeIsolamento(f"{caminho}: {fora_da_lista} reprovaram, mas não estão em isoladas no catálogo")
    return reprovadas


def _ultima_publicada(item: dict | None) -> dict | None:
    """Do registro de uma base no manifesto, a versão que está no site, ou None se ela não está lá."""
    if not item or item.get("situacao") not in ("publicada", "mantida") or not item.get("arquivos"):
        return None
    return {chave: item.get(chave) for chave in ("coletado_em", "execucao", "arquivos")}


def _fora(porque: str, publicada: dict | None) -> dict:
    return {"situacao": "fora", "porque": porque, "ultima_publicada": publicada}


def _manter_base(base: str, regra: dict, anterior: dict | None, destino: Path, pedir, site: str, marca: str) -> dict:
    publicada = _ultima_publicada(anterior)
    if publicada is None:
        anterior_fora = (anterior or {}).get("ultima_publicada")
        porque = ("o manifesto da última publicação não tem versão desta base no site"
                  + (" (ela já tinha saído do site)" if anterior_fora else ""))
        return _fora(porque, anterior_fora)
    if sorted(publicada["arquivos"]) != sorted(regra["arquivos"]):
        return _fora(f"os arquivos que o manifesto registra ({sorted(publicada['arquivos'])}) não são os que o catálogo "
                     f"lista hoje ({sorted(regra['arquivos'])})", publicada)
    baixados = {}
    for caminho in regra["arquivos"]:
        esperado, url = publicada["arquivos"][caminho], site + caminho
        try:
            r = pedir(url + marca, descomprimir=False)
        except rede.ErroDeRede as erro:
            return _fora(f"o site não respondeu para {caminho} ({erro})", publicada)
        if r.status != 200:
            return _fora(f"o site respondeu HTTP {r.status} para {url}", publicada)
        recebido = rede.sha256(r.corpo)
        if recebido != esperado["sha256"] or len(r.corpo) != esperado["bytes"]:
            return _fora(f"a cópia de {caminho} no site não confere com o manifesto: tem {len(r.corpo)} bytes e SHA-256 "
                         f"{recebido}, e o manifesto registra {esperado['bytes']} bytes e SHA-256 {esperado['sha256']}",
                         publicada)
        baixados[caminho] = r.corpo
    # Só grava depois de conferir todos: nunca fica metade de uma base na pasta.
    for caminho, corpo in baixados.items():
        (destino / caminho).parent.mkdir(parents=True, exist_ok=True)
        (destino / caminho).write_bytes(corpo)
    return {"situacao": "mantida", **publicada}


def manter(coleta: Path, catalogo: dict, manifesto: Path, destino: Path, pedir=None, site: str | None = None,
           agora=None) -> dict:
    """Para cada base que reprovou nesta execução, a versão dela que está no site, baixada de lá e conferida com o
    manifesto da última publicação. Grava os arquivos e `mantidas.json` em `destino`, que começa vazio.

    Sem reprovação, não pede nada ao site. `manifesto` ausente é a primeira publicação com o mecanismo: nenhuma
    base tem versão anterior registrada.
    """
    reprovadas = ler_reprovadas(coleta, catalogo, exigir=True)
    destino = Path(destino)
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    anteriores = (_ler(manifesto).get("bases") or {}) if Path(manifesto).exists() else {}
    instante = agora or datetime.now(timezone.utc)
    marca = f"?manter={instante:%Y%m%dT%H%M%S}"
    pedir, site = pedir or rede.pedir, site or projeto.SITE
    resultado = {base: _manter_base(base, bases(catalogo)[base], anteriores.get(base), destino, pedir, site, marca)
                 for base in sorted(reprovadas)}
    _gravar(destino / MANTIDAS, {"consultado_em": instante.strftime("%Y-%m-%dT%H:%M:%SZ"), "bases": resultado})
    return resultado


def aplicar(saida: Path, catalogo: dict, coletado_em: str, execucao: str, reprovadas: dict,
            mantidos: Path | None) -> dict:
    """Depois de o site ser construído: os arquivos de cada base reprovada vêm da versão mantida, ou ficam de
    fora, e o registro diz o que foi ao ar de cada base isolada, com o SHA-256 de cada arquivo.

    `mantidos` é a pasta de `manter`. Com ela (o `Publicar`), toda base que não reprovou precisa ter sido
    construída, e toda que reprovou precisa ter passado por `manter`. Sem ela (build local), base sem arquivos
    fica sem registro, e base reprovada fica de fora.
    """
    saida = Path(saida)
    mantidas = None
    if mantidos is not None:
        caminho = Path(mantidos) / MANTIDAS
        if not caminho.exists():
            raise ErroDeIsolamento(f"{caminho} não existe: o passo que mantém as bases reprovadas não rodou")
        mantidas = _ler(caminho)["bases"]
        if sorted(mantidas) != sorted(reprovadas):
            raise ErroDeIsolamento(f"{caminho} trata de {sorted(mantidas)}, mas reprovaram {sorted(reprovadas)}")
    registro = {}
    for base, regra in bases(catalogo).items():
        arquivos = regra["arquivos"]
        presentes = [a for a in arquivos if (saida / a).exists()]
        if base in reprovadas:
            if presentes:
                raise ErroDeIsolamento(f"{base} reprovou, mas o build gravou {presentes}")
            item = (mantidas or {}).get(base) or _fora("build local, sem consultar a versão no ar", None)
            if item["situacao"] == "mantida":
                for caminho, esperado in item["arquivos"].items():
                    corpo = (Path(mantidos) / caminho).read_bytes()
                    if rede.sha256(corpo) != esperado["sha256"]:
                        raise ErroDeIsolamento(f"{caminho} mudou entre a conferência com o manifesto e o build")
                    (saida / caminho).parent.mkdir(parents=True, exist_ok=True)
                    (saida / caminho).write_bytes(corpo)
            registro[base] = {"rotulo": regra["rotulo"], **item, "reprovada": reprovadas[base]}
            continue
        if not presentes:
            if mantidas is not None:
                raise ErroDeIsolamento(f"{base} não reprovou, e o build não gravou os arquivos dela")
            continue
        if len(presentes) != len(arquivos):
            raise ErroDeIsolamento(f"{base}: o build gravou {presentes}, e o catálogo lista {arquivos}")
        registro[base] = {"rotulo": regra["rotulo"], "situacao": "publicada", "coletado_em": coletado_em,
                          "execucao": execucao, "arquivos": {a: {"sha256": rede.sha256((saida / a).read_bytes()),
                                                                 "bytes": (saida / a).stat().st_size} for a in arquivos},
                          "reprovada": None}
    return registro


def _data(iso: str | None) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "data desconhecida"


def mensagem(base: str, item: dict) -> str:
    """O que reprovou e o que foi ao ar, numa linha, para o log e para o passo que deixa a execução vermelha."""
    reprovada = item["reprovada"]
    quem = f"{item.get('rotulo') or base} ({base}) reprovou na {reprovada['etapa']}"
    if item["situacao"] == "mantida":
        ao_ar = (f"o site saiu com a versão anterior dela, coletada em {_data(item.get('coletado_em'))}, igual byte a "
                 f"byte à registrada no manifesto")
    else:
        ao_ar = f"o site saiu sem ela, porque {item['porque']}"
    return f"{quem}, e o resto do site foi publicado; {ao_ar}. Motivo: {reprovada['motivo']}"
