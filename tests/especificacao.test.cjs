// Testes da especificação de consulta (consulta/especificacao.js), fora do navegador e sem rede.
// Roda pelo pytest (tests/test_especificacao.py), que entrega o catálogo real em JSON no primeiro argumento.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const E = require('../consulta/especificacao.js');

const real = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));
const copia = (x) => JSON.parse(JSON.stringify(x));
// O catálogo real com o SIH ligado: é o que a validação vai ver no dia em que a base for para o site.
const ligado = copia(real);
ligado.bases.sih.publicacao.site = true;

const PAPEIS = { municipio_residencia: 'residencia', municipio_hospital: 'hospital' };
const PERIODOS = { ano: { de: '2024', ate: '2025' }, tri: { de: '2025-T1', ate: '2025-T4' }, mes: { de: '2025-01', ate: '2025-12' } };

// Um painel exatamente no grão de um cruzamento, com todas as outras dimensões detalhadas.
const noGrao = (c, metrica) => ({
  metrica,
  territorio: { papel: PAPEIS[c.dimensoes.find((d) => PAPEIS[d])], municipios: ['3548500'] },
  filtros: {},
  detalhar: c.dimensoes.filter((d) => !PAPEIS[d]).sort(),
  periodo: PERIODOS[c.tempo],
  grafico: 'linha',
});
const base = (mudancas = {}) => ({
  metrica: 'internacoes',
  territorio: { papel: 'residencia', municipios: ['3548500'] },
  filtros: {},
  detalhar: [],
  periodo: { de: '2024', ate: '2025' },
  grafico: 'linha',
  ...mudancas,
});
const semNumero = (x) => JSON.stringify(x, (_, v) => { assert.notEqual(typeof v, 'number'); return v; });
const sugestoesValidam = (catalogo, r) => {
  for (const s of r.sugestoes) {
    const v = E.validar(catalogo, s.painel);
    assert.equal(v.ok, true, JSON.stringify(s));
    assert.equal(v.cruzamento, s.cruzamento);
    assert.ok(s.descricao);
  }
};

// ---------------------------------------------------------------- formato

// O exemplo do plano, no formato da versão 1: o município sai dos filtros e ganha o papel.
const exemplo = {
  v: 1,
  paineis: [{
    tema: 'saude',
    metrica: 'internacoes',
    territorio: { papel: 'residencia', municipios: ['3548500'] },
    filtros: { capitulo_cid: ['10'] },
    periodo: { de: '2025-T1', ate: '2025-T4' },
    grafico: 'linha',
  }],
};
{
  const r = E.normalizar(exemplo);
  assert.equal(r.ok, true);
  assert.deepEqual(r.estado.paineis[0].detalhar, []);
  assert.deepEqual(E.normalizar(r.estado), r);   // normalizar o canônico não muda nada
}
const invalido = (mudar, trecho) => {
  const estado = copia(exemplo);
  mudar(estado.paineis[0], estado);
  const r = E.normalizar(estado);
  assert.equal(r.ok, false, trecho);
  assert.equal(r.motivo, 'formato');
  assert.ok(r.erros.some((e) => e.includes(trecho)), `${trecho}: ${r.erros}`);
};
invalido((p, e) => { delete e.v; }, 'versão');
invalido((p, e) => { e.v = '1'; }, 'versão');
invalido((p, e) => { e.titulo = 'Meu painel'; }, 'campo desconhecido');
invalido((p) => { p.titulo = 'Texto livre'; }, 'campo desconhecido');
invalido((p) => { p.filtros.municipio_residencia = ['3548500']; }, 'o município vai em território');
invalido((p) => { p.territorio.papel = 'ocorrencia'; }, 'papel');
invalido((p) => { p.territorio.municipios = []; }, 'municípios');
invalido((p) => { p.territorio.municipios = ['354850']; }, 'municípios');
invalido((p) => { p.territorio.municipios = ['3548500', '3548500']; }, 'municípios');
invalido((p) => { p.territorio.municipios = Array.from({ length: 11 }, (_, i) => String(3500000 + i)); }, 'municípios');
invalido((p) => { p.territorio.uf = 'SP'; }, 'território');
invalido((p) => { p.filtros.capitulo_cid = []; }, 'filtro capitulo_cid');
invalido((p) => { p.filtros.capitulo_cid = ['10 OR 1=1']; }, 'filtro capitulo_cid');
invalido((p) => { p.detalhar = ['capitulo_cid']; }, 'ao mesmo tempo');
invalido((p) => { p.periodo = { de: '2025-T1', ate: '2025' }; }, 'mesmo formato');
invalido((p) => { p.periodo = { de: '2025-T5', ate: '2025-T5' }; }, 'mesmo formato');
invalido((p) => { p.periodo = { de: '2025-13', ate: '2025-13' }; }, 'mesmo formato');
invalido((p) => { p.periodo = { de: '2025', ate: '2024' }; }, 'depois');
invalido((p) => { p.grafico = 'pizza'; }, 'gráfico');
invalido((p) => { delete p.metrica; }, 'métrica');
invalido((p, e) => { e.paineis = []; }, 'painéis');
invalido((p, e) => { e.paineis = Array.from({ length: 13 }, () => copia(p)); }, 'painéis');
for (const lixo of [null, undefined, 'v=1', 7, [], { v: 1 }]) assert.equal(E.normalizar(lixo).ok, false);

