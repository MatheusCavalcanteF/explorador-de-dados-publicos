"""Cliente do INEP: o intermediário versionado, a cadeia até a raiz e as novas tentativas depois do reset.

Os handshakes são reais, em memória (ssl.MemoryBIO), sem socket, com os certificados de tests/fixtures/inep_tls
(gerar.sh): a raiz A faz o papel das raízes do sistema, e o intermediário de teste, o da RNP.
"""
import hashlib
import io
import ssl
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import inep_cliente, rede
from pipeline.contrato import ErroDeContrato
from pipeline.inep_cliente import ErroDeCertificado

FIX = Path(__file__).resolve().parent / "fixtures" / "inep_tls"
# O intermediário real vale até 19/11/2030; os testes usam uma data fixa, e a coleta confere a validade de verdade.
HOJE = datetime(2026, 11, 20, tzinfo=timezone.utc)


def der(nome: str) -> bytes:
    return ssl.PEM_cert_to_DER_cert((FIX / nome).read_text(encoding="ascii"))


def intermediario(nome: str = "intermediario.pem") -> inep_cliente.Intermediario:
    return inep_cliente.carregar_intermediario(FIX / nome, hashlib.sha256(der(nome)).hexdigest())


def apertar(ctx_cliente: ssl.SSLContext, certificados: list[str], tmp_path: Path, host: str = inep_cliente.HOST):
    """Handshake entre o cliente e um servidor que manda `certificados` (a folha primeiro). Devolve o lado do cliente."""
    cadeia = tmp_path / "servidor.pem"
    cadeia.write_text("".join((FIX / c).read_text(encoding="ascii") for c in certificados), encoding="ascii")
    servidor_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    servidor_ctx.load_cert_chain(cadeia, FIX / "folha.key")
    c_entra, c_sai, s_entra, s_sai = (ssl.MemoryBIO() for _ in range(4))
    cliente = ctx_cliente.wrap_bio(c_entra, c_sai, server_hostname=host)
    servidor = servidor_ctx.wrap_bio(s_entra, s_sai, server_side=True)
    prontos = {"cliente": False, "servidor": False}
    for _ in range(20):
        for nome, lado in (("cliente", cliente), ("servidor", servidor)):
            if not prontos[nome]:
                try:
                    lado.do_handshake()
                    prontos[nome] = True
                except ssl.SSLWantReadError:
                    pass
        s_entra.write(c_sai.read())
        c_entra.write(s_sai.read())
        if all(prontos.values()):
            return cliente
    raise AssertionError("o handshake não terminou")


class Resposta:
    def __init__(self, status=200, corpo=b"", cabecalhos=None):
        self.status, self._corpo = status, io.BytesIO(corpo)
        self._cabecalhos = {k.lower(): v for k, v in (cabecalhos or {}).items()}

    def getheader(self, nome):
        return self._cabecalhos.get(nome.lower())

    def read(self, n=-1):
        return self._corpo.read(n)


class SockFalso:
    """A cadeia que um handshake bom com as fixtures verificaria."""
    def get_verified_chain(self):
        return [der("folha.pem"), der("intermediario.pem"), der("raiz.pem")]


class Conexao:
    def __init__(self, sock, resposta):
        self.sock, self.resposta, self.pedidos, self.fechada = sock, resposta, [], False

    def request(self, metodo, caminho, headers):
        self.pedidos.append((metodo, caminho, headers))

    def getresponse(self):
        if isinstance(self.resposta, BaseException):
            raise self.resposta
        return self.resposta

    def close(self):
        self.fechada = True


def roteiro(passos):
    """Um `abrir` que segue os passos: exceção (com o passo em que ocorre) ou resposta."""
    chamadas = []

    def abrir(host, ctx, estado):
        passo = passos.pop(0)
        chamadas.append(passo)
        if isinstance(passo, tuple):
            estado["passo"] = passo[0]
            raise passo[1]
        conexao = Conexao(SockFalso(), passo)
        chamadas[-1] = conexao
        return conexao
    return abrir, chamadas


