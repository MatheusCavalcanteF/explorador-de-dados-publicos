#!/usr/bin/env bash
# Certificados de teste do cliente do INEP (tests/test_inep_cliente.py). Nenhum vale fora dos testes: as raízes são
# inventadas, e a chave da folha está aqui de propósito, porque o servidor do teste precisa dela.
#
#   raiz.pem              raiz A, autoassinada: faz o papel das raízes do sistema
#   raiz_outra.pem        raiz B, sem relação com A: um sistema que não tem a raiz A
#   intermediario.pem     emitido por A: faz o papel do intermediário da RNP versionado
#   intermediario_novo.pem emitido por A, com outra chave: o intermediário depois de uma renovação
#   folha.pem             emitida pelo intermediário, para download.inep.gov.br
#   folha_nova.pem        emitida pelo intermediário novo, com a mesma chave da folha
#   folha.key             chave das duas folhas
#
# Validade de 100 anos. Rodar de novo troca todas as chaves, e o SHA-256 do intermediário nos testes muda junto.
# Uso: bash tests/fixtures/inep_tls/gerar.sh
set -euo pipefail
# No Git Bash do Windows, "/O=Teste/CN=..." viraria um caminho de arquivo.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")"
# Pasta relativa: com MSYS_NO_PATHCONV, o openssl do Windows não acharia um /tmp.
temp=$(mktemp -d ./.temp.XXXXXX)
trap 'rm -rf "$temp"' EXIT
dias=36500
host=download.inep.gov.br

chave() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2> /dev/null; }

raiz() {
  chave "$temp/$1.key"
  openssl req -x509 -new -key "$temp/$1.key" -days "$dias" -subj "/O=Teste/CN=$2" -sha256 \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" -out "$1.pem"
}

emitir() {  # nome, CN, emissor, extensões, [chave já existente]
  local nome=$1 cn=$2 emissor=$3 ext=$4 chave_existente=${5:-}
  if [ -n "$chave_existente" ]; then cp "$chave_existente" "$temp/$nome.key"; else chave "$temp/$nome.key"; fi
  openssl req -new -key "$temp/$nome.key" -subj "/O=Teste/CN=$cn" -out "$temp/$nome.csr"
  printf '%b' "$ext" > "$temp/$nome.ext"
  openssl x509 -req -in "$temp/$nome.csr" -CA "$emissor.pem" -CAkey "$temp/$emissor.key" -CAcreateserial \
    -days "$dias" -sha256 -extfile "$temp/$nome.ext" -out "$nome.pem" 2> /dev/null
}

CA="basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,digitalSignature,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n"
FOLHA="basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:$host\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n"

raiz raiz "Raiz de teste A"
raiz raiz_outra "Raiz de teste B"
emitir intermediario "Intermediario de teste 2025" raiz "$CA"
emitir intermediario_novo "Intermediario de teste 2026" raiz "$CA"
emitir folha "$host" intermediario "$FOLHA"
cp "$temp/folha.key" folha.key
emitir folha_nova "$host" intermediario_novo "$FOLHA" "$temp/folha.key"
rm -f ./*.srl
ls -l
