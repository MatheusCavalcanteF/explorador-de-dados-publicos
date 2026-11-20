"""Cliente HTTPS do INEP (rota 1 da F1c): verificação completa e nova tentativa limitada depois do reset.

Medido em 20/11/2026 (INEP no runner, em docs/plano/ci-e-historico.md), e é isso que o cliente resolve:

**A cadeia.** O servidor manda só o certificado `*.inep.gov.br`, sem o intermediário `RNP ICPEdu GR46 OV TLS CA
2025`. O Windows completa a cadeia sozinho; o Python e o curl do Linux, não. O intermediário fica versionado em
`pipeline/certificados/`, baixado do endereço do campo AIA do certificado do servidor, e conferido aqui pelo
SHA-256 do DER, pela validade e pela cadeia no próprio handshake. Ele entra na loja só como peça para montar a
cadeia: a verificação continua até uma raiz autoassinada do sistema (a GlobalSign Root R46), confere o nome do
host e nunca é desligada. Erro de certificado não é repetido: para na hora, com o que conferir.

**O reset do primeiro contato.** Depois de um tempo sem pedidos, a primeira conexão TLS é atendida por um
equipamento na frente do servidor, que responde ao ClientHello com reset (121 ms depois, no runner; 486 ms aqui).
A conexão seguinte chega ao servidor. Por isso cada pedido tem até 3 tentativas, com espera de 2 s antes da
segunda e de 10 s antes da terceira (ESPERAS_S). As medidas por trás desses números:
  - a repetição imediata, sem espera, chegou ao servidor no runner;
  - repetindo o ClientHello depois de pausas de 0, 1, 2, 5, 10 e 30 s, todas as conexões fecharam (7 de 7 aqui,
    6 de 7 no runner, onde a pausa de 60 s levou reset de novo): a janela em que o equipamento lembra a origem
    ficou entre 30 e 60 s no runner;
  - 2 s fica dentro do que foi medido sem falha e longe dos 30 s; a terceira tentativa sai 12 s depois da primeira,
    ainda dentro da janela, e cobre uma queda que não seja a do primeiro contato.
Uma recusa de TCP como as de 07/11/2026, que não foram explicadas, esgota as 3 tentativas em cerca de 12 s e para
com erro, sem insistir. Cada tentativa fica registrada (passo, erro, tempo e espera), para medir no runner.
"""
from __future__ import annotations

import hashlib
import http.client
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import rede
from .contrato import ErroDeContrato

HOST = "download.inep.gov.br"
RAIZ_DO_REPOSITORIO = Path(__file__).resolve().parent.parent
INTERMEDIARIO = RAIZ_DO_REPOSITORIO / "pipeline" / "certificados" / "rnp-icpedu-gr46-ov-tls-ca-2025.pem"
# SHA-256 do DER do intermediário, o mesmo do "sha256 Fingerprint" do openssl. Muda só com uma troca conferida.
INTERMEDIARIO_SHA256 = "e10747d4da7bab09cba9952f019d3534cb9fba070bf13d8791b1699cd2ff59dd"
# De onde o arquivo veio: o campo AIA ("CA Issuers") do certificado *.inep.gov.br válido de 21/09/2026 a 08/04/2027.
INTERMEDIARIO_AIA = "http://secure.globalsign.com/cacert/rnpicpedugr46ovtlsca2025.crt"
ESPERAS_S = (2, 10)
TEMPO_CONEXAO_S = 15
TEMPO_LEITURA_S = 60
BLOCO = 1 << 20

# Códigos do OpenSSL que têm explicação própria (SSLCertVerificationError.verify_code).
SEM_EMISSOR_LOCAL = 20      # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
SEM_EMISSOR = 2             # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT
VENCIDO = 10                # X509_V_ERR_CERT_HAS_EXPIRED
NOME_ERRADO = 62            # X509_V_ERR_HOSTNAME_MISMATCH


class ErroDeCertificado(RuntimeError):
    """A cadeia do INEP não fecha com o intermediário versionado e as raízes do sistema. Não é repetido."""


class _Transitoria(Exception):
    """Falha que vale nova tentativa: 5xx ou corpo mais curto que o anunciado."""


# ---------------------------------------------------------------------------------------------------------------
# O intermediário versionado


def _tlv(dados: bytes, pos: int) -> tuple[int, int, int]:
    """(tag, início do conteúdo, fim do conteúdo) do elemento DER que começa em `pos`."""
    tag, tamanho, pos = dados[pos], dados[pos + 1], pos + 2
    if tamanho & 0x80:
        n = tamanho & 0x7F
        tamanho, pos = int.from_bytes(dados[pos:pos + n], "big"), pos + n
    if pos + tamanho > len(dados):
        raise ValueError("DER truncado")
    return tag, pos, pos + tamanho