def pedir(abrir, esperas, **extra):
    return inep_cliente.pedir("dados_abertos/a.zip", ctx=None, intermediario=intermediario(), abrir=abrir,
                              dormir=esperas.append, **extra)


# --- O intermediário versionado ------------------------------------------------------------------------------


def test_o_intermediario_versionado_e_o_da_rnp_conferido_e_vale_ate_2030():
    item = inep_cliente.carregar_intermediario(agora=HOJE)
    assert item.sha256 == inep_cliente.INTERMEDIARIO_SHA256
    assert item.assunto == "RNP ICPEdu GR46 OV TLS CA 2025" and item.emissor == "GlobalSign Root R46"
    assert item.resumo()["arquivo"] == "pipeline/certificados/rnp-icpedu-gr46-ov-tls-ca-2025.pem"
    assert (item.resumo()["valido_de"], item.resumo()["valido_ate"]) == ("2025-11-19", "2030-11-19")


def test_intermediario_que_falta_para_antes_de_qualquer_pedido(tmp_path):
    with pytest.raises(ErroDeCertificado, match="não está em .*restaure o arquivo do git"):
        inep_cliente.carregar_intermediario(tmp_path / "sumiu.pem")


def test_intermediario_trocado_no_repositorio_para_pelo_sha256():
    esperado = hashlib.sha256(der("intermediario.pem")).hexdigest()
    with pytest.raises(ErroDeCertificado, match="não é o intermediário conferido.*Intermediario de teste 2026"):
        inep_cliente.carregar_intermediario(FIX / "intermediario_novo.pem", esperado)


def test_intermediario_fora_da_validade_para_com_as_datas():
    with pytest.raises(ErroDeCertificado, match="está fora da validade em 01/01/2127"):
        inep_cliente.carregar_intermediario(FIX / "intermediario.pem", hashlib.sha256(der("intermediario.pem")).hexdigest(),
                                            agora=datetime(2127, 1, 1, tzinfo=timezone.utc))


def test_arquivo_com_dois_certificados_nao_e_aceito(tmp_path):
    dois = tmp_path / "dois.pem"
    dois.write_text((FIX / "intermediario.pem").read_text() + (FIX / "raiz.pem").read_text())
    with pytest.raises(ErroDeCertificado, match="exatamente um certificado"):
        inep_cliente.carregar_intermediario(dois)


# --- A cadeia até a raiz --------------------------------------------------------------------------------------


def test_contexto_verifica_certificado_e_nome_e_nao_aceita_cadeia_parcial():
    ctx = inep_cliente.contexto(inep_cliente.carregar_intermediario(agora=HOJE))
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert not ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_com_o_intermediario_a_cadeia_fecha_ate_a_raiz(tmp_path):
    cliente = apertar(inep_cliente.contexto(intermediario(), raizes=FIX / "raiz.pem"), ["folha.pem"], tmp_path)
    cadeia = inep_cliente.cadeia(cliente)
    assert [c["assunto"] for c in cadeia] == [inep_cliente.HOST, "Intermediario de teste 2025", "Raiz de teste A"]
    assert [c["autoassinado"] for c in cadeia] == [False, False, True]
    assert cadeia[1]["sha256"] == intermediario().sha256


def test_sem_o_intermediario_a_cadeia_nao_fecha_como_no_runner(tmp_path):
    """O que o runner via em 20/11/2026: o servidor não manda o intermediário, e a raiz sozinha não basta."""
    with pytest.raises(ssl.SSLCertVerificationError) as erro:
        apertar(ssl.create_default_context(cafile=FIX / "raiz.pem"), ["folha.pem"], tmp_path)
    assert erro.value.verify_code == inep_cliente.SEM_EMISSOR_LOCAL


def test_o_intermediario_nao_vira_ancora_sem_a_raiz_no_sistema(tmp_path):
    """Com o intermediário na loja e sem a raiz A, a verificação falha: ela vai até a raiz."""
    with pytest.raises(ssl.SSLCertVerificationError) as erro:
        apertar(inep_cliente.contexto(intermediario(), raizes=FIX / "raiz_outra.pem"), ["folha.pem"], tmp_path)
    assert erro.value.verify_code == inep_cliente.SEM_EMISSOR
    # Controle: com o padrão do Python (cadeia parcial aceita), o mesmo handshake passaria parando no intermediário.
    padrao = ssl.create_default_context(cafile=FIX / "raiz_outra.pem")
    padrao.load_verify_locations(cadata=intermediario().pem)
    assert padrao.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    assert inep_cliente.cadeia(apertar(padrao, ["folha.pem"], tmp_path))[-1]["assunto"] == "Intermediario de teste 2025"