// ---------------------------------------------------------------- catálogo de hoje: nada passa

// O SIH está com publicacao.site false e a população não tem cruzamento: toda especificação é recusada.
let recusadas = 0;
for (const c of real.cruzamentos) {
  for (const metrica of Object.keys(real.metricas)) {
    const r = E.validar(real, noGrao(c, metrica));
    assert.equal(r.ok, false, `${c.id} ${metrica}`);
    assert.equal(r.tipo, 'recusa');
    assert.deepEqual(r.sugestoes, []);
    assert.match(r.mensagem, /Nenhuma consulta parecida está publicada/);
    assert.equal(r.motivo, real.metricas[metrica].base === 'sih' || metrica === 'taxa_internacao' ? 'nao_publicada' : 'sem_cruzamento');
    semNumero(r);
    recusadas += 1;
  }
}
assert.equal(recusadas, real.cruzamentos.length * Object.keys(real.metricas).length);
assert.match(E.validar(real, base()).mensagem, /^Internações ainda não está publicada no site/);
// Sem o papel, a pergunta continua sendo qual é o papel, mas nenhuma das respostas valida.
{
  const r = E.validar(real, base({ territorio: { municipios: ['3548500'] } }));
  assert.equal(r.tipo, 'recusa');
  assert.equal(r.motivo, 'nao_publicada');
}

// ---------------------------------------------------------------- SIH ligado: só a superfície, no grão exato

const superficie = ligado.bases.sih.superficie.cruzamentos;
let aceitas = 0;
for (const c of ligado.cruzamentos) {
  for (const metrica of Object.keys(ligado.metricas)) {
    const esperado = Boolean(superficie[c.id] && superficie[c.id].medidas.includes(metrica));
    const r = E.validar(ligado, noGrao(c, metrica));
    assert.equal(r.ok, esperado, `${c.id} ${metrica}`);
    semNumero(r);
    if (esperado) {
      assert.equal(r.cruzamento, c.id);
      assert.equal(r.consulta.cruzamento, c.id);
      assert.equal(r.consulta.tempo, c.tempo);
      aceitas += 1;
    } else sugestoesValidam(ligado, r);
  }
}
assert.equal(aceitas, 6);   // as cinco tabelas da superfície, e os óbitos de sih_res_ano

// A consulta de modelo: só escolhe linhas de um cruzamento.
assert.deepEqual(E.validar(ligado, base({ filtros: { capitulo_cid: ['10', '0'] } })).consulta, {
  cruzamento: 'sih_res_ano_capitulo',
  metrica: 'internacoes',
  territorio: { dimensao: 'municipio_residencia', municipios: ['3548500'] },
  filtros: { capitulo_cid: ['10', '0'] },
  detalhar: [],
  tempo: 'ano',
  de: '2024',
  ate: '2025',
});
assert.equal(E.validar(ligado, base({ territorio: { papel: 'hospital', municipios: ['3548500', '3550308'] } })).cruzamento, 'sih_hosp_ano');
assert.equal(E.validar(ligado, base({ periodo: { de: '2025-01', ate: '2025-06' } })).cruzamento, 'sih_res_mes');
assert.equal(E.validar(ligado, base({ filtros: { sexo: ['F'] }, detalhar: ['faixa_12'] })).cruzamento, 'sih_res_ano_faixa12_sexo');

