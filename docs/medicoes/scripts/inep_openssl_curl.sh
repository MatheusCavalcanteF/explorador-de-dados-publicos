#!/usr/bin/env bash
# INEP pelo openssl e pelo curl, fora do Python: TLS 1.2 e 1.3, sem SNI, uma cifra só e a porta 80.
# Roda no runner (workflow Alcance das fontes, opção inep_tls) e numa máquina local. Sai uma tabela em
# Markdown na saída padrão. Nenhum corpo é guardado: o curl grava em /dev/null.
alvo=download.inep.gov.br
url="https://$alvo/dados_abertos/microdados_censo_escolar_2024.zip"
erro=$(mktemp)

medir() {
  local saida=0 linha
  linha=$(curl -sS -o /dev/null --connect-timeout 10 --max-time 60 \
    -w '%{remote_ip} | %{time_connect} | %{time_appconnect} | %{time_total} | %{http_code}' \
    "$@" 2> "$erro") || saida=$?
  echo "| curl $* | $linha | $saida $(tr '\n|' '  ' < "$erro" | head -c 160) |"
}

abrir() {
  local r
  r=$(timeout 20 openssl s_client -connect "$alvo:443" "$@" < /dev/null 2>&1 \
    | grep -E '^CONNECTED|Cipher is|errno|alert|reset' | head -4 | tr '\n|' '  ')
  echo "| openssl $* | $r |"
}

echo "### INEP: openssl e curl"
echo ""
echo "- $(openssl version)"
echo "- $(curl --version | head -1)"
if command -v dig > /dev/null; then
  echo "- dig: A $(dig +short A $alvo | tr '\n' ' ')/ AAAA de $alvo: $(dig +short AAAA $alvo | tr '\n' ' ')/ AAAA de public.inep.gov.br: $(dig +short AAAA public.inep.gov.br | tr '\n' ' ')"
fi
echo ""
echo "| Pedido | Resultado |"
echo "|---|---|"
abrir -tls1_2 -servername "$alvo"; sleep 2
abrir -tls1_3 -servername "$alvo"; sleep 2
abrir -tls1_2 -noservername; sleep 2
abrir -tls1_2 -cipher ECDHE-RSA-AES128-GCM-SHA256 -servername "$alvo"; sleep 2
echo ""
echo "| Pedido | IP | Conexão | TLS | Total | HTTP | Saída do curl |"
echo "|---|---|---:|---:|---:|---|---|"
medir -r 0-1023 --tlsv1.2 --tls-max 1.2 "$url"; sleep 2
medir -r 0-1023 --tlsv1.3 "$url"; sleep 2
medir -r 0-1023 "${url/https:/http:}"; sleep 2
medir -r 0-1023 "$url"

# Com ENUMERAR_CIFRAS=1: cada cifra de TLS 1.2 do OpenSSL desta máquina, uma por conexão. Vem depois dos pedidos
# acima para não cair no reset do primeiro contato, e a que não fechar nem receber alerta é tentada de novo uma vez.
if [ "${ENUMERAR_CIFRAS:-0}" = 1 ]; then
  echo ""
  echo "| Cifra TLS 1.2 | Resultado |"
  echo "|---|---|"
  for c in $(openssl ciphers -tls1_2 'ALL:COMPLEMENTOFALL' | tr ':' '\n' | grep -v '^TLS_'); do
    for tentativa in 1 2; do
      r=$(timeout 10 openssl s_client -connect "$alvo:443" -servername "$alvo" -tls1_2 -cipher "$c" < /dev/null 2>&1)
      if grep -q "Cipher is $c" <<< "$r"; then res=aceita; break
      elif grep -q "alert" <<< "$r"; then res="alerta do servidor"; break
      elif grep -q "no ciphers available" <<< "$r"; then res="este OpenSSL não oferece"; break
      else res="outro: $(grep -m1 -iE 'error|errno' <<< "$r" | cut -c1-100 | tr '|' ' ')"; fi
    done
    echo "| cifra $c | $res |"
  done
fi
rm -f "$erro"