def handshake_como_abrir(tmp_path, ctx, certificados, resposta=None, host=inep_cliente.HOST):
    chamadas = []

    def abrir(_host, _ctx, estado):
        estado["passo"] = "tls"
        chamadas.append(1)
        return Conexao(apertar(ctx, certificados, tmp_path, host), resposta)
    return abrir, chamadas


def test_intermediario_renovado_no_servidor_para_com_erro_claro_e_sem_nova_tentativa(tmp_path):
    ctx = inep_cliente.contexto(intermediario(), raizes=FIX / "raiz.pem")
    abrir, chamadas = handshake_como_abrir(tmp_path, ctx, ["folha_nova.pem"])
    esperas = []
    with pytest.raises(ErroDeCertificado) as erro:
        pedir(abrir, esperas)
    texto = str(erro.value)
    assert "código 20" in texto and "renovou o certificado" in texto and inep_cliente.INTERMEDIARIO_AIA in texto
    assert "Intermediario de teste 2025" in texto and "A verificação não é desligada" in texto
    assert chamadas == [1] and esperas == []


def test_raiz_que_falta_no_sistema_para_com_erro_claro(tmp_path):
    ctx = inep_cliente.contexto(intermediario(), raizes=FIX / "raiz_outra.pem")
    abrir, _ = handshake_como_abrir(tmp_path, ctx, ["folha.pem"])
    with pytest.raises(ErroDeCertificado, match="código 2\\).*falta a raiz Raiz de teste A"):
        pedir(abrir, [])


def test_nome_de_outro_host_e_recusado(tmp_path):
    ctx = inep_cliente.contexto(intermediario(), raizes=FIX / "raiz.pem")
    abrir, _ = handshake_como_abrir(tmp_path, ctx, ["folha.pem"], host="outro.inep.gov.br")
    with pytest.raises(ErroDeCertificado, match="não é de download.inep.gov.br"):
        pedir(abrir, [])


def test_servidor_que_manda_outro_intermediario_valido_passa_e_a_cadeia_mostra_qual(tmp_path):
    """Se o INEP passar a mandar a cadeia, verificada até a raiz, o pedido passa; a coleta avisa a troca."""
    ctx = inep_cliente.contexto(intermediario(), raizes=FIX / "raiz.pem")
    cabecalhos = {"Content-Length": "3", "Last-Modified": "Wed, 08 Jul 2026 12:22:27 GMT"}
    abrir, _ = handshake_como_abrir(tmp_path, ctx, ["folha_nova.pem", "intermediario_novo.pem"], Resposta(200, b"", cabecalhos))
    resultado = pedir(abrir, [])
    assert [c["assunto"] for c in resultado["cadeia"]] == [inep_cliente.HOST, "Intermediario de teste 2026", "Raiz de teste A"]
    assert intermediario().sha256 not in {c["sha256"] for c in resultado["cadeia"]}


# --- Novas tentativas -----------------------------------------------------------------------------------------

LM = "Wed, 08 Jul 2026 12:22:27 GMT"


def test_espera_fica_dentro_da_janela_medida_no_runner():
    """Pausas de 0 a 30 s passaram no runner, e a de 60 s levou reset: todas as tentativas cabem em menos de 30 s."""
    assert inep_cliente.ESPERAS_S == (2, 10) and sum(inep_cliente.ESPERAS_S) < 30