const recusa = (painel, motivo, primeira) => {
  const r = E.validar(ligado, painel);
  assert.equal(r.ok, false);
  assert.equal(r.tipo, 'recusa');
  assert.equal(r.motivo, motivo, r.mensagem);
  assert.ok(r.mensagem.length > 20);
  assert.ok(r.sugestoes.length >= 1 && r.sugestoes.length <= 3);
  sugestoesValidam(ligado, r);
  if (primeira) assert.equal(r.sugestoes[0].cruzamento, primeira);
  return r;
};
// O exemplo do plano pede capítulo por trimestre, que a superfície não tem: a sugestão é o mesmo
// capítulo por ano, nos mesmos anos.
{
  const r = recusa(exemplo.paineis[0], 'grao_indisponivel', 'sih_res_ano_capitulo');
  assert.deepEqual(r.sugestoes[0].painel.filtros, { capitulo_cid: ['10'] });
  assert.deepEqual(r.sugestoes[0].painel.periodo, { de: '2025', ate: '2025' });
}
// Só sexo, por ano: existiria somando as faixas de idade da tabela de faixa e sexo, e por isso é recusado.
{
  const r = recusa(base({ filtros: { sexo: ['F'] } }), 'grao_indisponivel', 'sih_res_ano_faixa12_sexo');
  assert.deepEqual(r.sugestoes[0].painel.detalhar, ['faixa_12']);
  assert.deepEqual(r.sugestoes[0].painel.filtros, { sexo: ['F'] });
}
// Total por UF ou por trimestre não existe sem somar municípios ou meses.
recusa(base({ periodo: { de: '2025-T1', ate: '2025-T2' } }), 'grao_indisponivel', 'sih_res_mes');
recusa(base({ metrica: 'obitos', periodo: { de: '2025-01', ate: '2025-12' } }), 'grao_indisponivel', 'sih_res_ano');
recusa(base({ metrica: 'obitos', territorio: { papel: 'hospital', municipios: ['3548500'] } }), 'grao_indisponivel', 'sih_res_ano');
recusa(base({ territorio: { papel: 'hospital', municipios: ['3548500'] }, detalhar: ['especialidade'] }), 'grao_indisponivel', 'sih_hosp_ano');
// Fora da superfície, fora do catálogo, valor que a dimensão não tem.
recusa(base({ metrica: 'valor_total' }), 'sem_cruzamento', 'sih_res_ano');
recusa(base({ metrica: 'leitos' }), 'metrica_desconhecida', 'sih_res_ano');
recusa(base({ metrica: 'constructor' }), 'metrica_desconhecida');
recusa(base({ tema: 'educacao' }), 'tema_incompativel', 'sih_res_ano');
recusa(base({ detalhar: ['escolaridade'] }), 'dimensao_desconhecida', 'sih_res_ano');
recusa(base({ filtros: { capitulo_cid: ['23'] } }), 'valor_desconhecido', 'sih_res_ano_capitulo');
{
  const r = recusa(base({ filtros: { sexo: ['X', 'F'] }, detalhar: ['faixa_12'] }), 'valor_desconhecido', 'sih_res_ano_faixa12_sexo');
  assert.deepEqual(r.sugestoes[0].painel.filtros, { sexo: ['F'] });
}
assert.equal(E.validar(ligado, base({ tema: 'saude' })).ok, true);
// População e taxa continuam de fora: não há cruzamento delas no catálogo.
recusa(base({ metrica: 'populacao_estimada' }), 'sem_cruzamento');
recusa(base({ metrica: 'taxa_internacao' }), 'sem_cruzamento');
// Formato errado também é recusa, sem exceção.
assert.equal(E.validar(ligado, { metrica: 'internacoes' }).motivo, 'formato');
assert.equal(E.validar(ligado, null).motivo, 'formato');