def _filhos(dados: bytes, inicio: int, fim: int) -> list[tuple[int, int, int]]:
    saida = []
    while inicio < fim:
        elemento = _tlv(dados, inicio)
        saida.append(elemento)
        inicio = elemento[2]
    return saida


def _hora(tag: int, texto: str) -> datetime:
    if tag == 0x17:  # UTCTime, AAMMDDhhmmssZ
        ano = int(texto[:2])
        texto = f"{1900 + ano if ano >= 50 else 2000 + ano}{texto[2:]}"
    return datetime.strptime(texto, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)


def _nome_comum(dados: bytes, inicio: int, fim: int) -> str:
    """O CN de um Name, ou o Name inteiro em hexadecimal se não houver CN."""
    for _, a, b in _filhos(dados, inicio, fim):          # SET
        for _, c, d in _filhos(dados, a, b):              # SEQUENCE {OID, valor}
            oid, valor = _filhos(dados, c, d)[:2]
            if dados[oid[1]:oid[2]] == b"\x55\x04\x03":
                return dados[valor[1]:valor[2]].decode("utf-8", "replace")
    return dados[inicio:fim].hex()


def descrever(der: bytes) -> dict:
    """Assunto, emissor, validade e SHA-256 de um certificado em DER, sem dependência fora da biblioteca padrão."""
    try:
        _, a, b = _tlv(der, 0)
        _, c, d = _filhos(der, a, b)[0]                    # tbsCertificate
        campos = _filhos(der, c, d)
        if campos[0][0] == 0xA0:                            # versão, opcional
            campos = campos[1:]
        emissor, validade, assunto = campos[2], campos[3], campos[4]
        (t1, i1, f1), (t2, i2, f2) = _filhos(der, validade[1], validade[2])
        return {"assunto": _nome_comum(der, assunto[1], assunto[2]), "emissor": _nome_comum(der, emissor[1], emissor[2]),
                "autoassinado": der[assunto[1]:assunto[2]] == der[emissor[1]:emissor[2]],
                "valido_de": _hora(t1, der[i1:f1].decode("ascii")), "valido_ate": _hora(t2, der[i2:f2].decode("ascii")),
                "sha256": hashlib.sha256(der).hexdigest()}
    except (IndexError, ValueError) as e:
        raise ErroDeCertificado(f"certificado em DER que não se consegue ler: {e}") from e


@dataclass(frozen=True)
class Intermediario:
    arquivo: Path
    pem: str
    sha256: str
    assunto: str
    emissor: str
    valido_de: datetime
    valido_ate: datetime

    def nome_do_arquivo(self) -> str:
        try:
            return self.arquivo.resolve().relative_to(RAIZ_DO_REPOSITORIO).as_posix()
        except ValueError:
            return str(self.arquivo)

    def resumo(self) -> dict:
        return {"arquivo": self.nome_do_arquivo(), "sha256": self.sha256, "assunto": self.assunto, "emissor": self.emissor,
                "valido_de": self.valido_de.date().isoformat(), "valido_ate": self.valido_ate.date().isoformat()}


def carregar_intermediario(arquivo: Path = INTERMEDIARIO, sha256: str = INTERMEDIARIO_SHA256,
                           agora: datetime | None = None) -> Intermediario:
    """O intermediário versionado, conferido antes de qualquer pedido: existe, é um certificado só, tem o SHA-256
    esperado e está na validade. Qualquer desvio para com ErroDeCertificado."""
    arquivo = Path(arquivo)
    if not arquivo.is_file():
        raise ErroDeCertificado(
            f"O intermediário da RNP não está em {arquivo}. Sem ele a cadeia do INEP não fecha no Linux, porque o "
            f"servidor não o manda. Ele é versionado com o código: restaure o arquivo do git. A verificação não é desligada.")
    texto = arquivo.read_text(encoding="ascii", errors="replace")
    if texto.count("-----BEGIN CERTIFICATE-----") != 1:
        raise ErroDeCertificado(f"{arquivo} deveria ter exatamente um certificado PEM")
    try:
        der = ssl.PEM_cert_to_DER_cert(texto.strip() + "\n")
    except ValueError as e:
        raise ErroDeCertificado(f"{arquivo} não é um certificado PEM: {e}") from e
    dados = descrever(der)
    if dados["sha256"] != sha256:
        raise ErroDeCertificado(
            f"{arquivo} não é o intermediário conferido: SHA-256 {dados['sha256']}, esperado {sha256} "
            f"(assunto do arquivo: {dados['assunto']}). Se a troca foi de propósito, por uma renovação, confira a cadeia "
            f"do certificado novo do INEP até uma raiz do sistema e mude também INTERMEDIARIO_SHA256 em pipeline/inep_cliente.py.")
    agora = agora or datetime.now(timezone.utc)
    if not dados["valido_de"] <= agora <= dados["valido_ate"]:
        raise ErroDeCertificado(
            f"O intermediário versionado ({dados['assunto']}) vale de {dados['valido_de']:%d/%m/%Y} a "
            f"{dados['valido_ate']:%d/%m/%Y} e está fora da validade em {agora:%d/%m/%Y}. Baixe o atual pelo campo AIA "
            f"do certificado do INEP, confira a cadeia e troque o arquivo e INTERMEDIARIO_SHA256.")
    return Intermediario(arquivo, texto, dados["sha256"], dados["assunto"], dados["emissor"], dados["valido_de"],
                         dados["valido_ate"])


