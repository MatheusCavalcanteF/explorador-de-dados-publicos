"""A especificação de consulta é JavaScript: os testes rodam no Node, sem navegador e sem rede, contra o catálogo real."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import catalogo

TESTE = Path(__file__).with_name("especificacao.test.cjs")


def test_especificacao_valida_no_catalogo_e_vai_e_volta_da_url(tmp_path):
    node = shutil.which("node")
    if node is None:
        # Nos runners do GitHub o Node já vem instalado: lá, faltar é erro, para o teste não ser pulado sem aviso.
        if os.environ.get("CI") or os.environ.get("EXIGIR_SIH") == "1":
            pytest.fail("Node não encontrado: os testes de web/especificacao.js não rodaram")
        pytest.skip("Node não instalado nesta máquina")
    arquivo = tmp_path / "catalogo.json"
    arquivo.write_text(json.dumps(catalogo.carregar(), ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([node, str(TESTE), str(arquivo)], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert r.returncode == 0, r.stderr