// "Em Santos": sem o papel, a resposta é a pergunta, com as duas opções já validadas.
{
  const r = E.validar(ligado, base({ territorio: { municipios: ['3548500'] } }));
  assert.equal(r.ok, false);
  assert.equal(r.tipo, 'ambigua');
  assert.deepEqual(r.opcoes.map((o) => [o.papel, o.validacao.cruzamento]), [['residencia', 'sih_res_ano'], ['hospital', 'sih_hosp_ano']]);
  const mensal = E.validar(ligado, base({ territorio: { municipios: ['3548500'] }, periodo: { de: '2025-01', ate: '2025-03' } }));
  assert.deepEqual(mensal.opcoes.map((o) => o.validacao.ok), [true, false]);
  semNumero(mensal);
}

// ---------------------------------------------------------------- URL: ida e volta

const fixo = 'v=1&p=m:internacoes;k:saude;t:r,3548500;f:capitulo_cid=10;p:2025-T1,2025-T4;g:linha';
assert.deepEqual(E.serializar(exemplo), { ok: true, texto: fixo, tamanho: fixo.length });
assert.deepEqual(E.ler(fixo), E.normalizar(exemplo));
assert.deepEqual(E.ler(`#${fixo}`), E.normalizar(exemplo));

const estados = [exemplo];
for (const c of ligado.cruzamentos) {
  for (const metrica of c.metricas) estados.push({ v: 1, paineis: [noGrao(c, metrica)] });
}
estados.push({ v: 1, paineis: [
  base({ filtros: { sexo: ['F', 'M'], faixa_12: ['80+', '01-04', '00', 'ign'] } }),
  base({ territorio: { municipios: ['3548500', '1100015', '5300108'] }, periodo: { de: '2025-03', ate: '2025-03' }, grafico: 'tabela' }),
  base({ tema: 'financas_publicas', metrica: 'valor_total', detalhar: ['sexo', 'faixa_5'], grafico: 'barras' }),
] });
estados.push({ v: 1, paineis: ligado.cruzamentos.slice(0, 12).map((c) => noGrao(c, c.metricas[0])) });
for (const estado of estados) {
  const canonico = E.normalizar(estado);
  const ida = E.serializar(estado);
  assert.equal(ida.ok, true);
  assert.match(ida.texto, /^[A-Za-z0-9_.~%:;,=&-]+$/);   // nada que um navegador reescreva depois do "#"
  assert.deepEqual(E.ler(ida.texto), canonico);
  assert.equal(E.serializar(E.ler(ida.texto).estado).texto, ida.texto);
  assert.equal(new URL(`https://exemplo.invalid/painel/#${ida.texto}`).hash, `#${ida.texto}`);
}
assert.match(E.serializar(estados[estados.length - 2]).texto, /f:faixa_12=80%2B,01-04,00,ign;f:sexo=F,M/);
// A ordem das chaves no objeto não muda o texto; a ordem dos municípios e dos valores é de quem montou.
assert.equal(E.serializar({ paineis: [{ grafico: 'linha', periodo: { ate: '2025-T4', de: '2025-T1' }, filtros: { capitulo_cid: ['10'] },
  territorio: { municipios: ['3548500'], papel: 'residencia' }, metrica: 'internacoes', tema: 'saude' }], v: 1 }).texto, fixo);
// Campos fora de ordem são lidos, e voltam na ordem canônica.
assert.equal(E.serializar(E.ler('v=1&p=g:linha;p:2025-T1,2025-T4;f:capitulo_cid=%31%30;t:r,3548500;k:saude;m:internacoes').estado).texto, fixo);
assert.equal(E.serializar({ v: 1, paineis: [{ metrica: 'internacoes' }] }).motivo, 'formato');