def contexto(intermediario: Intermediario, raizes: Path | None = None) -> ssl.SSLContext:
    """Verificação completa: raízes do sistema (ou `raizes`, nos testes), nome do host e o intermediário só como peça.

    Desde o Python 3.13, create_default_context liga VERIFY_X509_PARTIAL_CHAIN. Com ele, qualquer certificado da loja
    vira âncora, e a verificação pararia no intermediário versionado sem chegar a uma raiz. Aqui ele é desligado: a
    cadeia precisa terminar numa raiz autoassinada da loja, e um teste confere que, sem a raiz, o handshake falha.
    """
    ctx = ssl.create_default_context(cafile=str(raizes)) if raizes else ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.load_verify_locations(cadata=intermediario.pem)
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
        raise ErroDeCertificado("o contexto TLS do INEP saiu sem verificação de certificado ou de nome")
    return ctx


def explicar(erro: ssl.SSLCertVerificationError, intermediario: Intermediario, url: str) -> str:
    base = f"{url}: certificado recusado ({erro.verify_message}, código {erro.verify_code})."
    versionado = (f"o intermediário versionado ({intermediario.assunto}, SHA-256 {intermediario.sha256[:16]}..., "
                  f"válido até {intermediario.valido_ate:%d/%m/%Y}, em {intermediario.nome_do_arquivo()})")
    if erro.verify_code == SEM_EMISSOR_LOCAL:
        return (f"{base} O emissor do certificado do servidor não é {versionado} nem uma raiz do sistema. Se o INEP "
                f"renovou o certificado, o intermediário mudou: leia o campo AIA do certificado novo (o anterior apontava "
                f"para {INTERMEDIARIO_AIA}), baixe o intermediário, confira a cadeia até uma raiz do sistema e troque o "
                f"arquivo e INTERMEDIARIO_SHA256. A verificação não é desligada.")
    if erro.verify_code == SEM_EMISSOR:
        return (f"{base} A cadeia passa por {versionado}, mas não chega a nenhuma raiz da loja do sistema: falta a "
                f"raiz {intermediario.emissor}. A verificação não aceita parar no intermediário.")
    if erro.verify_code == VENCIDO:
        return f"{base} Um certificado da cadeia está vencido; confira a validade do certificado do servidor e de {versionado}."
    if erro.verify_code == NOME_ERRADO:
        return f"{base} O certificado não é de {HOST}."
    return f"{base} A verificação usa {versionado} e as raízes do sistema."


# ---------------------------------------------------------------------------------------------------------------
# Pedidos


def cadeia(objeto_tls) -> list[dict]:
    """A cadeia que o handshake verificou, da folha à raiz: assunto, emissor e SHA-256 de cada certificado."""
    saida = []
    for der in objeto_tls.get_verified_chain():
        d = descrever(der)
        saida.append({"assunto": d["assunto"], "emissor": d["emissor"], "autoassinado": d["autoassinado"],
                      "valido_ate": d["valido_ate"].date().isoformat(), "sha256": d["sha256"]})
    return saida


def _abrir(host: str, ctx: ssl.SSLContext, estado: dict):
    """Conexão TCP, handshake e uma HTTPSConnection por cima, com o passo atual em `estado`."""
    estado["passo"] = "conexao"
    sock = socket.create_connection((host, 443), timeout=TEMPO_CONEXAO_S)
    try:
        estado["passo"] = "tls"
        tls = ctx.wrap_socket(sock, server_hostname=host)
    except BaseException:
        sock.close()
        raise
    tls.settimeout(TEMPO_LEITURA_S)
    conexao = http.client.HTTPSConnection(host, timeout=TEMPO_LEITURA_S, context=ctx)
    conexao.sock = tls
    return conexao


def _tipo(e: BaseException) -> str:
    if isinstance(e, ConnectionResetError):
        return "reset"
    if isinstance(e, ConnectionRefusedError):
        return "recusa"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "tempo esgotado"
    if isinstance(e, ssl.SSLEOFError):
        return "fechou sem resposta (EOF)"
    if isinstance(e, ssl.SSLError):
        return f"TLS: {e.reason or e}"[:120]
    if isinstance(e, _Transitoria):
        return str(e)
    return f"{type(e).__name__}: {e}"[:160]


