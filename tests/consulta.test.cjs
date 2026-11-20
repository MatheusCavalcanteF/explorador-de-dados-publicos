// Testes da página de consulta (web/consulta.js), fora do navegador e sem rede: do texto do endereço até a
// consulta de modelo sobre o Parquet que o build gravou, lido pelo mesmo leitor do site, e cada recusa que a
// tela mostra no lugar do gráfico. Roda pelo pytest (tests/test_consulta.py): o primeiro argumento é o site
// construído com a chave ligada; o segundo, o catálogo inteiro, em JSON.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const E = require('../web/especificacao.js');
const L = require('../web/leitor.js');
const C = require('../web/consulta.js');

const [site, arquivoDoCatalogo] = process.argv.slice(2);
const ler = (p) => fs.readFileSync(path.join(site, p), 'utf-8');
const pagina = ler('consulta/index.html');
// O catálogo que a página leva: só o que vai para o site.
const catalogo = JSON.parse(/<script type="application\/json" id="catalogo">(.*?)<\/script>/s.exec(pagina)[1]);
const inteiro = JSON.parse(fs.readFileSync(arquivoDoCatalogo, 'utf-8'));
const serie = catalogo.cruzamentos_da_origem.find((c) => c.id === 'ibge_pop_ano');
const ficha = JSON.parse(ler(C.enderecoDaFicha(serie.arquivo)));
const nomes = new Map(JSON.parse(ler('dados/municipios.json')).map(([codigo, nome, uf]) => [codigo, { nome, uf }]));
const SANTOS = '3548500';
const HERVAL = '4206504';
const BOA = '5101837';
const SORRISO = '5107925';
const COLETADOS = [2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2024, 2025, 2026];
const copia = (x) => JSON.parse(JSON.stringify(x));
const link = (codigo) => {
  const html = ler(`municipio/${codigo}/index.html`);
  return /<p class="serie-link"><a href="\.\.\/\.\.\/consulta\/#([^"]+)">/.exec(html)[1].replace(/&amp;/g, '&');
};