// Versão antiga, versão mais nova e texto estragado: sempre um motivo e uma mensagem, nunca um estado.
const naoLe = (texto, motivo) => {
  const r = E.ler(texto);
  assert.equal(r.ok, false, texto);
  assert.equal(r.motivo, motivo, texto);
  assert.ok(r.mensagem.length > 20);
  assert.equal(r.estado, undefined);
  assert.deepEqual(E.abrir(ligado, texto), r);
};
naoLe(fixo.replace('v=1', 'v=0'), 'versao_antiga');
naoLe(fixo.replace('v=1', 'v=2'), 'versao_mais_nova');
naoLe('v=2&qualquer=coisa', 'versao_mais_nova');
for (const texto of ['', '#', 'v=1', 'v=1&', 'v=um&p=m:internacoes', fixo.replace('v=1&', ''), fixo.replace('v=1', 'v=-1'),
  fixo.replace('v=1', 'v=1.5'), `${fixo}&x=1`, `${fixo}&p=`, `${fixo};m:obitos`, `${fixo};z:1`, `${fixo};g:barras`,
  fixo.replace('t:r', 't:x'), fixo.replace('t:r,3548500', 't:r'), fixo.replace('=10', '=%E0%A4%A'), fixo.replace('=10', '='),
  fixo.replace('f:capitulo_cid=10', 'f:capitulo_cid=10;f:capitulo_cid=11'), fixo.replace('f:capitulo_cid', 'f:'),
  fixo.replace('p:2025-T1,2025-T4', 'p:2025-T1,2025-T2,2025-T4'), fixo.replace('m:internacoes', 'm:internacoes,obitos'),
  `${fixo};titulo:Compre%20agora`, fixo.replace('g:linha', 'g:<script>'), 'v=1&p=__proto__:x']) {
  naoLe(texto, 'ilegivel');
}
for (const lixo of [null, undefined, 7, {}]) assert.equal(E.ler(lixo).motivo, 'ilegivel');

// Texto estragado ao acaso (sorteio fixo): ler nunca lança erro, e o que ele aceita é um estado que volta igual.
{
  let semente = 20261112;
  const sorteio = (n) => { semente = (semente * 1103515245 + 12345) % 2147483648; return semente % n; };
  const alfabeto = 'v=1&p:;,%+-_#mtkfdgrhn09aZ ';
  const textos = estados.map((e) => E.serializar(e).texto);
  let lidos = 0;
  for (let i = 0; i < 4000; i += 1) {
    const letras = [...textos[sorteio(textos.length)]];
    for (let n = 1 + sorteio(3); n > 0; n -= 1) {
      const onde = sorteio(letras.length);
      const troca = alfabeto[sorteio(alfabeto.length)];
      letras.splice(onde, sorteio(2), ...(sorteio(3) ? [troca] : []));
    }
    const r = E.ler(letras.join(''));
    if (r.ok) {
      lidos += 1;
      assert.deepEqual(E.ler(E.serializar(r.estado).texto), r);
    } else assert.ok(r.mensagem);
  }
  assert.ok(lidos > 100 && lidos < 3900, String(lidos));
}

// ---------------------------------------------------------------- URL: tamanho