def pedir(caminho: str, ctx: ssl.SSLContext, intermediario: Intermediario, metodo: str = "HEAD",
          destino: Path | None = None, esperado: dict | None = None, abrir=_abrir, dormir=time.sleep,
          relogio=time.monotonic) -> dict:
    """HEAD ou GET de https://download.inep.gov.br/<caminho>, com até len(ESPERAS_S) + 1 tentativas.

    No GET, o corpo vai para `destino` por um arquivo .parcial, com tamanho e SHA-256, e só é renomeado inteiro.
    `esperado` ({"bytes", "last_modified"}, do HEAD) é conferido nos cabeçalhos antes de baixar: arquivo que mudou
    entre a consulta e o download para com ErroDeContrato. 404 volta para quem chamou decidir; outro status que não
    seja 200 nem 5xx para com ErroDeContrato. Certificado recusado para com ErroDeCertificado, sem nova tentativa.
    """
    url = f"https://{HOST}/{caminho}"
    tentativas: list[dict] = []
    inicio_total = relogio()
    parcial = destino.with_name(destino.name + ".parcial") if destino else None
    for numero in range(len(ESPERAS_S) + 1):
        estado = {"passo": "conexao"}
        inicio = relogio()
        conexao = None
        try:
            conexao = abrir(HOST, ctx, estado)
            verificada = cadeia(conexao.sock)
            estado["passo"] = "resposta"
            conexao.request(metodo, "/" + caminho, headers={"User-Agent": rede.AGENTE, "Accept-Encoding": "identity"})
            resposta = conexao.getresponse()
            cabecalho = lambda nome: resposta.getheader(nome)
            if resposta.status >= 500:
                raise _Transitoria(f"HTTP {resposta.status}")
            resultado = {"url": url, "status": resposta.status, "last_modified": cabecalho("Last-Modified"),
                         "etag": cabecalho("ETag"), "cadeia": verificada}
            if resposta.status == 404:
                resposta.read()
                return resultado | {"tentativas": tentativas, "s": round(relogio() - inicio_total, 3)}
            if resposta.status != 200:
                raise ErroDeContrato(f"{url}: HTTP {resposta.status}, esperado 200"
                                     + (f" (Location: {cabecalho('Location')[:200]})" if cabecalho("Location") else ""))
            anunciado = cabecalho("Content-Length")
            if anunciado is None or not anunciado.isdigit():
                raise ErroDeContrato(f"{url}: resposta sem Content-Length")
            resultado["bytes"] = int(anunciado)
            if esperado and (resultado["bytes"], resultado["last_modified"]) != (esperado["bytes"], esperado["last_modified"]):
                raise ErroDeContrato(
                    f"{url} mudou entre a consulta e o download: {resultado['bytes']} bytes e Last-Modified "
                    f"{resultado['last_modified']}, contra {esperado['bytes']} e {esperado['last_modified']} na consulta. "
                    f"Rode a coleta de novo.")
            if metodo == "HEAD":
                resposta.read()
            else:
                estado["passo"] = "corpo"
                resumo, recebido = hashlib.sha256(), 0
                with parcial.open("wb") as arquivo:
                    while bloco := resposta.read(BLOCO):
                        resumo.update(bloco)
                        recebido += len(bloco)
                        arquivo.write(bloco)
                if recebido != resultado["bytes"]:
                    raise _Transitoria(f"corpo com {recebido} de {resultado['bytes']} bytes")
                parcial.replace(destino)
                resultado["sha256"] = resumo.hexdigest()
            return resultado | {"tentativas": tentativas, "s": round(relogio() - inicio_total, 3)}
        except ssl.SSLCertVerificationError as e:
            raise ErroDeCertificado(explicar(e, intermediario, url)) from e
        except (OSError, http.client.HTTPException, _Transitoria) as e:
            espera = ESPERAS_S[numero] if numero < len(ESPERAS_S) else None
            tentativas.append({"passo": estado["passo"], "erro": _tipo(e), "ms": round((relogio() - inicio) * 1000),
                               "espera_s": espera})
            if espera is not None:
                dormir(espera)
        finally:
            if conexao is not None:
                conexao.close()
            if parcial is not None and parcial.exists():
                parcial.unlink()
    resumo = "; ".join(f"{t['passo']}: {t['erro']} em {t['ms']} ms" for t in tentativas)
    raise rede.ErroDeRede(f"{url} não respondeu após {len(tentativas)} tentativas, com esperas de "
                          f"{', '.join(map(str, ESPERAS_S))} s ({resumo})")
