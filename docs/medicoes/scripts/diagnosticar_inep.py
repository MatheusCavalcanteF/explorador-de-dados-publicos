"""INEP: em que passo a conexão cai, se a queda é do TLS ou da origem do pedido, e que rota oficial alcança os arquivos.

Roda igual numa máquina local e no runner (workflow `Alcance das fontes`, com a opção `inep_tls`). Só usa a
biblioteca padrão. Nenhum corpo é gravado nem guardado em disco: sai o status, alguns cabeçalhos, o tamanho e o
SHA-256. O download completo (`--baixar`) passa o arquivo pelo hash em blocos e o descarta.

Etapas, na ordem em que rodam (a ordem importa: o primeiro pedido de uma origem pode ser tratado diferente):
  sondas    cada variação de TLS, uma por vez, numa conexão nova ao mesmo IP do download.inep.gov.br
  sequencia o ClientHello padrão repetido, com pausas crescentes, para ver se a recusa muda com o tempo
  hosts     outros hosts do INEP na mesma rede e controles fora dela
  rotas     HEAD e primeiro KB de cada arquivo por https e por http (porta 80), e as páginas oficiais
  baixar    (só com --baixar) download completo de cada arquivo pela rota pedida em --esquemas

Uso:
  python diagnosticar_inep.py --saida inep.json [--etapas sondas,sequencia,hosts,rotas]
                              [--baixar --esquemas https,http --arquivos microdados_2024 --tentativas 3]
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOST = "download.inep.gov.br"
TEMPO = 15
AGENTE = "medicao-de-alcance/1.0 (python)"
CIFRA_ACEITA = "ECDHE-RSA-AES128-GCM-SHA256"  # uma das 3 que o servidor aceitou daqui, em TLS 1.2
CABECALHOS = ("Content-Length", "Content-Range", "Last-Modified", "ETag", "Content-Type", "Location", "Server")

ARQUIVOS = {
    "microdados_2024": "/dados_abertos/microdados_censo_escolar_2024.zip",
    "sinopse_2024": "/dados_abertos/sinopses_estatisticas/sinopse_estatistica_censo_escolar_2024.zip",
    "sinopse_2025": "/dados_abertos/sinopses_estatisticas/sinopse_estatistica_censo_escolar_2025.zip",
    "ideb_2023_anos_iniciais": "/ideb/resultados/divulgacao_anos_iniciais_municipios_2023.xlsx",
    "ideb_2023_anos_finais": "/ideb/resultados/divulgacao_anos_finais_municipios_2023.xlsx",
    "ideb_2023_ensino_medio": "/ideb/resultados/divulgacao_ensino_medio_municipios_2023.xlsx",
}

# Páginas oficiais que listam os arquivos, e os catálogos onde eles aparecem.
PAGINAS = {
    "govbr_inep_microdados": "https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/microdados/censo-escolar",
    "govbr_inep_sinopses": "https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/sinopses-estatisticas/educacao-basica",
    "govbr_inep_ideb": "https://www.gov.br/inep/pt-br/areas-de-atuacao/pesquisas-estatisticas-e-indicadores/ideb/resultados",
    "dadosgov_microdados": "https://dados.gov.br/dados/conjuntos-dados/inep-microdados-do-censo-escolar-da-educacao-basica",
    "mec_pgi_ideb": "http://api.pgi.gov.br/api/1/serie/240.json",
    "mec_dados_abertos": "https://dadosabertos.mec.gov.br/",
}

# Outros hosts do INEP (mesma faixa 200.130.24.0/24) e controles fora dela.
HOSTS = {
    "inep_www": "www.inep.gov.br",
    "inep_inepdata": "inepdata.inep.gov.br",
    "inep_ideb": "ideb.inep.gov.br",
    "govbr": "www.gov.br",
    "ibge_sidra": "apisidra.ibge.gov.br",
    "ibge_servicodados": "servicodados.ibge.gov.br",
    "controle_github": "api.github.com",
}

PAUSAS_SEQUENCIA = (0, 1, 2, 5, 10, 30, 60)


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms(inicio: float) -> int:
    return round((time.monotonic() - inicio) * 1000)


def classificar(e: BaseException) -> str:
    """Nome curto do jeito que a conexão acabou. O texto completo vai junto, em `erro`."""
    if isinstance(e, ssl.SSLCertVerificationError):
        return f"certificado: {e.verify_message}"
    if isinstance(e, ConnectionResetError):
        return "reset"
    if isinstance(e, ConnectionRefusedError):
        return "recusa"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "tempo esgotado"
    if isinstance(e, ssl.SSLEOFError):
        return "fechou sem resposta (EOF)"
    if isinstance(e, ssl.SSLError):
        return f"alerta TLS: {e.reason}" if e.reason else f"TLS: {e}"[:120]
    return type(e).__name__


def falha(e: BaseException, **extra) -> dict:
    return {"ok": False, "tipo": classificar(e), "erro": f"{type(e).__name__}: {e}"[:240], **extra}


def resolver(host: str) -> dict:
    saida = {}
    for nome, familia in (("A", socket.AF_INET), ("AAAA", socket.AF_INET6)):
        try:
            saida[nome] = sorted({i[4][0] for i in socket.getaddrinfo(host, 443, familia, socket.SOCK_STREAM)})
        except socket.gaierror as e:
            saida[nome] = f"sem registro ({e.errno})"
    return saida


def conectar(ip: str, porta: int) -> tuple[socket.socket, int]:
    inicio = time.monotonic()
    sock = socket.create_connection((ip, porta), timeout=TEMPO)
    return sock, ms(inicio)


def contexto(versao: str | None = None, cifra: str | None = None, curva: str | None = None,
             verificar: bool = False) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verificar:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if versao == "1.2":
        ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    elif versao == "1.3":
        ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    if cifra:
        ctx.set_ciphers(cifra)
    if curva:
        ctx.set_ecdh_curve(curva)
    return ctx


def tentar_tls(ip: str, sni: str | None, ctx: ssl.SSLContext, caminho: str | None = "/",
               host_http: str = HOST) -> dict:
    """Conexão nova, handshake e, se fechar, um HEAD. O certificado sai só como hash."""
    try:
        sock, ms_con = conectar(ip, 443)
    except OSError as e:
        return falha(e, passo="conexao")
    inicio = time.monotonic()
    try:
        with ctx.wrap_socket(sock, server_hostname=sni) as tls:
            r = {"ok": True, "ms_conexao": ms_con, "ms_tls": ms(inicio), "versao": tls.version(),
                 "cifra": tls.cipher()[0],
                 "certificado_sha256": hashlib.sha256(tls.getpeercert(binary_form=True) or b"").hexdigest()}
            if caminho:
                pedido = f"HEAD {caminho} HTTP/1.1\r\nHost: {host_http}\r\nUser-Agent: {AGENTE}\r\nConnection: close\r\n\r\n"
                tls.sendall(pedido.encode())
                r["http"] = tls.recv(200).split(b"\r\n", 1)[0].decode("latin-1")
            return r
    except (OSError, ssl.SSLError) as e:
        return falha(e, passo="tls", ms_conexao=ms_con, ms_tls=ms(inicio))


def tentar_cru(ip: str, porta: int, dados: bytes | None, espera: float = 6) -> dict:
    """Sem TLS: abre a conexão, manda `dados` (ou nada) e vê o que volta primeiro."""
    try:
        sock, ms_con = conectar(ip, porta)
    except OSError as e:
        return falha(e, passo="conexao")
    inicio = time.monotonic()
    with sock:
        sock.settimeout(espera)
        try:
            if dados:
                sock.sendall(dados)
            resposta = sock.recv(200)
        except (socket.timeout, TimeoutError):
            return {"ok": True, "tipo": f"silêncio por {espera:g} s", "ms_conexao": ms_con}
        except OSError as e:
            return falha(e, passo="resposta", ms_conexao=ms_con, ms_resposta=ms(inicio))
    if not resposta:
        return {"ok": True, "tipo": "fechou (FIN)", "ms_conexao": ms_con, "ms_resposta": ms(inicio)}
    return {"ok": True, "tipo": "respondeu", "ms_conexao": ms_con, "ms_resposta": ms(inicio),
            "primeira_linha": resposta.split(b"\r\n", 1)[0].decode("latin-1")[:120]}


def sondas(ip: str, pausa: float) -> dict:
    head = f"HEAD / HTTP/1.0\r\nHost: {HOST}\r\nUser-Agent: {AGENTE}\r\n\r\n".encode()
    variacoes = {
        # A primeira é a mesma do fim: se só a primeira cair, a recusa é do primeiro contato, não da variação.
        "padrao_primeira": lambda: tentar_tls(ip, HOST, contexto()),
        "tcp_sem_enviar_nada": lambda: tentar_cru(ip, 443, None),
        "http_na_porta_443": lambda: tentar_cru(ip, 443, head),
        "http_na_porta_80": lambda: tentar_cru(ip, 80, head),
        "tls12": lambda: tentar_tls(ip, HOST, contexto("1.2")),
        "tls13": lambda: tentar_tls(ip, HOST, contexto("1.3")),
        "tls12_uma_cifra_p256": lambda: tentar_tls(ip, HOST, contexto("1.2", CIFRA_ACEITA, "prime256v1")),
        "tls12_sem_sni": lambda: tentar_tls(ip, None, contexto("1.2")),
        "tls12_sni_de_outro_host": lambda: tentar_tls(ip, "example.com", contexto("1.2")),
        "padrao_so_x25519": lambda: tentar_tls(ip, HOST, contexto(curva="X25519")),
        "padrao_com_verificacao": lambda: tentar_tls(ip, HOST, contexto(verificar=True)),
        "padrao_ultima": lambda: tentar_tls(ip, HOST, contexto()),
    }
    saida = {}
    for nome, funcao in variacoes.items():
        saida[nome] = {"em": agora(), **funcao()}
        print(f"  sonda {nome}: {saida[nome].get('tipo') or saida[nome].get('versao')}", flush=True)
        time.sleep(pausa)
    return saida


def sequencia(ip: str) -> list[dict]:
    saida = []
    for pausa in PAUSAS_SEQUENCIA:
        time.sleep(pausa)
        r = {"pausa_s": pausa, "em": agora(), **tentar_tls(ip, HOST, contexto(), caminho=None)}
        saida.append(r)
        print(f"  sequência +{pausa}s: {r.get('tipo') or r.get('versao')}", flush=True)
    return saida


def hosts(pausa: float) -> dict:
    saida = {}
    for nome, host in HOSTS.items():
        dns = resolver(host)
        ips = dns["A"] if isinstance(dns["A"], list) else []
        r = {"host": host, "dns": dns}
        if ips:
            r["tls"] = tentar_tls(ips[0], host, contexto(), caminho="/", host_http=host)
            time.sleep(pausa)
            r["http_80"] = tentar_cru(ips[0], 80, f"HEAD / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
        saida[nome] = r
        print(f"  host {host}: tls {r.get('tls', {}).get('tipo') or r.get('tls', {}).get('versao')}", flush=True)
        time.sleep(pausa)
    return saida


def conexao_http(esquema: str, host: str):
    if esquema == "https":
        return http.client.HTTPSConnection(host, timeout=TEMPO * 2, context=contexto())
    return http.client.HTTPConnection(host, timeout=TEMPO * 2)


def pedir(esquema: str, host: str, caminho: str, metodo: str = "GET", faixa: str | None = None) -> dict:
    inicio = time.monotonic()
    conexao = conexao_http(esquema, host)
    try:
        cab = {"User-Agent": AGENTE}
        if faixa:
            cab["Range"] = faixa
        conexao.request(metodo, caminho, headers=cab)
        resposta = conexao.getresponse()
        corpo = resposta.read() if metodo == "GET" else b""
        r = {"ok": True, "status": resposta.status, "ms": ms(inicio),
             "cabecalhos": {c: resposta.getheader(c) for c in CABECALHOS if resposta.getheader(c)}}
        if metodo == "GET":
            r["bytes"] = len(corpo)
            r["sha256"] = hashlib.sha256(corpo).hexdigest()
            r["links_download_inep"] = corpo.count(b"download.inep.gov.br")
        return r
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        return falha(e, ms=ms(inicio))
    finally:
        conexao.close()


def rotas(pausa: float, esquemas: list[str]) -> dict:
    saida = {"arquivos": {}, "paginas": {}}
    for nome, caminho in ARQUIVOS.items():
        saida["arquivos"][nome] = {}
        for esquema in esquemas:
            head = pedir(esquema, HOST, caminho, "HEAD")
            time.sleep(pausa)
            inicio = pedir(esquema, HOST, caminho, faixa="bytes=0-1023")
            for r in (head, inicio):
                r.pop("links_download_inep", None)
            saida["arquivos"][nome][esquema] = {"head": head, "primeiro_kb": inicio}
            print(f"  {nome} {esquema}: HEAD {head.get('status') or head.get('tipo')}, KB {inicio.get('status') or inicio.get('tipo')}",
                  flush=True)
            time.sleep(pausa)
    for nome, url in PAGINAS.items():
        esquema, resto = url.split("://", 1)
        host, _, caminho = resto.partition("/")
        r = pedir(esquema, host, "/" + caminho)
        saida["paginas"][nome] = {"url": url, **r}
        print(f"  página {nome}: {r.get('status') or r.get('tipo')}", flush=True)
        time.sleep(pausa)
    return saida


def baixar_uma_vez(esquema: str, caminho: str) -> dict:
    inicio = time.monotonic()
    conexao = conexao_http(esquema, HOST)
    sha, md5, total = hashlib.sha256(), hashlib.md5(), 0
    try:
        conexao.request("GET", caminho, headers={"User-Agent": AGENTE})
        resposta = conexao.getresponse()
        if resposta.status != 200:
            raise http.client.HTTPException(f"status {resposta.status}")
        esperado = int(resposta.getheader("Content-Length") or -1)
        while bloco := resposta.read(1 << 20):
            sha.update(bloco)
            md5.update(bloco)
            total += len(bloco)
        return {"ok": total == esperado, "em": agora(), "bytes": total, "content_length": esperado,
                "sha256": sha.hexdigest(), "md5": md5.hexdigest(), "s": round(time.monotonic() - inicio, 1),
                "last_modified": resposta.getheader("Last-Modified"), "etag": resposta.getheader("ETag")}
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        return falha(e, em=agora(), bytes=total, s=round(time.monotonic() - inicio, 1))
    finally:
        conexao.close()


def baixar(esquemas: list[str], arquivos: list[str], tentativas: int) -> dict:
    """Download completo, só para o hash: os blocos passam pelo SHA-256 e pelo MD5 e são descartados.
    Cada tentativa fica registrada; a seguinte só sai se a anterior falhou, 3 s depois."""
    saida = {}
    for nome in arquivos:
        saida[nome] = {}
        for esquema in esquemas:
            registro = []
            for _ in range(tentativas):
                registro.append(baixar_uma_vez(esquema, ARQUIVOS[nome]))
                if registro[-1]["ok"]:
                    break
                time.sleep(3)
            saida[nome][esquema] = {**registro[-1], "tentativas": [
                {k: r.get(k) for k in ("em", "ok", "tipo", "bytes", "s")} for r in registro]}
            r = registro[-1]
            print(f"  baixar {nome} {esquema}: {r.get('bytes')} bytes, {r.get('s')} s, ok={r.get('ok')}, "
                  f"{len(registro)} tentativa(s)", flush=True)
    return saida


def ambiente() -> dict:
    return {"sistema": platform.platform(), "python": platform.python_version(), "openssl": ssl.OPENSSL_VERSION,
            "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
            "imagem_do_runner": os.environ.get("ImageOS"), "execucao": os.environ.get("GITHUB_RUN_ID")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--saida", required=True, type=Path)
    p.add_argument("--etapas", default="sondas,sequencia,hosts,rotas")
    p.add_argument("--esquemas", default="https,http")
    p.add_argument("--baixar", action="store_true")
    p.add_argument("--arquivos", default=",".join(ARQUIVOS), help="quais arquivos baixar, separados por vírgula")
    p.add_argument("--tentativas", type=int, default=1, help="tentativas por arquivo e rota no download")
    p.add_argument("--pausa", type=float, default=2.0)
    a = p.parse_args(argv)
    etapas = [e for e in a.etapas.split(",") if e]
    esquemas = [e for e in a.esquemas.split(",") if e]

    resultado = {"conferido_em": agora(), "ambiente": ambiente(), "host": HOST, "dns": resolver(HOST),
                 "dns_alias": resolver("public.inep.gov.br")}
    ips = resultado["dns"]["A"] if isinstance(resultado["dns"]["A"], list) else []
    if not ips:
        print("DNS sem endereço IPv4 para o INEP", file=sys.stderr)
    ip = ips[0] if ips else None
    print(f"INEP em {ip}; etapas {etapas}; baixar={a.baixar} por {esquemas}", flush=True)
    if ip and "sondas" in etapas:
        resultado["sondas"] = sondas(ip, a.pausa)
    if ip and "sequencia" in etapas:
        resultado["sequencia"] = sequencia(ip)
    if "hosts" in etapas:
        resultado["hosts"] = hosts(a.pausa)
    if "rotas" in etapas:
        resultado["rotas"] = rotas(a.pausa, esquemas)
    if a.baixar:
        arquivos = [n for n in a.arquivos.split(",") if n]
        desconhecidos = sorted(set(arquivos) - set(ARQUIVOS))
        if desconhecidos:
            p.error(f"arquivos desconhecidos: {desconhecidos}")
        resultado["baixar"] = baixar(esquemas, arquivos, a.tentativas)
    resultado["terminado_em"] = agora()
    a.saida.parent.mkdir(parents=True, exist_ok=True)
    a.saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
