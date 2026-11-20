"""Leitor mínimo de .xlsx, só com a biblioteca padrão: a primeira planilha, linha a linha.

Serve às planilhas do Ideb, que o pipeline lê sem as dependências do SIH. Lê texto (compartilhado, em linha ou
de fórmula), número e booleano; célula vazia ou ausente vira None. Datas saem como o número que o Excel guarda,
e o estilo é ignorado. A planilha é lida em fluxo, sem montar a árvore inteira na memória.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

PRINCIPAL = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RELACAO = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACOTE = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_REFERENCIA = re.compile(r"([A-Z]{1,3})(\d+)")


class ErroDePlanilha(ValueError):
    """O arquivo não é uma planilha que este leitor entende."""


def coluna(letras: str) -> int:
    """A, B, ..., Z, AA, ... para 0, 1, ..., 25, 26, ..."""
    numero = 0
    for letra in letras:
        numero = numero * 26 + (ord(letra) - 64)
    return numero - 1


def _primeira_planilha(z: zipfile.ZipFile) -> str:
    livro = ET.fromstring(z.read("xl/workbook.xml"))
    folhas = livro.find(f"{PRINCIPAL}sheets")
    if folhas is None or not len(folhas):
        raise ErroDePlanilha("xl/workbook.xml sem planilhas")
    alvo_id = folhas[0].get(f"{RELACAO}id")
    relacoes = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    for relacao in relacoes.iter(f"{PACOTE}Relationship"):
        if relacao.get("Id") == alvo_id:
            alvo = relacao.get("Target", "")
            # O destino pode ser relativo a xl/ ou absoluto dentro do pacote.
            return alvo.lstrip("/") if alvo.startswith("/") else posixpath.normpath(posixpath.join("xl", alvo))
    raise ErroDePlanilha(f"a primeira planilha ({alvo_id}) não está nas relações do livro")


def _textos(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    raiz = ET.fromstring(z.read("xl/sharedStrings.xml"))
    # Texto com formatação vem em vários trechos <r><t>; a fonética (<rPh>) não faz parte do valor.
    return ["".join(t.text or "" for t in [*si.findall(f"{PRINCIPAL}t"), *si.findall(f"{PRINCIPAL}r/{PRINCIPAL}t")])
            for si in raiz.findall(f"{PRINCIPAL}si")]


def _numero(texto: str):
    valor = float(texto)
    return int(valor) if valor.is_integer() and re.fullmatch(r"-?\d+", texto) else valor


def _valor(celula: ET.Element, textos: list[str]):
    tipo = celula.get("t", "n")
    if tipo == "inlineStr":
        caixa = celula.find(f"{PRINCIPAL}is")
        return "".join(t.text or "" for t in caixa.iter(f"{PRINCIPAL}t")) if caixa is not None else None
    v = celula.find(f"{PRINCIPAL}v")
    if v is None or v.text is None:
        return None
    if tipo == "s":
        return textos[int(v.text)]
    if tipo in ("str", "e"):
        return v.text
    if tipo == "b":
        return v.text == "1"
    return _numero(v.text)


def linhas(caminho: Path) -> Iterator[tuple[int, list]]:
    """(número da linha no Excel, valores da linha), na ordem do arquivo. Linha sem célula não aparece."""
    try:
        z = zipfile.ZipFile(caminho)
    except zipfile.BadZipFile as e:
        raise ErroDePlanilha(f"{Path(caminho).name} não é um .xlsx: {e}") from e
    with z:
        try:
            planilha, textos = _primeira_planilha(z), _textos(z)
        except KeyError as e:
            raise ErroDePlanilha(f"{Path(caminho).name}: falta {e} no pacote") from e
        with z.open(planilha) as arquivo:
            anterior = 0
            for _, elemento in ET.iterparse(arquivo, events=("end",)):
                if elemento.tag != f"{PRINCIPAL}row":
                    continue
                numero = int(elemento.get("r") or anterior + 1)
                valores: list = []
                for posicao, celula in enumerate(elemento.findall(f"{PRINCIPAL}c")):
                    referencia = _REFERENCIA.fullmatch(celula.get("r") or "")
                    indice = coluna(referencia.group(1)) if referencia else posicao
                    if indice >= len(valores):
                        valores.extend([None] * (indice + 1 - len(valores)))
                    valores[indice] = _valor(celula, textos)
                elemento.clear()
                anterior = numero
                yield numero, valores
