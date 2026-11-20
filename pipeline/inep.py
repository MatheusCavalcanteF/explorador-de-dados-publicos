"""Coleta do INEP para a F1c: microdados do Censo Escolar e Ideb por município. Só a coleta: sem tabelas,
supressão nem site.

    python -m pipeline.inep consultar --saida build/inep
        HEAD de cada arquivo de catalogo/inep.yaml, pelo cliente do INEP (pipeline/inep_cliente.py). Grava
        remoto.json, com caminho, tamanho e Last-Modified de cada arquivo e nada que mude de uma execução para outra:
        o workflow tira dele a chave do cache do Actions. A medição dos pedidos vai para consulta.json.

    python -m pipeline.inep coletar --remoto build/inep/remoto.json --cache PASTA --municipios build/coleta/ibge.json
                                    --saida build/inep [--anterior manifesto/inep.json] [--medicao build/inep-medicao]
        Usa a cópia do cache que bate em tamanho, Last-Modified e SHA-256 e baixa só o resto. Confere o contrato
        de cada arquivo e grava inep.json, o manifesto: SHA-256, tamanho, Last-Modified e contagens agregadas.

O cache não é fonte de dados: a consulta ao INEP roda antes, sempre, e sem ela nada é usado; uma cópia só vale se
o tamanho e o Last-Modified forem os que a origem diz agora e o SHA-256 bater com o índice do cache e com o
manifesto anterior. Nenhum dado dos arquivos sai daqui: o manifesto e a medição têm só metadados e contagens
(linhas, municípios, total nacional), e as mensagens de erro dizem a coluna, o número da linha e, quando é o caso,
o código do município, sem outros valores da linha.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import platform
import re
import ssl
import sys
import time
import zipfile
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

from . import inep_cliente, rede, xlsx
from .contrato import ErroDeContrato

CATALOGO = Path(__file__).resolve().parent.parent / "catalogo" / "inep.yaml"
INDICE = "indice.json"
EXEMPLOS = 10
BLOCO = 1 << 20


class ErroDeRegras(ValueError):
    """catalogo/inep.yaml contradiz as próprias regras."""


# ---------------------------------------------------------------------------------------------------------------
# Regras


def _anos(valor, onde: str) -> list[int]:
    if (not isinstance(valor, list) or not valor or any(isinstance(a, bool) or not isinstance(a, int) for a in valor)
            or valor != sorted(set(valor))):
        raise ErroDeRegras(f"{onde}: lista de anos crescente, sem repetição")
    return valor


def validar(regras: dict) -> None:
    if not isinstance(regras, dict) or regras.get("versao") != 1 or regras.get("host") != inep_cliente.HOST:
        raise ErroDeRegras(f"catalogo/inep.yaml: versão 1 e host {inep_cliente.HOST}")
    bases = regras.get("bases") or {}
    censo, ideb = bases.get("inep_censo_escolar"), bases.get("inep_ideb")
    if not censo or not ideb or set(bases) != {"inep_censo_escolar", "inep_ideb"}:
        raise ErroDeRegras("catalogo/inep.yaml: as bases são inep_censo_escolar e inep_ideb")
    _anos(censo.get("anos"), "inep_censo_escolar.anos")
    for chave in ("arquivo", "csv", "md5"):
        if "{ano}" not in str(censo.get(chave, "")):
            raise ErroDeRegras(f"inep_censo_escolar.{chave} precisa de {{ano}}")
    for chave in ("separador", "codificacao", "ano", "municipio", "chave", "total"):
        if not censo.get(chave):
            raise ErroDeRegras(f"inep_censo_escolar.{chave} vazio")
    if not censo.get("contagens") or censo["total"] not in censo["contagens"]:
        raise ErroDeRegras("inep_censo_escolar.contagens precisa listar o total")
    if not (censo.get("dependencia") or {}).get("coluna") or not censo["dependencia"].get("valores"):
        raise ErroDeRegras("inep_censo_escolar.dependencia precisa de coluna e valores")
    anos = _anos(ideb.get("anos"), "inep_ideb.anos")
    if not isinstance(ideb.get("edicao"), int) or ideb["edicao"] != anos[-1]:
        raise ErroDeRegras("inep_ideb.edicao é o último ano de inep_ideb.anos")
    if not ideb.get("arquivos") or any("{edicao}" not in str(m) for m in ideb["arquivos"].values()):
        raise ErroDeRegras("inep_ideb.arquivos: um caminho com {edicao} por etapa")
    if "{ano}" not in str(ideb.get("valor", "")) or not ideb.get("marcas") or len(ideb.get("escala") or []) != 2:
        raise ErroDeRegras("inep_ideb: valor com {ano}, marcas e escala [mínimo, máximo]")
    if not (ideb.get("rede") or {}).get("coluna") or not ideb["rede"].get("valores") or not ideb.get("uf") \
            or not ideb.get("municipio") or not isinstance(ideb.get("cabecalho_ate_a_linha"), int):
        raise ErroDeRegras("inep_ideb: uf, municipio, rede e cabecalho_ate_a_linha")


def carregar_regras(caminho: Path = CATALOGO) -> dict:
    regras = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    validar(regras)
    return regras


def esperados(regras: dict) -> list[dict]:
    """Cada arquivo que a F1c usa, na ordem da coleta."""
    censo, ideb = regras["bases"]["inep_censo_escolar"], regras["bases"]["inep_ideb"]
    saida = [{"base": "inep_censo_escolar", "ano": ano, "caminho": censo["arquivo"].format(ano=ano)} for ano in censo["anos"]]
    saida += [{"base": "inep_ideb", "etapa": etapa, "edicao": ideb["edicao"], "caminho": modelo.format(edicao=ideb["edicao"])}
              for etapa, modelo in ideb["arquivos"].items()]
    for item in saida:
        item["nome"] = item["caminho"].rsplit("/", 1)[-1]
    if len({i["nome"] for i in saida}) != len(saida):
        raise ErroDeRegras("catalogo/inep.yaml: dois arquivos com o mesmo nome")
    return saida


# ---------------------------------------------------------------------------------------------------------------
# Consulta e cache


def consultar(regras: dict, ctx: ssl.SSLContext, intermediario, pedir=None) -> tuple[dict, dict]:
    """(remoto, medição). remoto tem só o que identifica cada arquivo na origem, para a chave do cache."""
    pedir = pedir or inep_cliente.pedir
    remoto, medicao, faltam = {}, {}, []
    for item in esperados(regras):
        r = pedir(item["caminho"], ctx, intermediario, metodo="HEAD")
        medicao[item["nome"]] = {"s": r["s"], "tentativas": r["tentativas"], "cadeia": r["cadeia"]}
        if r["status"] == 404:
            faltam.append(r["url"])
            continue
        if not r.get("last_modified"):
            raise ErroDeContrato(f"{r['url']}: resposta sem Last-Modified")
        remoto[item["nome"]] = {"caminho": item["caminho"], "bytes": r["bytes"], "last_modified": r["last_modified"]}
    if faltam:
        raise ErroDeContrato(f"{len(faltam)} arquivo(s) que catalogo/inep.yaml espera respondem 404: {', '.join(faltam)}. "
                             f"Nada foi baixado.")
    return {"host": inep_cliente.HOST, "arquivos": remoto}, medicao


def tls(medicao: dict, intermediario) -> tuple[dict, list[str]]:
    """As cadeias verificadas nos pedidos e os avisos. Uma cadeia que não termina numa raiz autoassinada para."""
    cadeias = []
    for item in medicao.values():
        if item.get("cadeia") and item["cadeia"] not in cadeias:
            cadeias.append(item["cadeia"])
    for c in cadeias:
        if not c[-1]["autoassinado"]:
            raise inep_cliente.ErroDeCertificado(f"a cadeia verificada termina em {c[-1]['assunto']}, que não é raiz")
    usou = all(intermediario.sha256 in {x["sha256"] for x in c} for c in cadeias)
    avisos = [] if usou else [
        f"O INEP passou a mandar outro intermediário, verificado até a raiz ({', '.join(c[1]['assunto'] for c in cadeias if len(c) > 2)}). "
        f"O versionado ({intermediario.assunto}) não foi usado: confira se ele deve ser trocado."]
    return {"intermediario": intermediario.resumo(), "cadeias": cadeias, "intermediario_versionado_na_cadeia": usou,
            "raizes": sorted({c[-1]["assunto"] for c in cadeias})}, avisos


def _sha256(caminho: Path) -> str:
    resumo = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        while bloco := arquivo.read(BLOCO):
            resumo.update(bloco)
    return resumo.hexdigest()


def _ler_indice(cache: Path) -> dict:
    caminho = cache / INDICE
    if not caminho.exists():
        return {}
    try:
        indice = json.loads(caminho.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        indice = None
    # Índice estragado: nenhuma cópia é usada, e tudo é baixado de novo.
    return indice if isinstance(indice, dict) and all(isinstance(v, dict) for v in indice.values()) else {}


def _da_copia(destino: Path, no_indice: dict | None, remoto: dict, anterior: dict | None, medicao: dict,
              relogio) -> tuple[str | None, str | None]:
    """(SHA-256 da cópia que pode ser usada, None) ou (None, por que baixar)."""
    if no_indice is None or not destino.is_file():
        return None, "sem cópia no cache"
    if (no_indice.get("bytes"), no_indice.get("last_modified")) != (remoto["bytes"], remoto["last_modified"]):
        return None, "tamanho ou Last-Modified mudou na origem"
    if destino.stat().st_size != remoto["bytes"]:
        return None, "cópia do cache com outro tamanho"
    inicio = relogio()
    sha = _sha256(destino)
    medicao["s_sha256_da_copia"] = round(relogio() - inicio, 2)
    if sha != no_indice.get("sha256"):
        return None, "cópia do cache não confere com o SHA-256 do índice"
    if anterior and (anterior["bytes"], anterior["last_modified"]) == (remoto["bytes"], remoto["last_modified"]) \
            and anterior["sha256"] != sha:
        return None, "cópia do cache não confere com o SHA-256 do manifesto anterior"
    return sha, None


def _em_relacao(anterior: dict | None, remoto: dict, sha: str) -> str:
    if not anterior:
        return "primeira_coleta"
    if anterior["sha256"] == sha:
        return "igual"
    if (anterior["bytes"], anterior["last_modified"]) == (remoto["bytes"], remoto["last_modified"]):
        return "mudou_sem_mudar_last_modified"
    return "mudou"


# ---------------------------------------------------------------------------------------------------------------
# Contrato


class _Problemas:
    """Junta os desvios de um arquivo para um erro só, com a contagem e alguns exemplos de cada tipo."""

    def __init__(self):
        self.contagem: collections.Counter = collections.Counter()
        self.exemplos: dict[str, list] = collections.defaultdict(list)

    def __call__(self, tipo: str, exemplo) -> None:
        self.contagem[tipo] += 1
        if len(self.exemplos[tipo]) < EXEMPLOS and exemplo not in self.exemplos[tipo]:
            self.exemplos[tipo].append(exemplo)

    def levantar(self, nome: str) -> None:
        if self.contagem:
            partes = [f"{tipo}: {n} (por exemplo, {', '.join(map(str, self.exemplos[tipo]))})"
                      for tipo, n in self.contagem.items()]
            raise ErroDeContrato(f"{nome} não passa no contrato. " + "; ".join(partes))


def _nome_interno(caminho: str) -> str:
    return caminho.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _md5_declarado(conteudo: bytes, alvo: str, nome: str) -> str:
    for linha in conteudo.decode("latin-1").splitlines():
        achado = re.fullmatch(r"\s*([0-9a-fA-F]{32})\s+\*?(.+?)\s*", linha)
        if achado and achado.group(2).lower() == alvo:
            return achado.group(1).lower()
    raise ErroDeContrato(f"{nome}: o arquivo de MD5 não declara {alvo}")


def conferir_censo(caminho: Path, ano: int, regra: dict, municipios: dict[str, str]) -> dict:
    """Microdados de um ano: o CSV e o MD5 dentro do .zip, as colunas pelo nome no cabeçalho, uma linha por escola,
    o ano em todas as linhas, municípios da DTB, dependência e contagens válidas."""
    nome = caminho.name
    try:
        z = zipfile.ZipFile(caminho)
    except zipfile.BadZipFile as e:
        raise ErroDeContrato(f"{nome}: não é um .zip ({e})") from e
    with z:
        alvo_csv, alvo_md5 = regra["csv"].format(ano=ano).lower(), regra["md5"].format(ano=ano).lower()
        csvs = [m for m in z.infolist() if _nome_interno(m.filename) == alvo_csv]
        md5s = [m for m in z.infolist() if _nome_interno(m.filename) == alvo_md5]
        if len(csvs) != 1 or len(md5s) != 1:
            raise ErroDeContrato(f"{nome}: esperado um {alvo_csv} e um {alvo_md5} no .zip; achados {len(csvs)} e {len(md5s)}")
        declarado = _md5_declarado(z.read(md5s[0]), alvo_csv, nome)
        try:
            calculado = hashlib.md5()
            with z.open(csvs[0]) as bruto:
                while bloco := bruto.read(BLOCO):
                    calculado.update(bloco)
            resumo = _linhas_do_censo(z, csvs[0], ano, regra, municipios, nome)
        except (zipfile.BadZipFile, EOFError, zlib.error) as e:
            raise ErroDeContrato(f"{nome}: {alvo_csv} corrompido dentro do .zip ({e})") from e
    if calculado.hexdigest() != declarado:
        raise ErroDeContrato(f"{nome}: MD5 de {alvo_csv} é {calculado.hexdigest()}, e o .zip declara {declarado}")
    return {"csv": _nome_interno(csvs[0].filename), "bytes_csv": csvs[0].file_size, "md5": "confere com o declarado"} | resumo


def _linhas_do_censo(z: zipfile.ZipFile, membro: zipfile.ZipInfo, ano: int, regra: dict, municipios: dict[str, str],
                     nome: str) -> dict:
    problemas = _Problemas()
    with z.open(membro) as bruto:
        leitor = csv.reader(io.TextIOWrapper(bruto, encoding=regra["codificacao"], newline=""), delimiter=regra["separador"])
        cabecalho = next(leitor, None)
        if not cabecalho:
            raise ErroDeContrato(f"{nome}: CSV vazio")
        dependencia = regra["dependencia"]
        exigidas = [regra["ano"], regra["municipio"], regra["chave"], dependencia["coluna"], *regra["contagens"]]
        repetidas = sorted({c for c in cabecalho if cabecalho.count(c) > 1})
        faltam = [c for c in exigidas if c not in cabecalho]
        if faltam or repetidas:
            raise ErroDeContrato(f"{nome}: no cabeçalho, faltam {faltam} e se repetem {repetidas}")
        i_ano, i_mun, i_chave, i_dep = (cabecalho.index(c) for c in exigidas[:4])
        contagens = [(c, cabecalho.index(c)) for c in regra["contagens"]]
        i_total = cabecalho.index(regra["total"])
        colunas, texto_ano, aceitas = len(cabecalho), str(ano), set(dependencia["valores"])
        linhas, total, vistos, chaves = 0, 0, set(), set()
        for numero, linha in enumerate(leitor, start=2):
            linhas += 1
            if len(linha) != colunas:
                problemas("linha com outro número de campos", f"linha {numero}: {len(linha)} de {colunas}")
                continue
            if linha[i_ano] != texto_ano:
                problemas(f"{regra['ano']} diferente de {ano}", f"linha {numero}: {linha[i_ano][:8]!r}")
            municipio = linha[i_mun]
            if municipio in municipios:
                vistos.add(municipio)
            else:
                problemas(f"{regra['municipio']} fora da DTB", municipio[:12])
            if linha[i_dep] not in aceitas:
                problemas(f"{dependencia['coluna']} fora de {sorted(aceitas)}", f"linha {numero}: {linha[i_dep][:8]!r}")
            if linha[i_chave] in chaves:
                problemas(f"{regra['chave']} repetido", f"linha {numero}")
            chaves.add(linha[i_chave])
            for coluna, i in contagens:
                valor = linha[i]
                if valor and not (valor.isascii() and valor.isdigit()):
                    problemas(f"{coluna} que não é inteiro de 0 para cima", f"linha {numero}")
            if linha[i_total].isdigit():
                total += int(linha[i_total])
    if linhas == 0:
        problemas("CSV sem linhas de dados", "nenhuma")
    problemas.levantar(nome)
    sem_escola = sorted(set(municipios) - vistos)
    return {"colunas": colunas, "linhas": linhas, "municipios": len(vistos),
            "municipios_da_dtb_sem_linha": {"total": len(sem_escola), "codigos": sem_escola[:20]},
            f"total_{regra['total'].lower()}": total}


def conferir_ideb(caminho: Path, etapa: str, regra: dict, municipios: dict[str, str]) -> dict:
    """Planilha de uma etapa: o cabeçalho pelo nome, as colunas dos anos esperados e a edição, municípios da DTB na
    UF certa, redes conhecidas, uma linha por município e rede, e valor na escala ou marca conhecida."""
    nome = caminho.name
    ufs, problemas = set(municipios.values()), _Problemas()
    colunas_valor = {ano: regra["valor"].format(ano=ano) for ano in regra["anos"]}
    prefixo = regra["valor"].split("{ano}")[0]
    rede = regra["rede"]
    exigidas = [regra["uf"], regra["municipio"], rede["coluna"], *colunas_valor.values()]
    minimo, maximo = regra["escala"]
    indice, linha_do_cabecalho, estado = None, None, "antes"
    linhas, vistos, chaves = 0, set(), set()
    redes: collections.Counter = collections.Counter()
    por_ano = {ano: collections.Counter() for ano in regra["anos"]}
    pegar = lambda valores, coluna: valores[indice[coluna]] if indice[coluna] < len(valores) else None
    try:
        for numero, valores in xlsx.linhas(caminho):
            if indice is None:
                if numero > regra["cabecalho_ate_a_linha"]:
                    break
                if valores and valores[0] == regra["uf"]:
                    textos = [v for v in valores if isinstance(v, str)]
                    faltam = [c for c in exigidas if c not in textos]
                    repetidas = sorted({c for c in textos if textos.count(c) > 1})
                    if faltam or repetidas:
                        raise ErroDeContrato(f"{nome}: no cabeçalho da linha {numero}, faltam {faltam} e se repetem {repetidas}")
                    indice, linha_do_cabecalho = {v: i for i, v in enumerate(valores) if isinstance(v, str)}, numero
                    anos_da_planilha = sorted(int(c[len(prefixo):]) for c in indice
                                              if c.startswith(prefixo) and c[len(prefixo):].isdigit())
                    if anos_da_planilha[-1] != regra["edicao"]:
                        raise ErroDeContrato(f"{nome}: a última coluna {prefixo}* é de {anos_da_planilha[-1]}, e a edição "
                                             f"esperada é {regra['edicao']}")
                continue
            uf = pegar(valores, regra["uf"])
            if not (isinstance(uf, str) and re.fullmatch(r"[A-Z]{2}", uf)):
                if estado == "dados":
                    estado = "notas"
                continue
            if estado == "notas":
                problemas("linha de dados depois das notas", f"linha {numero}")
                continue
            estado, linhas = "dados", linhas + 1
            codigo = pegar(valores, regra["municipio"])
            codigo = str(codigo) if isinstance(codigo, int) and not isinstance(codigo, bool) else codigo
            if not isinstance(codigo, str) or codigo not in municipios:
                problemas(f"{regra['municipio']} fora da DTB", str(codigo)[:12])
            elif municipios[codigo] != uf:
                problemas(f"{regra['uf']} diferente da UF do município na DTB", f"linha {numero}: {codigo} em {uf}")
            else:
                vistos.add(codigo)
            if uf not in ufs:
                problemas(f"{regra['uf']} fora das UFs da DTB", uf)
            valor_rede = pegar(valores, rede["coluna"])
            if valor_rede not in rede["valores"]:
                problemas(f"{rede['coluna']} fora de {rede['valores']}", f"linha {numero}: {str(valor_rede)[:20]!r}")
            redes[valor_rede] += 1
            if (codigo, valor_rede) in chaves:
                problemas("município e rede repetidos", f"linha {numero}")
            chaves.add((codigo, valor_rede))
            for ano, coluna in colunas_valor.items():
                valor = pegar(valores, coluna)
                if isinstance(valor, (int, float)) and not isinstance(valor, bool):
                    if minimo <= valor <= maximo:
                        por_ano[ano]["com_valor"] += 1
                    else:
                        problemas(f"{coluna} fora de {minimo} a {maximo}", f"linha {numero}")
                elif isinstance(valor, str) and valor in regra["marcas"]:
                    por_ano[ano][valor] += 1
                else:
                    problemas(f"{coluna} com marca que o catálogo não lista", repr(valor)[:20])
    except xlsx.ErroDePlanilha as e:
        raise ErroDeContrato(f"{nome}: {e}") from e
    if indice is None:
        raise ErroDeContrato(f"{nome}: cabeçalho com {regra['uf']} não encontrado até a linha {regra['cabecalho_ate_a_linha']}")
    if linhas == 0:
        problemas("planilha sem linhas de dados", "nenhuma")
    problemas.levantar(nome)
    return {"etapa": etapa, "edicao": regra["edicao"], "cabecalho_na_linha": linha_do_cabecalho, "colunas": len(indice),
            "linhas": linhas, "municipios": len(vistos), "redes": dict(sorted(redes.items())),
            "anos": {str(ano): dict(sorted(c.items())) for ano, c in por_ano.items()}}


# ---------------------------------------------------------------------------------------------------------------
# Coleta


def _iso(data_http: str) -> str:
    return parsedate_to_datetime(data_http).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def coletar(regras: dict, remoto: dict, cache: Path, municipios: dict[str, str], anterior: dict | None,
            ctx: ssl.SSLContext, intermediario, pedir=None, relogio=time.monotonic,
            agora: datetime | None = None, consulta: dict | None = None) -> tuple[dict, dict]:
    """(manifesto, medição). Um contrato reprovado para tudo: nenhum manifesto é gravado. `consulta` são os pedidos
    de consulta.json, cuja cadeia verificada entra no manifesto junto com a dos downloads."""
    inicio_total, pedir = relogio(), pedir or inep_cliente.pedir
    lista = esperados(regras)
    if set(remoto.get("arquivos", {})) != {i["nome"] for i in lista} or remoto.get("host") != inep_cliente.HOST:
        raise ErroDeContrato("remoto.json não corresponde a catalogo/inep.yaml: rode a consulta de novo")
    cache.mkdir(parents=True, exist_ok=True)
    indice = _ler_indice(cache)
    anteriores = (anterior or {}).get("arquivos") or {}
    arquivos, medicao = {}, {}
    cadeias = {f"HEAD {n}": item for n, item in (consulta or {}).items()}
    for item in lista:
        nome, origem = item["nome"], remoto["arquivos"][item["nome"]]
        destino, med = cache / nome, {}
        sha, motivo = _da_copia(destino, indice.get(nome), origem, anteriores.get(nome), med, relogio)
        if sha is None:
            indice.pop(nome, None)
            resposta = pedir(item["caminho"], ctx, intermediario, metodo="GET", destino=destino, esperado=origem)
            sha = resposta["sha256"]
            cadeias[f"GET {nome}"] = {"cadeia": resposta["cadeia"]}
            med.update(origem="download", motivo=motivo, s_download=resposta["s"], tentativas=resposta["tentativas"])
            indice[nome] = {"caminho": item["caminho"], "bytes": origem["bytes"], "last_modified": origem["last_modified"],
                            "sha256": sha}
            _gravar(cache / INDICE, indice)
        else:
            med["origem"] = "cache"
        inicio = relogio()
        if item["base"] == "inep_censo_escolar":
            contrato = conferir_censo(destino, item["ano"], regras["bases"]["inep_censo_escolar"], municipios)
        else:
            contrato = conferir_ideb(destino, item["etapa"], regras["bases"]["inep_ideb"], municipios)
        med["s_contrato"] = round(relogio() - inicio, 2)
        med["bytes"] = origem["bytes"]
        medicao[nome] = med
        identidade = {k: item[k] for k in ("ano", "etapa", "edicao") if k in item}
        arquivos[nome] = {"base": item["base"], **identidade, "url": f"https://{inep_cliente.HOST}/{item['caminho']}",
                          "bytes": origem["bytes"], "last_modified": origem["last_modified"],
                          "modificado_na_origem": _iso(origem["last_modified"]), "sha256": sha,
                          "em_relacao_a_coleta_anterior": _em_relacao(anteriores.get(nome), origem, sha),
                          "contrato": contrato}
    # O cache guarda só os arquivos do catálogo: o que saiu dele é apagado.
    removidos = sorted(p.name for p in cache.iterdir() if p.name != INDICE and p.name not in arquivos)
    for nome in removidos:
        (cache / nome).unlink()
    _gravar(cache / INDICE, {k: v for k, v in indice.items() if k in arquivos})
    baixados = [n for n, m in medicao.items() if m["origem"] == "download"]
    resumo_tls, avisos = tls(cadeias, intermediario) if cadeias else ({"intermediario": intermediario.resumo()}, [])
    manifesto = {"coletado_em": (agora or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "origem": f"https://{inep_cliente.HOST}/", "catalogo": "catalogo/inep.yaml", "tls": resumo_tls,
                 "arquivos": arquivos}
    medicao_total = {"arquivos": medicao, "avisos": avisos,
                     "cache": {"do_cache": len(arquivos) - len(baixados), "baixados": len(baixados),
                               "bytes_baixados": sum(arquivos[n]["bytes"] for n in baixados),
                               "bytes_no_cache": sum(a["bytes"] for a in arquivos.values()), "removidos": removidos},
                     "s_total": round(relogio() - inicio_total, 2)}
    return manifesto, medicao_total


# ---------------------------------------------------------------------------------------------------------------
# Linha de comando


def _gravar(caminho: Path, valor) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(valor, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def _ler(caminho: Path):
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _pico_de_memoria_mb() -> int | None:
    try:
        import resource
    except ImportError:  # Windows
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


def _maquina() -> dict:
    return {"sistema": platform.platform(), "python": platform.python_version(), "openssl": ssl.OPENSSL_VERSION}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.inep")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("consultar").add_argument("--saida", type=Path, required=True)
    p = sub.add_parser("coletar")
    p.add_argument("--remoto", type=Path, required=True)
    p.add_argument("--cache", type=Path, required=True)
    # A coleta do IBGE (python -m pipeline coletar): os municípios da DTB, com a UF de cada um.
    p.add_argument("--municipios", type=Path, required=True)
    p.add_argument("--saida", type=Path, required=True)
    p.add_argument("--anterior", type=Path)
    p.add_argument("--medicao", type=Path)
    args = parser.parse_args(argv)
    try:
        regras = carregar_regras()
        intermediario = inep_cliente.carregar_intermediario()
        ctx = inep_cliente.contexto(intermediario)
        if args.comando == "consultar":
            inicio = time.monotonic()
            remoto, medicao = consultar(regras, ctx, intermediario)
            resumo_tls, avisos = tls(medicao, intermediario)
            _gravar(args.saida / "remoto.json", remoto)
            _gravar(args.saida / "consulta.json", {"maquina": _maquina(), "s_total": round(time.monotonic() - inicio, 2),
                                                   "tls": resumo_tls, "pedidos": medicao})
            for nome, item in medicao.items():
                falhas = ", ".join(f"{t['passo']}: {t['erro']} em {t['ms']} ms, espera {t['espera_s']} s" for t in item["tentativas"])
                print(f"{nome}: {remoto['arquivos'][nome]['bytes']} bytes, Last-Modified "
                      f"{remoto['arquivos'][nome]['last_modified']}" + (f" (antes: {falhas})" if falhas else ""))
            print(f"Cadeia: {' > '.join(c['assunto'] for c in resumo_tls['cadeias'][0])}")
        else:
            anterior = _ler(args.anterior) if args.anterior and args.anterior.exists() else None
            municipios = {m["codigo"]: m["uf"] for m in _ler(args.municipios)["municipios"]}
            consulta = args.remoto.with_name("consulta.json")
            pedidos = _ler(consulta)["pedidos"] if consulta.exists() else None
            manifesto, medicao = coletar(regras, _ler(args.remoto), args.cache, municipios, anterior, ctx, intermediario,
                                         consulta=pedidos)
            medicao["maquina"], medicao["pico_de_memoria_mb"] = _maquina(), _pico_de_memoria_mb()
            _gravar(args.saida / "inep.json", manifesto)
            if args.medicao:
                _gravar(args.medicao / "coleta.json", medicao)
            avisos = medicao["avisos"]
            for nome, item in manifesto["arquivos"].items():
                med, contrato = medicao["arquivos"][nome], item["contrato"]
                origem = "cache" if med["origem"] == "cache" else f"baixado em {med['s_download']} s ({med['motivo']})"
                print(f"{nome}: {origem}; {item['bytes']} bytes, SHA-256 {item['sha256'][:16]}...; contrato em "
                      f"{med['s_contrato']} s: {contrato['linhas']} linhas, {contrato['municipios']} municípios; "
                      f"em relação à coleta anterior: {item['em_relacao_a_coleta_anterior']}")
                if item["em_relacao_a_coleta_anterior"].startswith("mudou"):
                    avisos.append(f"{nome} mudou desde a coleta anterior ({item['em_relacao_a_coleta_anterior']})")
            cache = medicao["cache"]
            print(f"Cache: {cache['do_cache']} do cache, {cache['baixados']} baixados ({cache['bytes_baixados']} bytes); "
                  f"{medicao['s_total']} s; pico de memória {medicao['pico_de_memoria_mb']} MB")
        for aviso in avisos:
            print(f"::warning title=INEP::{aviso}")
    except (ErroDeContrato, ErroDeRegras, inep_cliente.ErroDeCertificado, rede.ErroDeRede) as e:
        print(f"::error title=INEP, {type(e).__name__}::{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