const principal = async () => {
  const bytes = fs.readFileSync(path.join(site, serie.arquivo));
  const linhas = await L.parquetReadObjects({ file: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) });
  const indice = C.indexar(linhas, C.colunaDoValor(ficha));
  assert.equal(C.colunaDoValor(ficha), 'populacao');
  assert.deepEqual(C.conferir(indice, ficha), { ok: true });

  // O caminho da tela: abrir o link com a ficha, validar cada painel e, no aceito, a consulta de modelo.
  const consultar = (texto, fichas = [ficha], cat = catalogo) => {
    const leitura = C.abrir(cat, fichas, texto);
    if (!leitura.ok) return { leitura };
    return { leitura, paineis: leitura.paineis.map((v) => (v.ok
      ? { v, m: C.executar(indice, v.consulta, C.cruzamento(cat, v.cruzamento), ficha) } : { v })) };
  };
  const recusa = (texto, motivo, fichas) => {
    const r = consultar(texto, fichas);
    assert.equal(r.leitura.ok, true, texto);
    const v = r.paineis[0].v;
    assert.equal(v.ok, false, texto);
    assert.equal(v.motivo, motivo, `${texto}: ${v.motivo}`);
    assert.ok(v.mensagem.length > 20);
    // Toda sugestão vira link que abre e valida.
    for (const s of v.sugestoes || []) {
      const t = C.estadoCom(r.leitura.estado, 0, s.painel);
      assert.equal(t.ok, true);
      assert.equal(consultar(t.texto).paineis[0].v.ok, true, t.texto);
    }
    return v;
  };

  // ------------------------------------------------ o link da página do município, ida e volta

  for (const codigo of [SANTOS, HERVAL, BOA, SORRISO]) {
    const texto = link(codigo);
    const lido = E.ler(texto);
    assert.equal(lido.ok, true, texto);
    assert.equal(E.serializar(lido.estado).texto, texto, 'o Python escreve o estado como o JavaScript');
    const r = consultar(texto);
    assert.equal(r.paineis[0].v.ok, true, texto);
    assert.equal(r.paineis[0].m.ok, true, texto);
    const m = r.paineis[0].m;
    // O link começa e termina num ano com valor do município.
    assert.equal(m.celulas[codigo][m.anos[0]].situacao, 'valor');
    assert.equal(m.celulas[codigo][m.anos[m.anos.length - 1]].situacao, 'valor');
  }
  // A placa vazia começa pela mesma consulta que o link do município.
  const inicial = C.painelDaSerie(C.comCobertura(catalogo, [ficha]), [SANTOS]);
  assert.equal(E.serializar({ v: 1, paineis: [inicial] }).texto, link(SANTOS));

  // ------------------------------------------------ Santos, a série inteira

  const santos = consultar(link(SANTOS)).paineis[0];
  assert.equal(santos.v.cruzamento, 'ibge_pop_ano');
  assert.deepEqual(santos.m.anos, [...Array(14)].map((_, k) => 2013 + k));
  assert.deepEqual(santos.m.coletados, COLETADOS);
  for (const ano of COLETADOS) {
    assert.deepEqual(santos.m.celulas[SANTOS][ano], { valor: 429528 - (2026 - ano) * 10, situacao: 'valor',
      comparavel: ano >= 2024 ? 2024 : ano >= 2018 ? 2018 : 2013 });
  }
  for (const ano of [2022, 2023]) assert.deepEqual(santos.m.celulas[SANTOS][ano], { valor: null, situacao: 'sem_dado', comparavel: null });
  // As ressalvas: as duas quebras, a de 2018 com os três documentos do IBGE, e os anos sem estimativa.
  const tipos = santos.v.ressalvas.map((r) => `${r.tipo}:${r.a_partir_de || (r.periodos || []).join(',')}`);
  assert.deepEqual(tipos, ['quebra_de_serie:2018', 'quebra_de_serie:2024', 'sem_dado:2022,2023']);
  const docs = santos.v.ressalvas[0].documentos;
  assert.equal(docs.length, 3);
  assert.ok(docs.every((d) => /^https:\/\/(ftp\.ibge\.gov\.br|www\.ibge\.gov\.br)\//.test(d.url) && d.titulo.startsWith('IBGE')));
  assert.equal(santos.v.ressalvas[1].documentos, undefined);
  // Os trechos da tabela e da linha: nada atravessa uma quebra nem um ano sem dado.
  assert.deepEqual(C.trechos(santos.m).map((t) => [t.tipo, t.comparavel, t.anos[0], t.anos[t.anos.length - 1]]),
    [['linha', 2013, 2013, 2017], ['linha', 2018, 2018, 2021], ['sem_dado', null, 2022, 2023], ['linha', 2024, 2024, 2026]]);
  const pontos = (m, codigo) => m.anos.map((ano) => ({ ano, ...m.celulas[codigo][ano] }));
  assert.deepEqual(C.segmentos(pontos(santos.m, SANTOS)).map((s) => s.map((p) => p.ano)),
    [[2013, 2014, 2015, 2016, 2017], [2018, 2019, 2020, 2021], [2024, 2025, 2026]]);
  assert.equal(C.titulo(catalogo, santos.v.painel, nomes), 'População estimada de Santos (SP), de 2013 a 2026');
  assert.equal(C.descrever(catalogo, santos.v.painel, nomes), 'População estimada de Santos (SP), de 2013 a 2026, em linha');

  // O catálogo embutido decide igual ao inteiro em toda consulta de população.
  for (const texto of [link(SANTOS), 'v=1&p=m:populacao_estimada;t:n,3548500;p:2010,2012;g:linha',
    'v=1&p=m:populacao_estimada;t:n,5107925;p:2021,2025;g:tabela', 'v=1&p=m:populacao_estimada;t:n,3548500;p:2013,2026;g:numero']) {
    const a = consultar(texto).paineis[0].v;
    const b = consultar(texto, [ficha], inteiro).paineis[0].v;
    assert.deepEqual(a, b, texto);
  }

  // ------------------------------------------------ ausência nunca vira zero

  const boa = consultar('v=1&p=m:populacao_estimada;t:n,5101837;p:2013,2026;g:linha').paineis[0];
  for (const ano of [2013, 2017, 2021, 2024]) assert.deepEqual(boa.m.celulas[BOA][ano], { valor: null, situacao: 'sem_estimativa', comparavel: ano >= 2024 ? 2024 : ano >= 2018 ? 2018 : 2013 });
  assert.equal(boa.m.celulas[BOA][2025].valor, 5983 - 10);
  assert.ok(Object.values(boa.m.celulas[BOA]).every((c) => c.valor !== 0));
  assert.deepEqual(C.segmentos(pontos(boa.m, BOA)).map((s) => s.map((p) => p.ano)), [[2025, 2026]]);

  // ------------------------------------------------ nota do município

  const sorriso = consultar('v=1&p=m:populacao_estimada;t:n,5107925;p:2024;g:numero').paineis[0];
  assert.equal(sorriso.m.celulas[SORRISO][2024].valor, 110635 - 200);
  assert.deepEqual(sorriso.v.ressalvas.map((r) => r.tipo), ['nota_do_municipio']);
  assert.deepEqual(sorriso.v.ressalvas[0].municipios, [SORRISO]);
  assert.ok(sorriso.v.ressalvas[0].texto.includes('Boa Esperança do Norte'));
  const atravessa = consultar('v=1&p=m:populacao_estimada;t:n,3548500,5107925;p:2021,2025;g:tabela').paineis[0];
  assert.deepEqual(atravessa.v.ressalvas.map((r) => r.tipo), ['quebra_de_serie', 'sem_dado', 'nota_do_municipio']);
  assert.deepEqual(atravessa.v.ressalvas[2].municipios, [SORRISO]);

  // ------------------------------------------------ vários municípios num ano, e vários painéis

  const doisNumAno = consultar('v=1&p=m:populacao_estimada;t:n,3548500,4206504;p:2025;g:barras').paineis[0];
  assert.deepEqual(doisNumAno.m.municipios, [SANTOS, HERVAL]);
  assert.equal(doisNumAno.m.celulas[HERVAL][2025].valor, 22000 - 10);
  const misto = consultar(`${link(SANTOS)}&p=m:populacao_estimada;t:n,3548500;p:2010;g:linha`);
  assert.equal(misto.paineis[0].v.ok, true);
  assert.equal(misto.paineis[1].v.ok, false);
  assert.equal(misto.paineis[1].v.motivo, 'periodo_indisponivel');
  // A sugestão de um painel troca só ele: o outro continua no link.
  const trocado = C.estadoCom(misto.leitura.estado, 1, misto.paineis[1].v.sugestoes[0].painel);
  assert.equal(consultar(trocado.texto).paineis.every((p) => p.v.ok), true);
  assert.ok(trocado.texto.startsWith(link(SANTOS)));

  // ------------------------------------------------ CSV

  const contexto = { catalogo, painel: santos.v.painel, nomes, ficha, ressalvas: santos.v.ressalvas, enderecoFicha: 'https://exemplo/ficha.json' };
  const texto = C.csv(santos.m, contexto);
  assert.ok(texto.startsWith('﻿municipio;uf;codigo_ibge;ano;valor;unidade;situacao;comparavel_desde;'));
  const linhasCsv = texto.slice(1).trimEnd().split('\r\n');
  assert.equal(linhasCsv.length, 1 + 14);
  const de = (ano) => linhasCsv.find((l) => l.split(';')[3] === String(ano)).split(';');
  assert.deepEqual(de(2026).slice(0, 8), ['Santos', 'SP', SANTOS, '2026', '429528', 'pessoas', 'estimativa do IBGE', '2024']);
  assert.equal(de(2026)[10], 'https://apisidra.ibge.gov.br/values/t/6579/n6/3548500/v/9324/p/2026');
  assert.equal(de(2026)[11], ficha.anos['2026'].coletado_em);
  assert.equal(de(2026)[13], '2018, 2024');
  assert.equal(de(2026)[14], 'https://exemplo/ficha.json');
  assert.deepEqual(de(2022).slice(4, 8), ['', 'pessoas', 'sem estimativa do IBGE no ano', '']);
  assert.equal(de(2022)[10], '');
  // Nome com separador ou aspas sai entre aspas, com as aspas dobradas.
  const estranho = C.csv(doisNumAno.m, { ...contexto, painel: doisNumAno.v.painel, ressalvas: [],
    nomes: new Map([[SANTOS, { nome: 'Santos', uf: 'SP' }], [HERVAL, { nome: 'Herval "d;Oeste"', uf: 'SC' }]]) });
  assert.ok(estranho.includes('\r\n"Herval ""d;Oeste""";SC;4206504;2025;21990;'));
  // A nota do município vai na linha dele e do ano dela, e só nela.
  const comNota = C.csv(atravessa.m, { ...contexto, painel: atravessa.v.painel, ressalvas: atravessa.v.ressalvas });
  const notas = comNota.split('\r\n').filter((l) => l.includes('Boa Esperança do Norte'));
  assert.equal(notas.length, 1);
  assert.ok(notas[0].startsWith('Sorriso;MT;5107925;2024;'));

  // ------------------------------------------------ recusas: mensagem e sugestões, nunca gráfico vazio

  // Sem a ficha, a tela não sabe até onde a série vai.
  for (const fichas of [[], [{ ...ficha, anos: { ...ficha.anos, 2022: ficha.anos['2021'] } }]]) {
    const v = recusa(link(SANTOS), 'cobertura_desconhecida', fichas);
    assert.match(v.mensagem, /não foi possível saber quais anos estão publicados/);
  }
  // Código no formato certo, mas fora da lista de municípios: a validação aceita, a consulta de modelo recusa.
  const fora = consultar('v=1&p=m:populacao_estimada;t:n,3548500,3500001;p:2013,2026;g:linha').paineis[0];
  assert.equal(fora.v.ok, true);
  assert.equal(fora.m.ok, false);
  assert.equal(fora.m.motivo, 'municipio_desconhecido');
  assert.deepEqual(fora.m.codigos, ['3500001']);
  assert.match(fora.m.mensagem, /3500001 não é de nenhum município/);
  // Fora das pontas da série, ou só nos anos sem estimativa.
  assert.deepEqual(recusa('v=1&p=m:populacao_estimada;t:n,3548500;p:2010,2012;g:linha', 'periodo_indisponivel').sugestoes[0].painel.periodo,
    { de: '2013', ate: '2013' });
  assert.deepEqual(recusa('v=1&p=m:populacao_estimada;t:n,3548500;p:2030;g:linha', 'periodo_indisponivel').sugestoes[0].painel.periodo,
    { de: '2026', ate: '2026' });
  recusa('v=1&p=m:populacao_estimada;t:n,3548500;p:2022,2023;g:linha', 'periodo_sem_dado');
  // Número em destaque com mais de uma célula: a sugestão é a mesma consulta em tabela.
  assert.equal(recusa('v=1&p=m:populacao_estimada;t:n,3548500;p:2013,2026;g:numero', 'numero_pede_uma_celula').sugestoes[0].painel.grafico, 'tabela');
  // O SIH não está no catálogo da página: internações não existe, e a sugestão é a população.
  const sih = recusa('v=1&p=m:internacoes;t:r,3548500;p:2024,2025;g:linha', 'metrica_desconhecida');
  assert.equal(sih.sugestoes[0].painel.metrica, 'populacao_estimada');
  assert.equal(consultar('v=1&p=m:internacoes;t:r,3548500;p:2024,2025;g:linha', [ficha], inteiro).paineis[0].v.motivo, 'nao_publicada');
  // População não tem município de residência nem de hospital.
  assert.equal(recusa('v=1&p=m:populacao_estimada;t:r,3548500;p:2025;g:linha', 'papel_nao_se_aplica').sugestoes[0].painel.territorio.papel, undefined);
  // Links que não abrem: cada um com a sua mensagem, e os códigos que estavam neles para sugerir a série.
  const quebrados = {
    'v=1&p=lixo': 'ilegivel',
    'v=1&p=m:populacao_estimada;t:n,3548500;p:20%': 'ilegivel',
    'v=2&p=m:populacao_estimada;t:n,3548500;p:2025;g:linha': 'versao_mais_nova',
    'v=0&p=m:populacao_estimada;t:n,3548500;p:2025;g:linha': 'versao_antiga',
    [`v=1&p=${'x'.repeat(2000)}`]: 'grande_demais',
    '': 'ilegivel',
  };
  for (const [t, motivo] of Object.entries(quebrados)) {
    const r = consultar(t).leitura;
    assert.equal(r.ok, false, t);
    assert.equal(r.motivo, motivo, t);
    assert.ok(r.mensagem.length > 20);
  }
  assert.deepEqual(C.codigosNoTexto('v=1&p=m:populacao_estimada;t:n,3548500,5101837;p:20%'), [SANTOS, BOA]);
  assert.deepEqual(C.codigosNoTexto('v=1&p=m:x;t:n,35485001'), []);
  assert.deepEqual(C.codigosNoTexto('3548500'), [SANTOS]);
  // O código-fonte do site não usa lookbehind em expressão regular (Safari antes do 16.4).
  for (const f of ['especificacao.js', 'consulta.js', 'busca.js']) {
    assert.ok(!/\(\?<[=!]/.test(fs.readFileSync(path.join(__dirname, '..', 'web', f), 'utf-8')), f);
  }

  // ------------------------------------------------ ficha e arquivo de coletas diferentes

  const com2027 = { ...ficha, anos: { ...ficha.anos, 2027: ficha.anos['2026'] } };
  assert.equal(C.conferir(indice, com2027).motivo, 'arquivos_divergentes');
  const outraContagem = copia(ficha);
  outraContagem.anos['2025'].municipios_com_valor -= 1;
  assert.equal(C.conferir(indice, outraContagem).motivo, 'arquivos_divergentes');
  // Um ano coletado que some do arquivo, sem a conferência antes: a consulta de modelo também recusa.
  const semAno = C.indexar(linhas.filter((l) => l.ano !== 2019), 'populacao');
  assert.equal(C.executar(semAno, santos.v.consulta, serie, ficha).motivo, 'arquivos_divergentes');

  // ------------------------------------------------ escala e textos

  const e = C.escala(429398, 429528, false);
  assert.ok(e.inicio > 0 && e.inicio <= 429398 && e.fim >= 429528);
  // Faixa mínima da linha: 20% do maior valor, centrada nos dados, nunca abaixo de zero; série que varia mais
  // do que isso ocupa o eixo inteiro.
  assert.equal(C.FAIXA_MINIMA, 0.2);
  const santosEixo = C.dominioDaLinha(428000, 435000);
  assert.ok(Math.abs(santosEixo.hi - santosEixo.lo - 0.2 * 435000) < 1e-6 && santosEixo.lo < 428000 && santosEixo.hi > 435000);
  assert.deepEqual(C.dominioDaLinha(600, 1000), { lo: 600, hi: 1000 });
  assert.ok(Math.abs(C.dominioDaLinha(5, 6).lo - 4.9) < 1e-9);
  assert.equal(C.dominioDaLinha(0, 10).lo, 0);
  // Variação por trecho comparável, sem atravessar quebra nem ano sem dado.
  const vs = C.variacoes(pontos(santos.m, SANTOS));
  assert.deepEqual(vs.map((v) => [v.de, v.ate]), [[2013, 2017], [2018, 2021], [2024, 2026]]);
  assert.ok(Math.abs(vs[2].pct - (429528 / 429508 - 1) * 100) < 1e-9);
  assert.deepEqual(C.variacoes(pontos(boa.m, BOA)).map((v) => [v.de, v.ate]), [[2025, 2026]]);
  // Um ano sem estimativa no meio de um trecho comparável interrompe a linha, e a variação também: nada de
  // 2018 a 2021 por cima do buraco de 2019, só o segmento de 2020 a 2021.
  const comBuraco = [
    { ano: 2018, valor: 100, situacao: 'valor', comparavel: 2018 },
    { ano: 2019, valor: null, situacao: 'sem_estimativa', comparavel: 2018 },
    { ano: 2020, valor: 110, situacao: 'valor', comparavel: 2018 },
    { ano: 2021, valor: 121, situacao: 'valor', comparavel: 2018 },
  ];
  assert.deepEqual(C.variacoes(comBuraco).map((v) => [v.de, v.ate]), [[2020, 2021]]);
  assert.ok(Math.abs(C.variacoes(comBuraco)[0].pct - 10) < 1e-9);
  assert.equal(C.textoDasVariacoes(comBuraco), '+10,0% de 2020 a 2021');
  // A variação é sempre um dos segmentos que a linha desenha.
  for (const p of [comBuraco, pontos(santos.m, SANTOS), pontos(boa.m, BOA)]) {
    assert.deepEqual(C.variacoes(p).map((v) => [v.de, v.ate]),
      C.segmentos(p).filter((s) => s.length > 1).map((s) => [s[0].ano, s[s.length - 1].ano]));
  }
  assert.equal(C.porcentagem(1.234), '+1,2%');
  assert.equal(C.porcentagem(-0.96), '\u22121,0%');
  assert.equal(C.porcentagem(0.004), '0,0%');
  assert.equal(C.porcentagem(-0.004), '0,0%');
  assert.equal(C.textoDasVariacoes(pontos(boa.m, BOA)), `${C.porcentagem((5983 / 5973 - 1) * 100)} de 2025 a 2026`);
  assert.equal(C.escala(5000, 9000, true).inicio, 0);
  assert.equal(C.formatoDoEixo(430000)(425000), '425 mil');
  assert.equal(C.formatoDoEixo(600000)(0), '0');
  assert.equal(C.formatoDoEixo(12000000)(11500000), '11,5 mi');
  assert.equal(C.formatoDoEixo(9000)(5000), '5.000');
  assert.equal(C.faixas([2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2024, 2025, 2026]), '2013 a 2021 e 2024 a 2026');
  assert.equal(C.faixas([2025, 2026]), '2025 e 2026');
  assert.equal(C.dataHora('2026-11-07T23:23:20Z'), '07/11/2026, 20:23 (Brasília)');
};

principal().catch((erro) => {
  console.error(erro);
  process.exit(1);
});
