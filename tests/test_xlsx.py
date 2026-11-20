"""Leitor mínimo de .xlsx (pipeline/xlsx.py), com planilhas montadas aqui."""
import zipfile

import pytest
from conftest import LIVRO_XLSX, coluna_xlsx, xlsx_de_teste

from pipeline import xlsx


def test_le_a_primeira_planilha_pela_relacao_com_texto_numero_e_buracos(tmp_path):
    arquivo = xlsx_de_teste(tmp_path / "a.xlsx", {1: ["Título"], 10: ["SG_UF", "CO_MUNICIPIO", None, "VL"], 11: ["RO", 1100015, None, 5.9]})
    assert list(xlsx.linhas(arquivo)) == [(1, ["Título"]), (10, ["SG_UF", "CO_MUNICIPIO", None, "VL"]),
                                          (11, ["RO", 1100015, None, 5.9])]


def test_texto_compartilhado_inclusive_com_trechos_formatados_e_fonetica(tmp_path):
    compartilhadas = ["<si><t>Pública</t></si>", "<si><r><t>Mu</t></r><r><t>nicipal</t></r><rPh><t>x</t></rPh></si>"]
    folha_extra = '<row r="2"><c r="A2" t="s"><v>0</v></c><c r="C2" t="s"><v>1</v></c><c r="D2" t="str"><v>-</v></c></row>'
    arquivo = xlsx_de_teste(tmp_path / "a.xlsx", {}, compartilhadas, extra_linhas=folha_extra)
    assert list(xlsx.linhas(arquivo)) == [(2, ["Pública", None, "Municipal", "-"])]


def test_destino_absoluto_na_relacao_e_linha_sem_numero(tmp_path):
    extra = '<row><c><v>1</v></c><c><v>2</v></c></row>'
    arquivo = xlsx_de_teste(tmp_path / "a.xlsx", {3: [True]}, alvo="/xl/worksheets/sheet1.xml", extra_linhas=extra)
    assert list(xlsx.linhas(arquivo)) == [(3, [True]), (4, [1, 2])]


def test_arquivo_que_nao_e_planilha(tmp_path):
    (tmp_path / "a.xlsx").write_bytes(b"<html>manutencao</html>")
    with pytest.raises(xlsx.ErroDePlanilha, match="não é um .xlsx"):
        list(xlsx.linhas(tmp_path / "a.xlsx"))
    with zipfile.ZipFile(tmp_path / "b.xlsx", "w") as z:
        z.writestr("xl/workbook.xml", LIVRO_XLSX)
    with pytest.raises(xlsx.ErroDePlanilha, match="falta"):
        list(xlsx.linhas(tmp_path / "b.xlsx"))


def test_colunas_de_letras():
    assert [xlsx.coluna(c) for c in ("A", "Z", "AA", "AZ", "DR")] == [0, 25, 26, 51, 121]
    assert [coluna_xlsx(i) for i in (0, 25, 26, 51, 121)] == ["A", "Z", "AA", "AZ", "DR"]