// Medidas de um dashboard grande, fixadas aqui: se o formato do texto mudar, o teste avisa.
const dashboard = (paineis, municipios, filtros) => ({ v: 1, paineis: Array.from({ length: paineis }, (_, i) => base({
  territorio: { papel: 'residencia', municipios: Array.from({ length: municipios }, (_, m) => String(3500105 + 1000 * i + m)) },
  filtros,
  periodo: { de: '2021', ate: '2025' },
})) });
const tamanho = (estado) => { const r = E.serializar(estado, Infinity); assert.equal(r.ok, true); return r.tamanho; };
const medidas = {
  limite: E.LIMITE_ESTADO,
  '12 painéis, 1 município cada': tamanho(dashboard(12, 1, {})),
  '12 painéis, 5 municípios e 3 capítulos cada': tamanho(dashboard(12, 5, { capitulo_cid: ['9', '10', '11'] })),
  '12 painéis, 10 municípios cada': tamanho(dashboard(12, 10, {})),
  '12 painéis, 10 municípios, sexo e as 13 faixas de idade': tamanho(dashboard(12, 10, { sexo: ['F'], faixa_12: ligado.dimensoes.faixa_12.valores })),
};
if (process.env.MEDIR) console.log(JSON.stringify(medidas, null, 1));
assert.deepEqual(medidas, {
  limite: 1800,
  '12 painéis, 1 município cada': 579,
  '12 painéis, 5 municípios e 3 capítulos cada': 1239,
  '12 painéis, 10 municípios cada': 1443,
  '12 painéis, 10 municípios, sexo e as 13 faixas de idade': 2559,
});
// No pior painel que o formato aceita, quantos cabem.
{
  const pior = { sexo: ['F'], faixa_12: ligado.dimensoes.faixa_12.valores };
  let cabem = 0;
  while (cabem < E.LIMITES.paineis && E.serializar(dashboard(cabem + 1, 10, pior)).ok) cabem += 1;
  assert.equal(cabem, 8);
  // Acima do limite não sai link nenhum, e a recusa diz o tamanho e o limite.
  const r = E.serializar(dashboard(cabem + 1, 10, pior));
  assert.equal(r.ok, false);
  assert.equal(r.motivo, 'grande_demais');
  assert.equal(r.texto, undefined);
  assert.ok(r.tamanho > r.limite && r.limite === E.LIMITE_ESTADO);
  assert.match(r.mensagem, new RegExp(`${r.tamanho} caracteres, e o limite é 1800`));
  // Um link maior que o limite, vindo de fora, também não é lido.
  const longo = E.serializar(dashboard(cabem + 1, 10, pior), Infinity).texto;
  assert.equal(E.ler(longo).motivo, 'grande_demais');
  assert.equal(E.abrir(ligado, longo).motivo, 'grande_demais');
  assert.equal(E.ler(longo, Infinity).ok, true);
}

// ---------------------------------------------------------------- link de cruzamento que deixou de existir

{
  const link = E.serializar({ v: 1, paineis: [base(), base({ filtros: { capitulo_cid: ['10'] } })] }).texto;
  const antes = E.abrir(ligado, link);
  assert.deepEqual(antes.paineis.map((p) => p.cruzamento), ['sih_res_ano', 'sih_res_ano_capitulo']);

  // O capítulo sai da superfície: o primeiro painel continua, e o segundo vira recusa com mensagem e sugestão.
  const semCapitulo = copia(ligado);
  delete semCapitulo.bases.sih.superficie.cruzamentos.sih_res_ano_capitulo;
  const depois = E.abrir(semCapitulo, link);
  assert.equal(depois.ok, true);
  assert.equal(depois.paineis[0].cruzamento, 'sih_res_ano');
  assert.equal(depois.paineis[1].ok, false);
  assert.equal(depois.paineis[1].motivo, 'grao_indisponivel');
  assert.match(depois.paineis[1].mensagem, /Internações, por município de residência, por ano, com capitulo_cid: este cruzamento não está publicado/);
  assert.equal(depois.paineis[1].sugestoes[0].cruzamento, 'sih_res_ano');
  sugestoesValidam(semCapitulo, depois.paineis[1]);

  // O cruzamento sai do catálogo inteiro, ou a métrica deixa de existir: mesma coisa.
  const semTabela = copia(ligado);
  semTabela.cruzamentos = semTabela.cruzamentos.filter((c) => c.id !== 'sih_res_ano_capitulo' && c.mae !== 'sih_res_ano_capitulo');
  delete semTabela.bases.sih.superficie.cruzamentos.sih_res_ano_capitulo;
  assert.equal(E.abrir(semTabela, link).paineis[1].motivo, 'grao_indisponivel');
  const semMetrica = copia(ligado);
  delete semMetrica.metricas.internacoes;
  assert.deepEqual(E.abrir(semMetrica, link).paineis.map((p) => p.motivo), ['metrica_desconhecida', 'metrica_desconhecida']);

  // O mesmo link, aberto com o catálogo de hoje: a base não está no site.
  const hoje = E.abrir(real, link);
  assert.equal(hoje.ok, true);
  assert.deepEqual(hoje.paineis.map((p) => p.motivo), ['nao_publicada', 'nao_publicada']);
  assert.ok(hoje.paineis.every((p) => p.mensagem.length > 20));
}