def test_reset_do_primeiro_contato_tenta_de_novo_depois_de_2_s():
    abrir, chamadas = roteiro([("tls", ConnectionResetError(104, "Connection reset by peer")),
                               Resposta(200, b"", {"Content-Length": "33829396", "Last-Modified": LM})])
    esperas = []
    resultado = pedir(abrir, esperas)
    assert esperas == [2] and resultado["status"] == 200 and resultado["bytes"] == 33829396
    assert resultado["last_modified"] == LM and resultado["cadeia"][-1]["autoassinado"]
    assert [(t["passo"], t["erro"], t["espera_s"]) for t in resultado["tentativas"]] == [("tls", "reset", 2)]
    assert chamadas[-1].pedidos[0][:2] == ("HEAD", "/dados_abertos/a.zip")
    assert chamadas[-1].pedidos[0][2]["User-Agent"] == rede.AGENTE and chamadas[-1].fechada


def test_tres_falhas_param_com_cada_tentativa_na_mensagem():
    abrir, _ = roteiro([("tls", ConnectionResetError()), ("conexao", ConnectionRefusedError()), ("tls", TimeoutError())])
    esperas = []
    with pytest.raises(rede.ErroDeRede, match="após 3 tentativas, com esperas de 2, 10 s .*tls: reset.*conexao: recusa"
                                              ".*tls: tempo esgotado"):
        pedir(abrir, esperas)
    assert esperas == [2, 10]


def test_404_volta_sem_nova_tentativa():
    abrir, chamadas = roteiro([Resposta(404)])
    resultado = pedir(abrir, esperas := [])
    assert resultado["status"] == 404 and len(chamadas) == 1 and esperas == []


def test_5xx_tenta_de_novo():
    abrir, _ = roteiro([Resposta(503), Resposta(200, b"", {"Content-Length": "1", "Last-Modified": LM})])
    resultado = pedir(abrir, esperas := [])
    assert resultado["status"] == 200 and resultado["tentativas"][0]["erro"] == "HTTP 503" and esperas == [2]


def test_redirecionamento_para_com_o_destino_sem_nova_tentativa():
    abrir, chamadas = roteiro([Resposta(301, b"", {"Location": "http://download.inep.gov.br/dados_abertos/a.zip"})])
    with pytest.raises(ErroDeContrato, match="HTTP 301, esperado 200 \\(Location: http://download"):
        pedir(abrir, [])
    assert len(chamadas) == 1


def test_get_grava_inteiro_com_sha256_e_sem_arquivo_parcial(tmp_path):
    corpo = b"PK" + bytes(range(256)) * 9000
    abrir, _ = roteiro([Resposta(200, corpo, {"Content-Length": str(len(corpo)), "Last-Modified": LM})])
    destino = tmp_path / "a.zip"
    resultado = pedir(abrir, [], metodo="GET", destino=destino, esperado={"bytes": len(corpo), "last_modified": LM})
    assert destino.read_bytes() == corpo and resultado["sha256"] == hashlib.sha256(corpo).hexdigest()
    assert list(tmp_path.iterdir()) == [destino]


def test_corpo_incompleto_tenta_de_novo_e_nao_deixa_parcial(tmp_path):
    corpo = b"0123456789"
    abrir, _ = roteiro([Resposta(200, corpo[:5], {"Content-Length": "10", "Last-Modified": LM}),
                        Resposta(200, corpo, {"Content-Length": "10", "Last-Modified": LM})])
    destino = tmp_path / "a.zip"
    resultado = pedir(abrir, esperas := [], metodo="GET", destino=destino, esperado={"bytes": 10, "last_modified": LM})
    assert destino.read_bytes() == corpo and esperas == [2]
    assert [(t["passo"], t["erro"]) for t in resultado["tentativas"]] == [("corpo", "corpo com 5 de 10 bytes")]
    assert list(tmp_path.iterdir()) == [destino]


def test_arquivo_que_mudou_entre_a_consulta_e_o_download_para_sem_gravar(tmp_path):
    abrir, _ = roteiro([Resposta(200, b"novo", {"Content-Length": "4", "Last-Modified": "Thu, 19 Nov 2026 10:00:00 GMT"})])
    destino = tmp_path / "a.zip"
    with pytest.raises(ErroDeContrato, match="mudou entre a consulta e o download"):
        pedir(abrir, [], metodo="GET", destino=destino, esperado={"bytes": 4, "last_modified": LM})
    assert list(tmp_path.iterdir()) == []
