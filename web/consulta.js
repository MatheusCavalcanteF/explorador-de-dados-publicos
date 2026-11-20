// Página de consulta. O estado vem do endereço, depois do "#". A especificação (especificacao.js) valida cada
// painel no catálogo embutido na página, com a cobertura da ficha publicada ao lado da série; o leitor
// (leitor.js) lê o Parquet; a consulta de modelo escolhe linhas, sem somar nem juntar; e a tela desenha
// número, linha, barras ou tabela, sempre com a tabela equivalente, as ressalvas e a fonte.
// A parte sem tela (Consulta) roda nos testes, no Node e sem rede; a parte com tela, só no navegador.

const Consulta = (() => {
  const E = typeof Especificacao !== 'undefined' ? Especificacao : require('./especificacao.js');

  const SITUACOES = {
    valor: 'estimativa do IBGE',
    sem_estimativa: 'sem estimativa do IBGE para o município no ano',
    sem_dado: 'sem estimativa do IBGE no ano',
    fora_da_cobertura: 'ano depois do último coletado',
  };
  // O que a célula da tabela diz quando não há número. Ausência nunca é zero.
  const NA_CELULA = { sem_estimativa: 'sem estimativa', sem_dado: 'sem dado no ano', fora_da_cobertura: 'ainda sem dado' };
  const NOME_DO_GRAFICO = { linha: 'linha', barras: 'barras', tabela: 'tabela', numero: 'número em destaque' };
  const DIVERGENTES = 'A ficha e o arquivo da série vieram de coletas diferentes, o que pode acontecer logo depois de '
    + 'uma publicação, enquanto o navegador guarda uma cópia antiga. Recarregue a página em alguns minutos.';

  const numero = (n) => Number(n).toLocaleString('pt-BR');
  const juntar = (lista) => (lista.length > 1 ? `${lista.slice(0, -1).join(', ')} e ${lista[lista.length - 1]}` : lista[0] || '');

  // Anos em trechos contínuos: [2013..2021, 2024..2026] vira "2013 a 2021 e 2024 a 2026".
  const faixas = (anos) => {
    const grupos = [];
    for (const ano of [...anos].sort((a, b) => a - b)) {
      const ultimo = grupos[grupos.length - 1];
      if (ultimo && ano === ultimo[1] + 1) ultimo[1] = ano;
      else grupos.push([ano, ano]);
    }
    return juntar(grupos.map(([de, ate]) => (de === ate ? String(de) : ate === de + 1 ? `${de} e ${ate}` : `${de} a ${ate}`)));
  };

  // Data e hora de Brasília (UTC−3 fixo desde 2019), como nas outras páginas do site.
  const dataHora = (iso) => {
    const d = new Date(Date.parse(iso) - 3 * 3600 * 1000);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getUTCDate())}/${p(d.getUTCMonth() + 1)}/${d.getUTCFullYear()}, ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} (Brasília)`;
  };

  // ---------------------------------------------------------------- catálogo e ficha

  const todos = (catalogo) => [...(catalogo.cruzamentos || []), ...(catalogo.cruzamentos_da_origem || [])];
  const cruzamento = (catalogo, id) => todos(catalogo).find((c) => c.id === id) || null;
  // Os cruzamentos cuja cobertura vem de uma ficha publicada ao lado do arquivo: o mesmo nome, em .json.
  const comFicha = (catalogo) => (catalogo.cruzamentos_da_origem || []).filter((c) => c.anos && c.arquivo);
  const enderecoDaFicha = (arquivo) => arquivo.replace(/\.parquet$/, '.json');
  const fichaDe = (fichas, id) => (fichas || []).find((f) => f && f.cruzamento === id) || null;
  // A coluna do valor é a que a ficha descreve além de código, ano e trecho comparável.
  const colunaDoValor = (ficha) => Object.keys(ficha.colunas || {}).find((k) => !['codigo', 'ano', 'comparavel_desde'].includes(k));

  // A mesma abertura de link da tela e dos testes: a cobertura das fichas entra no catálogo, e cada painel é
  // validado. Sem ficha, a validação recusa (não sabe até onde a série vai).
  const abrir = (catalogo, fichas, texto) => E.abrir(E.comCobertura(catalogo, fichas || []), texto);
  const comCobertura = (catalogo, fichas) => E.comCobertura(catalogo, fichas || []);

  // ---------------------------------------------------------------- leitura e consulta de modelo

  // As linhas do Parquet, por município e ano, e quantos municípios têm valor em cada ano.
  const indexar = (linhas, coluna) => {
    const porCodigo = new Map();
    const comValor = new Map();
    for (const l of linhas) {
      const ano = Number(l.ano);
      const valor = l[coluna] === null || l[coluna] === undefined ? null : Number(l[coluna]);
      if (!porCodigo.has(l.codigo)) porCodigo.set(l.codigo, new Map());
      porCodigo.get(l.codigo).set(ano, { valor, comparavel: Number(l.comparavel_desde) });
      comValor.set(ano, (comValor.get(ano) || 0) + (valor === null ? 0 : 1));
    }
    return { porCodigo, comValor };
  };

  // A ficha e o arquivo precisam ser da mesma coleta: os mesmos anos e, em cada um, o mesmo número de
  // municípios com valor. Logo depois de uma publicação, o cache pode juntar um velho e um novo.
  const conferir = (indice, ficha) => {
    const doArquivo = [...indice.comValor.keys()].sort((a, b) => a - b);
    const daFicha = Object.keys(ficha.anos || {}).map(Number).sort((a, b) => a - b);
    const iguais = doArquivo.length === daFicha.length && doArquivo.every((ano, i) => ano === daFicha[i])
      && daFicha.every((ano) => indice.comValor.get(ano) === ficha.anos[ano].municipios_com_valor);
    return iguais ? { ok: true } : { ok: false, motivo: 'arquivos_divergentes', mensagem: DIVERGENTES, tentarDeNovo: true };
  };

  // A consulta de modelo: as linhas do cruzamento, para os municípios e os anos pedidos. Não soma, não junta e
  // não calcula: cada célula é uma linha do arquivo, ou a razão de não haver uma.
  const executar = (indice, consulta, c, ficha) => {
    if (consulta.tempo !== 'ano' || Object.keys(consulta.filtros).length || consulta.detalhar.length) {
      return { ok: false, motivo: 'sem_modelo', mensagem: 'Esta tela ainda não desenha consultas com filtros ou detalhes.' };
    }
    const codigos = consulta.territorio.municipios;
    const desconhecidos = codigos.filter((codigo) => !indice.porCodigo.has(codigo));
    if (desconhecidos.length) {
      const um = desconhecidos.length === 1;
      return { ok: false, motivo: 'municipio_desconhecido', codigos: desconhecidos,
        mensagem: `${um ? 'O código' : 'Os códigos'} ${juntar(desconhecidos)} não ${um ? 'é' : 'são'} de nenhum município `
          + 'da lista do IBGE que o site usa. Confira o link ou escolha o município pelo nome.' };
    }
    const coletados = Object.keys(ficha.anos).map(Number).sort((a, b) => a - b);
    const ultimo = coletados[coletados.length - 1];
    const semDado = (c.anos && c.anos.sem_dado) || [];
    const anos = [];
    for (let ano = Number(consulta.de); ano <= Number(consulta.ate); ano += 1) anos.push(ano);
    const celulas = {};
    for (const codigo of codigos) {
      const linhas = indice.porCodigo.get(codigo);
      celulas[codigo] = {};
      for (const ano of anos) {
        const linha = linhas.get(ano);
        if (linha) {
          celulas[codigo][ano] = { valor: linha.valor, situacao: linha.valor === null ? 'sem_estimativa' : 'valor', comparavel: linha.comparavel };
        } else if (semDado.includes(ano)) celulas[codigo][ano] = { valor: null, situacao: 'sem_dado', comparavel: null };
        else if (ano > ultimo) celulas[codigo][ano] = { valor: null, situacao: 'fora_da_cobertura', comparavel: null };
        else return { ok: false, motivo: 'arquivos_divergentes', mensagem: DIVERGENTES, tentarDeNovo: true };
      }
    }
    return { ok: true, anos, municipios: [...codigos], celulas, coletados: coletados.filter((a) => a >= anos[0] && a <= anos[anos.length - 1]) };
  };

  // A situação de um ano, igual para todos os municípios quando não há linha no arquivo.
  const situacaoDoAno = (modelo, ano) => {
    const s = modelo.municipios.map((codigo) => modelo.celulas[codigo][ano]);
    const linha = s.find((x) => x.situacao === 'valor' || x.situacao === 'sem_estimativa');
    return linha ? { tipo: 'linha', comparavel: linha.comparavel } : { tipo: s[0].situacao, comparavel: null };
  };

  // Trechos da tabela: anos seguidos com o mesmo "comparável desde", ou sem dado. Só fazem sentido quando o
  // período atravessa uma quebra: valores de trechos diferentes não se comparam.
  const trechos = (modelo) => {
    const saida = [];
    for (const ano of modelo.anos) {
      const s = situacaoDoAno(modelo, ano);
      const chave = s.tipo === 'linha' ? `c${s.comparavel}` : s.tipo;
      const ultimo = saida[saida.length - 1];
      if (ultimo && ultimo.chave === chave) ultimo.anos.push(ano);
      else saida.push({ chave, tipo: s.tipo, comparavel: s.comparavel, anos: [ano] });
    }
    return saida;
  };

  // Pontos de uma série desenhados ligados: anos seguidos, com valor e no mesmo trecho comparável. A linha
  // nunca atravessa uma quebra nem um ano sem dado.
  const segmentos = (pontos) => {
    const saida = [];
    let atual = [];
    for (const p of pontos) {
      const anterior = atual[atual.length - 1];
      if (p.valor === null) {
        if (atual.length) saida.push(atual);
        atual = [];
        continue;
      }
      if (anterior && (p.ano !== anterior.ano + 1 || p.comparavel !== anterior.comparavel)) {
        saida.push(atual);
        atual = [];
      }
      atual.push(p);
    }
    if (atual.length) saida.push(atual);
    return saida;
  };

  // ---------------------------------------------------------------- textos

  const nomeDe = (nomes, codigo) => {
    const m = nomes && nomes.get(codigo);
    return m ? `${m.nome} (${m.uf})` : `município ${codigo}`;
  };
  const listaDeMunicipios = (codigos, nomes, maximo = 3) => {
    const todos = codigos.map((codigo) => nomeDe(nomes, codigo));
    return todos.length > maximo ? `${todos.slice(0, maximo).join(', ')} e mais ${todos.length - maximo}` : juntar(todos);
  };
  const textoDoPeriodo = (periodo) => (periodo.de === periodo.ate ? `em ${periodo.de}` : `de ${periodo.de} a ${periodo.ate}`);
  const rotuloDaMetrica = (catalogo, nome) => (Object.hasOwn(catalogo.metricas, nome) ? catalogo.metricas[nome].rotulo : `"${nome}"`);
  const titulo = (catalogo, painel, nomes) => `${rotuloDaMetrica(catalogo, painel.metrica)} de `
    + `${listaDeMunicipios(painel.territorio.municipios, nomes)}, ${textoDoPeriodo(painel.periodo)}`;
  const descrever = (catalogo, painel, nomes) => `${titulo(catalogo, painel, nomes)}, em ${NOME_DO_GRAFICO[painel.grafico]}`;

  // O texto de um estado com o painel `i` trocado: é o link de uma sugestão.
  const estadoCom = (estado, i, painel) => E.serializar({ v: E.VERSAO, paineis: estado.paineis.map((p, j) => (j === i ? painel : p)) });

  // Códigos do IBGE que estavam num link que não abre: a tela sugere a série deles.
  // Sem lookbehind na expressão: o Safari só o aceita desde a versão 16.4, e o erro pararia o script inteiro.
  const codigosNoTexto = (texto) => [...new Set([...String(texto).matchAll(/(?:^|[^0-9])([1-5][0-9]{6})(?![0-9])/g)].map((m) => m[1]))]
    .slice(0, E.LIMITES.municipios);

  // A série inteira de um ou mais municípios, do primeiro ao último ano coletado.
  const painelDaSerie = (catalogo, codigos) => {
    const c = comFicha(catalogo).find((x) => x.anos.coletados && x.anos.coletados.length);
    if (!c || !codigos.length) return null;
    const anos = c.anos.coletados;
    return { metrica: c.metricas[0], territorio: { municipios: [...codigos] }, filtros: {}, detalhar: [],
      periodo: { de: String(anos[0]), ate: String(anos[anos.length - 1]) }, grafico: 'linha' };
  };

  // ---------------------------------------------------------------- escala

  const passoRedondo = (bruto) => {
    const potencia = 10 ** Math.floor(Math.log10(bruto));
    return [1, 2, 2.5, 5, 10].map((f) => f * potencia).find((v) => v >= bruto);
  };
  // Marcas redondas. Colunas partem do zero; a linha mostra a variação, sem zero.
  const escala = (minimo, maximo, comZero) => {
    let lo = comZero ? 0 : minimo;
    let hi = maximo;
    if (hi <= lo) {
      const folga = Math.max(1, Math.abs(hi) * 0.02);
      lo = comZero ? 0 : lo - folga;
      hi += folga;
    }
    const passo = Math.max(1, passoRedondo((hi - lo) / 4));
    const inicio = Math.floor(lo / passo) * passo;
    const fim = Math.ceil(hi / passo) * passo;
    const marcas = [];
    for (let v = inicio; v <= fim + passo / 2; v += passo) marcas.push(Math.round(v));
    return { inicio, fim, marcas };
  };
  // Faixa mínima do eixo da linha, relativa ao maior valor do período. Medido em 18/11/2026 na série de
  // 2013 a 2026: dentro de um trecho comparável, a variação de um ano para o seguinte tem mediana de 0,51% e
  // p99 de 3,31% (188 de 50.131 passagens acima de 5%); a série inteira de um município varia, do menor ao
  // maior valor, 9,9% do maior na mediana e 22,7% no p90. Com 20%, a variação anual típica ocupa cerca de
  // 2,5% da altura, uma de 5% (o limite de quebra não declarada do catálogo) ocupa um quarto, e em 9 de cada
  // 10 municípios a série cabe na faixa: aí a mesma variação percentual tem a mesma altura em todos os
  // gráficos. Quando os valores não cabem, o eixo cresce até cobri-los.
  const FAIXA_MINIMA = 0.2;
  const dominioDaLinha = (minimo, maximo) => {
    const faixa = Math.max(maximo - minimo, FAIXA_MINIMA * maximo);
    const lo = Math.max(0, (minimo + maximo) / 2 - faixa / 2);
    return { lo, hi: lo + faixa };
  };

  // Variação percentual dentro de cada trecho comparável, do primeiro ao último ano com valor dele. Nunca
  // através de uma quebra: de um trecho a outro, a série muda de base.
  const variacoes = (pontos) => {
    const trechos = new Map();
    for (const p of pontos) {
      if (p.valor === null) continue;
      if (!trechos.has(p.comparavel)) trechos.set(p.comparavel, []);
      trechos.get(p.comparavel).push(p);
    }
    return [...trechos.values()].filter((t) => t.length > 1).map((t) => ({
      de: t[0].ano, ate: t[t.length - 1].ano, pct: (t[t.length - 1].valor / t[0].valor - 1) * 100 }));
  };
  const porcentagem = (pct) => {
    const texto = Math.abs(pct).toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    return `${texto === '0,0' ? '' : pct > 0 ? '+' : '\u2212'}${texto}%`;
  };
  const textoDasVariacoes = (pontos) => variacoes(pontos).map((v) => `${porcentagem(v.pct)} de ${v.de} a ${v.ate}`).join('; ');

  // Um formato por eixo: em mil a partir de 10 mil, em milhões a partir de 1 milhão.
  const formatoDoEixo = (maximo) => {
    if (maximo >= 1e6) return (v) => (v ? `${(v / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 2 })} mi` : '0');
    if (maximo >= 1e4) return (v) => (v ? `${(v / 1e3).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} mil` : '0');
    return (v) => numero(v);
  };

  // ---------------------------------------------------------------- CSV

  const campo = (texto) => {
    const t = texto === null || texto === undefined ? '' : String(texto);
    return /[";\r\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };

  // Formato longo: uma linha por município e ano, com a situação, o trecho comparável, a fonte, o link exato,
  // a data da coleta e a nota do município, quando houver. As ressalvas completas e os documentos ficam na
  // ficha, cujo endereço vai em todas as linhas.
  const csv = (modelo, contexto) => {
    const { catalogo, painel, nomes, ficha, ressalvas, enderecoFicha } = contexto;
    const metrica = catalogo.metricas[painel.metrica];
    const fonte = `${metrica.fonte.orgao}, ${metrica.fonte.pesquisa}`;
    const notas = (ressalvas || []).filter((r) => r.tipo === 'nota_do_municipio');
    const quebras = (ressalvas || []).filter((r) => r.tipo === 'quebra_de_serie').map((r) => r.a_partir_de);
    const linhas = [['municipio', 'uf', 'codigo_ibge', 'ano', 'valor', 'unidade', 'situacao', 'comparavel_desde',
      'metrica', 'fonte', 'valor_exato', 'coletado_em', 'nota', 'quebras_de_serie_no_periodo', 'documentacao']];
    for (const codigo of modelo.municipios) {
      const m = nomes && nomes.get(codigo);
      for (const ano of modelo.anos) {
        const c = modelo.celulas[codigo][ano];
        const coletado = (ficha.anos[ano] || {}).coletado_em || '';
        const exato = c.situacao === 'valor' || c.situacao === 'sem_estimativa'
          ? metrica.fonte.acesso.url.replace('{municipio}', codigo).replace('{periodo}', String(ano)) : '';
        const nota = notas.filter((n) => n.municipios.includes(codigo) && n.periodos.includes(String(ano))).map((n) => n.texto).join(' ');
        linhas.push([m ? m.nome : '', m ? m.uf : '', codigo, ano, c.valor, metrica.unidade || '', SITUACOES[c.situacao],
          c.comparavel || '', metrica.rotulo, fonte, exato, coletado, nota, quebras.join(', '), enderecoFicha]);
      }
    }
    return `﻿${linhas.map((l) => l.map(campo).join(';')).join('\r\n')}\r\n`;
  };

  return {
    SITUACOES, NA_CELULA, NOME_DO_GRAFICO, numero, juntar, faixas, dataHora, cruzamento, comFicha, enderecoDaFicha,
    fichaDe, colunaDoValor, abrir, comCobertura, indexar, conferir, executar, situacaoDoAno, trechos, segmentos,
    nomeDe, listaDeMunicipios, textoDoPeriodo, rotuloDaMetrica, titulo, descrever, estadoCom, codigosNoTexto,
    painelDaSerie, escala, formatoDoEixo, csv, FAIXA_MINIMA, dominioDaLinha, variacoes, porcentagem, textoDasVariacoes,
  };
})();

if (typeof module !== 'undefined') module.exports = Consulta;

// ==================================================================== a tela

(() => {
  if (typeof document === 'undefined') return;
  const C = Consulta;
  const E = Especificacao;
  const raiz = document.getElementById('paineis');
  const placa = document.getElementById('entendido');
  const tituloDaPagina = document.getElementById('titulo');
  const lead = document.getElementById('lead');
  const anuncio = document.getElementById('anuncio');
  const fonteDoCatalogo = document.getElementById('catalogo');
  if (!raiz || !placa || !tituloDaPagina || !lead || !fonteDoCatalogo) return;
  const catalogo = JSON.parse(fonteDoCatalogo.textContent);
  const LEAD = lead.textContent;
  const ROTULO = tituloDaPagina.textContent;
  const SVG = 'http://www.w3.org/2000/svg';
  const RAIZ_DO_SITE = new URL('../', location.href).href;

  // ---------------------------------------------------------------- elementos

  const h = (nome, atributos, ...filhos) => {
    const e = document.createElement(nome);
    for (const [k, v] of Object.entries(atributos || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const f of filhos.flat(Infinity)) if (f !== null && f !== undefined && f !== false) e.append(f);
    return e;
  };
  const s = (nome, atributos, pai, texto) => {
    const e = document.createElementNS(SVG, nome);
    for (const [k, v] of Object.entries(atributos || {})) e.setAttribute(k, v);
    if (texto !== undefined) e.textContent = texto;
    if (pai) pai.appendChild(e);
    return e;
  };
  const icone = (caminho, classe) => {
    const svg = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true', class: classe });
    s('path', { d: caminho }, svg);
    return svg;
  };
  const SETA = () => icone('M5 3l5 5-5 5', 'seta');
  const XIS = () => icone('M4 4l8 8M12 4l-8 8', 'xis');
  const anunciar = (texto) => { anuncio.textContent = ''; setTimeout(() => { anuncio.textContent = texto; }, 30); };

  // ---------------------------------------------------------------- dados

  const dados = { fichas: [], falhas: {}, nomes: null, lista: null, series: new Map(), prontas: new Set() };
  const baixarJson = (url, opcoes) => fetch(url, opcoes).then((r) => {
    if (!r.ok) throw new Error(`o servidor respondeu ${r.status}`);
    return r.json();
  });
  let base = null;
  // As fichas decidem até onde cada série vai: pedidas sem cópia velha (o servidor confirma a atual). A lista
  // de municípios dá os nomes e a busca; sem ela, a tela mostra os códigos.
  const carregarBase = () => {
    base ??= Promise.all([
      Promise.all(C.comFicha(catalogo).map((c) => baixarJson(`../${C.enderecoDaFicha(c.arquivo)}`, { cache: 'no-cache' })
        .then((f) => ({ id: c.id, ficha: f }), (e) => ({ id: c.id, falha: e.message })))),
      baixarJson('../dados/municipios.json').catch(() => null),
    ]).then(([fichas, lista]) => {
      dados.fichas = fichas.filter((f) => f.ficha).map((f) => f.ficha);
      dados.falhas = Object.fromEntries(fichas.filter((f) => f.falha).map((f) => [f.id, f.falha]));
      if (lista) {
        dados.nomes = new Map(lista.map(([codigo, nome, uf]) => [codigo, { nome, uf }]));
        dados.lista = typeof BuscaMunicipios !== 'undefined' ? BuscaMunicipios.indexar(lista) : null;
      }
    });
    return base;
  };
  // O Parquet da série, pedido com a coleta da ficha no endereço: ficha nova, arquivo novo.
  const carregarSerie = (c, ficha) => {
    if (!dados.series.has(c.id)) {
      const url = `../${c.arquivo}?coleta=${encodeURIComponent(ficha.coletado_em || '')}`;
      const promessa = fetch(url).then((r) => {
        if (!r.ok) throw new Error(`o servidor respondeu ${r.status}`);
        return r.arrayBuffer();
      }).then((file) => Leitor.parquetReadObjects({ file }))
        .then((linhas) => C.indexar(linhas, C.colunaDoValor(ficha)));
      promessa.then(() => dados.prontas.add(c.id), () => dados.series.delete(c.id));
      dados.series.set(c.id, promessa);
    }
    return dados.series.get(c.id);
  };
  const recomecar = () => {
    base = null;
    dados.series.clear();
    dados.prontas.clear();
    atualizar();
  };

  // ---------------------------------------------------------------- estado

  let atual = null;   // o estado aberto: { v, paineis }
  const ir = (estado, foco, aviso) => {
    const r = E.serializar(estado);
    if (!r.ok) {
      const texto = r.mensagem || 'Esta mudança não cabe num link.';
      raiz.querySelectorAll('.nota--aviso').forEach((e) => e.remove());
      raiz.prepend(h('p', { class: 'nota nota--aviso', role: 'alert' }, texto));
      return false;
    }
    history.replaceState(null, '', `#${r.texto}`);
    atualizar(foco).then(() => { if (aviso) anunciar(aviso); });
    return true;
  };
  const trocar = (i, painel, foco, aviso) => ir({ v: E.VERSAO, paineis: atual.paineis.map((p, j) => (j === i ? painel : p)) }, foco, aviso);
  const href = (texto) => `#${texto}`;

  // ---------------------------------------------------------------- o que foi entendido

  const anosDaSerie = (c) => {
    const coletados = (c && c.anos && c.anos.coletados) || [];
    if (!coletados.length) return [];
    const anos = [];
    for (let ano = coletados[0]; ano <= coletados[coletados.length - 1]; ano += 1) anos.push(ano);
    return anos;
  };

  const seletorDeAno = (id, rotulo, valor, anos, c, aoMudar) => {
    const opcoes = [...new Set([...anos, Number(valor)])].sort((a, b) => a - b);
    const semDado = (c && c.anos && c.anos.sem_dado) || [];
    const select = h('select', { id, onchange: (e) => aoMudar(e.target.value) },
      opcoes.map((ano) => h('option', { value: String(ano), selected: String(ano) === valor },
        String(ano) + (semDado.includes(ano) ? ' (sem estimativa)' : anos.length && !anos.includes(ano) ? ' (fora da série)' : ''))));
    return h('label', { class: 'entendido__ano', for: id }, h('span', { class: 'sr-only' }, 'Período, '), h('span', null, rotulo), select);
  };

  // Busca de município por nome, para acrescentar à consulta. Enter escolhe o primeiro; as setas andam na lista.
  const adicionar = (prefixo, jaTem, aoEscolher, rotulo) => {
    const id = `${prefixo}-busca`;
    const cheio = jaTem.length >= E.LIMITES.municipios;
    const semLista = !dados.lista;
    const aviso = h('p', { class: 'adicionar__aviso', id: `${id}-aviso`, 'aria-live': 'polite' },
      cheio ? `Até ${E.LIMITES.municipios} municípios por painel.` : semLista ? 'A lista de municípios não carregou. Recarregue a página.' : '');
    const lista = h('ul', { class: 'adicionar__opcoes', id: `${id}-opcoes` });
    const campo = h('input', { id, type: 'search', autocomplete: 'off', spellcheck: 'false', placeholder: 'Ex.: Santos, ou Santos SP',
      'aria-describedby': `${id}-aviso`, disabled: cheio || semLista });
    const botoes = () => [...lista.querySelectorAll('button')];
    const mostrar = () => {
      lista.replaceChildren();
      const achados = C && dados.lista ? BuscaMunicipios.procurar(dados.lista, campo.value) : null;
      if (achados === null) { aviso.textContent = ''; return; }
      const livres = achados.filter((m) => !jaTem.includes(m.codigo));
      for (const m of livres.slice(0, 8)) {
        lista.append(h('li', null, h('button', { type: 'button', 'data-codigo': m.codigo, onclick: () => aoEscolher(m.codigo, id) },
          h('span', null, m.nome), ' ', h('span', { class: 'escudo' }, m.uf))));
      }
      aviso.textContent = livres.length === 0 ? 'Nenhum município com esse nome. Confira a grafia.'
        : livres.length > 8 ? `${livres.length} municípios encontrados. Mostrando os 8 primeiros; digite mais letras.`
          : `${livres.length} ${livres.length === 1 ? 'município encontrado' : 'municípios encontrados'}. Enter escolhe o primeiro.`;
    };
    campo.addEventListener('input', mostrar);
    campo.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const primeiro = botoes()[0];
        if (primeiro) primeiro.click();
      } else if (e.key === 'ArrowDown' && botoes().length) {
        e.preventDefault();
        botoes()[0].focus();
      }
    });
    lista.addEventListener('keydown', (e) => {
      const b = botoes();
      const i = b.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' && i < b.length - 1) { e.preventDefault(); b[i + 1].focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); (i > 0 ? b[i - 1] : campo).focus(); }
      if (e.key === 'Escape') { e.preventDefault(); campo.focus(); }
    });
    return h('div', { class: 'adicionar' }, h('label', { for: id }, rotulo), campo, aviso, lista);
  };

  // A interpretação de um painel, em campos que corrigem a especificação. Mudar um campo troca o endereço.
  const entendido = (i, painel, validacao) => {
    const prefixo = `p${i}`;
    const metrica = Object.hasOwn(catalogo.metricas, painel.metrica) ? catalogo.metricas[painel.metrica] : null;
    const c = validacao && validacao.ok ? C.cruzamento(catalogo, validacao.cruzamento)
      : C.comFicha(catalogo).find((x) => x.metricas.includes(painel.metrica)) || null;
    const coberto = c ? C.comCobertura(catalogo, dados.fichas).cruzamentos_da_origem.find((x) => x.id === c.id) : null;
    const anos = anosDaSerie(coberto);
    const codigos = painel.territorio.municipios;
    const comPeriodo = (de, ate) => ({ ...painel, periodo: { de, ate } });
    const umaCelula = codigos.length === 1 && painel.periodo.de === painel.periodo.ate;

    const fichas = h('ul', { class: 'municipios-escolhidos' }, codigos.map((codigo) => {
      const m = dados.nomes && dados.nomes.get(codigo);
      return h('li', { class: 'escolhido' },
        m ? h('a', { href: `../municipio/${codigo}/` }, m.nome) : h('span', null, `Código ${codigo}`),
        m ? h('span', { class: 'escudo' }, m.uf) : null,
        codigos.length > 1 ? h('button', { type: 'button', class: 'tirar', id: `${prefixo}-tirar-${codigo}`,
          'aria-label': `Tirar ${C.nomeDe(dados.nomes, codigo)} da consulta`,
          onclick: () => trocar(i, { ...painel, territorio: { ...painel.territorio, municipios: codigos.filter((x) => x !== codigo) } },
            `${prefixo}-busca`, `${C.nomeDe(dados.nomes, codigo)} saiu da consulta.`) }, XIS()) : null);
    }));
    const grafico = h('fieldset', { class: 'entendido__grupo entendido__grafico' }, h('legend', null, 'Gráfico'),
      h('div', { class: 'segmentos' }, ['linha', 'barras', 'tabela', 'numero'].map((g) => {
        const id = `${prefixo}-grafico-${g}`;
        const bloqueado = g === 'numero' && !umaCelula && painel.grafico !== 'numero';
        return h('label', { class: 'segmento', for: id },
          h('input', { type: 'radio', name: `${prefixo}-grafico`, id, value: g, checked: painel.grafico === g, disabled: bloqueado,
            'aria-describedby': g === 'numero' ? `${prefixo}-numero-ajuda` : null,
            onchange: () => trocar(i, { ...painel, grafico: g }, id) }),
          h('span', null, g === 'numero' ? 'Número' : C.NOME_DO_GRAFICO[g][0].toUpperCase() + C.NOME_DO_GRAFICO[g].slice(1)));
      })),
      h('p', { class: 'entendido__ajuda', id: `${prefixo}-numero-ajuda` }, 'Número em destaque: com um município e um ano.'));

    return h('form', { class: 'entendido', 'aria-label': `O que foi entendido${atual && atual.paineis.length > 1 ? ` no painel ${i + 1}` : ''}`,
      onsubmit: (e) => e.preventDefault() },
    h('div', { class: 'entendido__grupo' }, h('span', { class: 'entendido__rotulo' }, 'Métrica'),
      h('span', { class: 'entendido__valor' }, metrica ? `${metrica.rotulo}${metrica.unidade ? `, em ${metrica.unidade}` : ''}`
        : `"${painel.metrica}", que não está publicada no site`)),
    h('fieldset', { class: 'entendido__grupo' }, h('legend', null, codigos.length > 1 ? 'Municípios' : 'Município'), fichas,
      adicionar(prefixo, codigos, (codigo, foco) => trocar(i, { ...painel, territorio: { ...painel.territorio, municipios: [...codigos, codigo] } },
        foco, `${C.nomeDe(dados.nomes, codigo)} entrou na consulta.`), 'Adicionar município')),
    h('div', { class: 'entendido__grupo entendido__periodo' }, h('span', { class: 'entendido__rotulo' }, 'Período'),
      seletorDeAno(`${prefixo}-de`, 'de', painel.periodo.de, anos, coberto, (v) => trocar(i,
        comPeriodo(v, Number(v) > Number(painel.periodo.ate) ? v : painel.periodo.ate), `${prefixo}-de`)),
      seletorDeAno(`${prefixo}-ate`, 'até', painel.periodo.ate, anos, coberto, (v) => trocar(i,
        comPeriodo(Number(v) < Number(painel.periodo.de) ? v : painel.periodo.de, v), `${prefixo}-ate`))),
    grafico);
  };

  // ---------------------------------------------------------------- desenho

  const MIN_LARGURA = 260;
  const largura = (caixa) => Math.max(MIN_LARGURA, Math.floor(caixa.getBoundingClientRect().width));
  const svgNovo = (W, H) => s('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, 'aria-hidden': 'true', class: 'grafico' });
  const larguraDoTexto = (texto) => texto.length * 6.6;

  // Linha ou colunas de uma série no tempo. Cada ano é uma faixa da mesma largura, também os que não têm dado:
  // o espaço do tempo fica honesto. A linha se interrompe nas quebras e nos anos sem valor.
  const serie = (caixa, o) => {
    const W = largura(caixa);
    const H = o.altura;
    const colunas = o.tipo === 'colunas';
    const valores = o.pontos.filter((p) => p.valor !== null).map((p) => p.valor);
    let e = C.escala(0, 1, true);
    if (valores.length && colunas) e = C.escala(Math.min(...valores), Math.max(...valores), true);
    else if (valores.length) {
      const d = C.dominioDaLinha(Math.min(...valores), Math.max(...valores));
      e = C.escala(d.lo, d.hi, false);
    }
    const fmt = C.formatoDoEixo(e.fim);
    const esq = 8 + Math.max(...e.marcas.map((m) => larguraDoTexto(fmt(m))));
    const dir = colunas ? 6 : 14;
    const topo = 34;
    const base = H - 26;
    const passo = (W - esq - dir) / o.anos.length;
    const x = (ano) => esq + (ano - o.anos[0] + 0.5) * passo;
    const y = (v) => base - ((base - topo) * (v - e.inicio)) / (e.fim - e.inicio);
    const svg = svgNovo(W, H);
    for (const m of e.marcas) {
      s('line', { class: 'grade', x1: esq, x2: W - dir, y1: y(m), y2: y(m) }, svg);
      s('text', { class: 'eixo', x: esq - 8, y: y(m) + 4, 'text-anchor': 'end' }, svg, fmt(m));
    }
    // Faixas dos anos sem dado, com o nome quando cabe.
    let grupo = null;
    const faixas = [];
    for (const p of o.pontos) {
      const tipo = p.situacao === 'sem_dado' || p.situacao === 'fora_da_cobertura' ? p.situacao : null;
      if (tipo && grupo && grupo.tipo === tipo) grupo.ate = p.ano;
      else if (tipo) faixas.push(grupo = { tipo, de: p.ano, ate: p.ano });
      else grupo = null;
    }
    for (const f of faixas) {
      const x0 = esq + (f.de - o.anos[0]) * passo;
      const w = (f.ate - f.de + 1) * passo;
      s('rect', { class: 'faixa', x: x0, y: topo - 6, width: w, height: base - topo + 6 }, svg);
      const texto = f.tipo === 'sem_dado' ? 'sem estimativa' : 'ainda sem dado';
      if (w >= larguraDoTexto(texto) + 8) s('text', { class: 'eixo', x: x0 + w / 2, y: base - 8, 'text-anchor': 'middle' }, svg, texto);
    }
    // Quebras de série: um fio entre o último ano de um trecho e o primeiro do seguinte.
    for (const q of o.quebras) {
      if (q <= o.anos[0] || q > o.anos[o.anos.length - 1]) continue;
      const xq = esq + (q - o.anos[0]) * passo;
      s('line', { class: 'quebra', x1: xq, x2: xq, y1: topo - 20, y2: base }, svg);
      const ancora = xq < esq + 30 ? 'start' : xq > W - dir - 30 ? 'end' : 'middle';
      s('text', { class: 'eixo quebra__rotulo', x: xq, y: topo - 23, 'text-anchor': ancora }, svg, `quebra ${q}`);
    }
    // Anos no eixo: todos, se couberem; senão, um sim e outro não, sempre com o último.
    const cada = Math.ceil(36 / passo);
    o.anos.forEach((ano, k) => {
      const ultimo = k === o.anos.length - 1;
      if ((k % cada === 0 && !(o.anos.length - 1 - k < cada && !ultimo)) || ultimo) {
        s('text', { class: 'eixo', x: x(ano), y: base + 18, 'text-anchor': 'middle' }, svg, String(ano));
      }
    });
    if (colunas) {
      const w = Math.max(2, Math.min(24, passo - 4));
      for (const p of o.pontos) {
        if (p.valor === null) continue;
        const alto = Math.max(1, base - y(p.valor));
        const r = Math.min(4, w / 2, alto);
        const x0 = x(p.ano) - w / 2;
        s('path', { class: 'barra', d: `M${x0},${base}v${-(alto - r)}q0,${-r} ${r},${-r}h${w - 2 * r}q${r},0 ${r},${r}v${alto - r}z` }, svg);
      }
    } else {
      for (const trecho of C.segmentos(o.pontos)) {
        if (trecho.length > 1) {
          s('path', { class: 'linha', d: trecho.map((p, k) => `${k ? 'L' : 'M'}${x(p.ano).toFixed(1)},${y(p.valor).toFixed(1)}`).join('') }, svg);
        }
      }
      for (const p of o.pontos) if (p.valor !== null) s('circle', { class: 'ponto', cx: x(p.ano), cy: y(p.valor), r: 4 }, svg);
    }
    s('line', { class: 'base', x1: esq, x2: W - dir, y1: base, y2: base }, svg);
    // O último valor, escrito na ponta: os outros ficam no eixo, na dica e na tabela.
    const ultimo = [...o.pontos].reverse().find((p) => p.valor !== null);
    if (ultimo) {
      const texto = C.numero(ultimo.valor);
      const xu = Math.min(x(ultimo.ano) + 6, W - 2);
      const yu = colunas ? y(ultimo.valor) - 8 : y(ultimo.valor) - 10;
      s('text', { class: 'valor', x: xu, y: Math.max(12, yu), 'text-anchor': 'end' }, svg, texto);
    }
    const mira = s('line', { class: 'mira', x1: 0, x2: 0, y1: topo - 6, y2: base, visibility: 'hidden' }, svg);
    caixa.alvos = o.pontos.map((p) => ({ x: x(p.ano), y: p.valor === null ? null : y(p.valor), texto: o.dica(p) }));
    caixa.mira = (k) => {
      if (k === null) { mira.setAttribute('visibility', 'hidden'); return; }
      mira.setAttribute('x1', caixa.alvos[k].x);
      mira.setAttribute('x2', caixa.alvos[k].x);
      mira.setAttribute('visibility', 'visible');
    };
    caixa.eixo = 'x';
    return svg;
  };

  // Barras horizontais, uma por município, num ano só. Partem do zero; o valor fica na ponta.
  const barras = (caixa, itens) => {
    const W = largura(caixa);
    const estreito = W < 520;
    const valores = itens.filter((i) => i.valor !== null).map((i) => i.valor);
    const maximo = Math.max(1, ...valores);
    const esq = estreito ? 0 : Math.min(220, 12 + Math.max(...itens.map((i) => larguraDoTexto(i.rotulo))));
    const folga = larguraDoTexto(C.numero(maximo)) + 12;
    const linha = estreito ? 44 : 30;
    const H = linha * itens.length + 8;
    const util = W - esq - folga;
    const svg = svgNovo(W, H);
    caixa.alvos = [];
    itens.forEach((item, k) => {
      const y0 = 4 + k * linha;
      const yb = estreito ? y0 + 20 : y0 + 5;
      s('text', { class: 'rotulo', x: estreito ? 0 : esq - 10, y: estreito ? y0 + 13 : y0 + 18, 'text-anchor': estreito ? 'start' : 'end' }, svg, item.rotulo);
      if (item.valor === null) {
        s('text', { class: 'eixo', x: esq, y: yb + 13 }, svg, C.NA_CELULA[item.situacao]);
      } else {
        const w = Math.max(1, (util * item.valor) / maximo);
        const r = Math.min(4, w);
        s('path', { class: 'barra', d: `M${esq},${yb}h${w - r}q${r},0 ${r},${r}v${18 - 2 * r}q0,${r} ${-r},${r}h${-(w - r)}z` }, svg);
        s('text', { class: 'valor', x: esq + w + 6, y: yb + 13 }, svg, C.numero(item.valor));
      }
      caixa.alvos.push({ x: esq + 4, y: yb + 9, texto: item.dica });
    });
    if (!estreito) s('line', { class: 'base', x1: esq + 0.5, x2: esq + 0.5, y1: 2, y2: H - 2 }, svg);
    caixa.mira = () => {};
    caixa.eixo = 'y';
    return svg;
  };

  // Dica: a mesma no ponteiro e no teclado. O texto entra por textContent.
  const dica = h('div', { class: 'dica', hidden: true });
  document.body.append(dica);
  const mostrarDica = (caixa, k, cx, cy) => {
    const alvo = caixa.alvos[k];
    caixa.mira(k);
    dica.textContent = alvo.texto;
    dica.hidden = false;
    const x = Math.min(cx + 14, window.innerWidth - dica.offsetWidth - 8);
    dica.style.left = `${Math.max(8, x)}px`;
    dica.style.top = `${Math.min(cy + 16, window.innerHeight - dica.offsetHeight - 8)}px`;
  };
  const esconderDica = (caixa) => {
    dica.hidden = true;
    if (caixa && caixa.mira) caixa.mira(null);
  };
  const interativo = (caixa, rotulo) => {
    const leitura = h('p', { class: 'sr-only', 'aria-live': 'polite' });
    caixa.setAttribute('tabindex', '0');
    caixa.setAttribute('role', 'group');
    caixa.setAttribute('aria-label', `${rotulo} Use as setas para ler os valores; a tabela abaixo tem os mesmos números.`);
    caixa.after(leitura);
    let indice = null;
    const proximo = (e) => {
      const svg = caixa.querySelector('svg');
      const r = svg.getBoundingClientRect();
      const escalaX = r.width / svg.viewBox.baseVal.width;
      const pos = caixa.eixo === 'x' ? (e.clientX - r.left) / escalaX : (e.clientY - r.top) / escalaX;
      let melhor = 0;
      caixa.alvos.forEach((a, k) => {
        if (Math.abs((caixa.eixo === 'x' ? a.x : a.y) - pos) < Math.abs((caixa.eixo === 'x' ? caixa.alvos[melhor].x : caixa.alvos[melhor].y) - pos)) melhor = k;
      });
      return melhor;
    };
    caixa.addEventListener('pointermove', (e) => { if (caixa.alvos && caixa.alvos.length) mostrarDica(caixa, proximo(e), e.clientX, e.clientY); });
    caixa.addEventListener('pointerleave', () => esconderDica(caixa));
    caixa.addEventListener('keydown', (e) => {
      if (!caixa.alvos || !caixa.alvos.length) return;
      const n = caixa.alvos.length;
      const antes = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (e.key === 'Escape') { esconderDica(caixa); return; }
      if (antes === undefined && e.key !== 'Home' && e.key !== 'End') return;
      e.preventDefault();
      indice = e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : indice === null ? (antes > 0 ? 0 : n - 1) : Math.min(n - 1, Math.max(0, indice + antes));
      const svg = caixa.querySelector('svg');
      const r = svg.getBoundingClientRect();
      const escalaX = r.width / svg.viewBox.baseVal.width;
      const alvo = caixa.alvos[indice];
      mostrarDica(caixa, indice, r.left + alvo.x * escalaX, r.top + (alvo.y === null ? r.height / 2 : alvo.y * escalaX));
      leitura.textContent = alvo.texto;
    });
    caixa.addEventListener('blur', () => { esconderDica(caixa); indice = null; });
  };

  // Cada caixa guarda como se desenhar: na mudança de largura, desenha de novo.
  const caixas = new Set();
  const desenhar = (caixa) => {
    const W = largura(caixa);
    if (caixa.dataset.largura === String(W) && caixa.querySelector('svg')) return;
    caixa.dataset.largura = String(W);
    caixa.replaceChildren(caixa.desenho(caixa));
  };
  let espera;
  window.addEventListener('resize', () => {
    clearTimeout(espera);
    espera = setTimeout(() => { for (const caixa of caixas) if (caixa.isConnected) desenhar(caixa); else caixas.delete(caixa); }, 150);
  });
  const novaCaixa = (desenho, rotulo, extra) => {
    const caixa = h('div', { class: 'grafico-consulta', 'data-titulo': extra || '' });
    caixa.desenho = desenho;
    caixas.add(caixa);
    return { caixa, depois: () => { desenhar(caixa); interativo(caixa, rotulo); } };
  };

  // ---------------------------------------------------------------- resultado

  const unidade = (metrica) => metrica.unidade || '';
  const comUnidade = (metrica, valor) => `${C.numero(valor)} ${unidade(metrica)}`.trim();

  const dicaDoPonto = (metrica, nome, ressalvas) => (p) => {
    const quebra = ressalvas.some((r) => r.tipo === 'quebra_de_serie' && Number(r.a_partir_de) === p.ano);
    const valor = p.valor === null ? C.NA_CELULA[p.situacao] : comUnidade(metrica, p.valor);
    return `${valor} · ${nome ? `${nome}, ` : ''}${p.ano}${quebra ? ' · começo de trecho, depois de quebra de série' : ''}`;
  };

  const figura = (painel, modelo, metrica, ressalvas, depois) => {
    const quebras = ressalvas.filter((r) => r.tipo === 'quebra_de_serie').map((r) => Number(r.a_partir_de));
    const nomes = dados.nomes;
    const varios = modelo.municipios.length > 1;
    const umAno = modelo.anos.length === 1;
    const elementos = [];
    if (painel.grafico === 'numero') {
      const codigo = modelo.municipios[0];
      const c = modelo.celulas[codigo][modelo.anos[0]];
      return [h('figure', { class: 'destaque' },
        c.valor === null ? h('p', { class: 'destaque__numero destaque__numero--ausente' }, C.NA_CELULA[c.situacao])
          : h('p', { class: 'destaque__numero' }, h('data', { value: String(c.valor) }, C.numero(c.valor)), ' ', h('span', { class: 'destaque__unidade' }, unidade(metrica))),
        c.valor === null ? h('p', { class: 'nota' }, 'A ausência não é zero: o IBGE não publicou estimativa para este município neste ano.') : null)];
    }
    if (painel.grafico === 'tabela') return [];
    const rotuloDoGrafico = painel.grafico === 'linha' && !umAno ? 'Gráfico de linha' : painel.grafico === 'barras' && !umAno ? 'Gráfico de colunas' : 'Gráfico de barras';
    if (umAno) {
      // Num ano só, a comparação é entre municípios: barras, do maior para o menor.
      const itens = modelo.municipios.map((codigo) => {
        const c = modelo.celulas[codigo][modelo.anos[0]];
        const nome = C.nomeDe(nomes, codigo);
        return { rotulo: nome, valor: c.valor, situacao: c.situacao,
          dica: `${c.valor === null ? C.NA_CELULA[c.situacao] : comUnidade(metrica, c.valor)} · ${nome}, ${modelo.anos[0]}` };
      }).sort((a, b) => (b.valor ?? -1) - (a.valor ?? -1));
      if (painel.grafico === 'linha') {
        elementos.push(h('p', { class: 'nota-grafico' }, 'Com um ano só, a linha seria um ponto por município: as barras mostram os mesmos valores. Escolha um período para ver a linha.'));
      }
      const { caixa, depois: d } = novaCaixa((cx) => barras(cx, itens), `${rotuloDoGrafico}: ${metrica.rotulo}, ${modelo.anos[0]}.`);
      depois.push(d);
      elementos.push(h('figure', { class: 'figura' }, caixa));
      return elementos;
    }
    const tipo = painel.grafico === 'barras' ? 'colunas' : 'linha';
    const pontosDe = (codigo) => modelo.anos.map((ano) => ({ ano, ...modelo.celulas[codigo][ano] }));
    if (!varios) {
      const codigo = modelo.municipios[0];
      const variacao = C.textoDasVariacoes(pontosDe(codigo));
      if (variacao) elementos.push(h('p', { class: 'variacao' }, h('span', { class: 'variacao__rotulo' }, 'Variação em cada trecho comparável: '), variacao));
      const { caixa, depois: d } = novaCaixa((cx) => serie(cx, { anos: modelo.anos, pontos: pontosDe(codigo), tipo, quebras, altura: 260,
        dica: dicaDoPonto(metrica, null, ressalvas) }), `${rotuloDoGrafico}: ${C.titulo(catalogo, painel, nomes)}.`);
      depois.push(d);
      caixa.dataset.variacao = variacao;
      elementos.push(h('figure', { class: 'figura' }, caixa));
    } else {
      // Um gráfico por município, cada um na própria escala: um município de 5 mil habitantes ao lado de um
      // de 11 milhões viraria uma reta no chão. A comparação entre eles fica nas barras de um ano e na tabela.
      elementos.push(h('p', { class: 'nota-grafico' }, tipo === 'linha'
        ? `Um gráfico por município. Cada eixo cobre pelo menos ${Math.round(C.FAIXA_MINIMA * 100)}% do maior valor do município no período, `
          + 'para que uma variação pequena não pareça grande; quando os valores cabem nessa faixa, a mesma variação percentual tem a mesma '
          + 'altura em todos os gráficos. A porcentagem é medida dentro de cada trecho comparável, nunca através de uma quebra.'
        : 'Um gráfico por município, cada um na própria escala, a partir do zero. A porcentagem é medida dentro de cada trecho comparável, nunca através de uma quebra.'));
      elementos.push(h('div', { class: 'multiplos' }, modelo.municipios.map((codigo) => {
        const nome = C.nomeDe(nomes, codigo);
        const { caixa, depois: d } = novaCaixa((cx) => serie(cx, { anos: modelo.anos, pontos: pontosDe(codigo), tipo, quebras, altura: 200,
          dica: dicaDoPonto(metrica, nome, ressalvas) }), `${rotuloDoGrafico}: ${metrica.rotulo} de ${nome}, ${C.textoDoPeriodo(painel.periodo)}.`, nome);
        depois.push(d);
        const variacao = C.textoDasVariacoes(pontosDe(codigo));
        caixa.dataset.variacao = variacao;
        return h('figure', { class: 'multiplo' }, h('figcaption', null, h('span', { class: 'multiplo__titulo' }, nome),
          variacao ? h('span', { class: 'variacao' }, variacao) : null), caixa);
      })));
    }
    // A legenda diz o que cada marca quer dizer, e só o que aparece no gráfico.
    const tem = (situacao) => modelo.municipios.some((codigo) => modelo.anos.some((ano) => modelo.celulas[codigo][ano].situacao === situacao));
    const itens = [];
    if (quebras.length) itens.push(h('span', { class: 'item' }, h('span', { class: 'chave chave--quebra' }), 'Quebra de série: os valores de um lado e do outro não se comparam'));
    if (tem('sem_dado')) itens.push(h('span', { class: 'item' }, h('span', { class: 'chave chave--faixa' }), 'Ano sem estimativa do IBGE'));
    if (tem('fora_da_cobertura')) itens.push(h('span', { class: 'item' }, h('span', { class: 'chave chave--faixa' }), 'Ano ainda sem dado'));
    if (tem('sem_estimativa')) itens.push(h('span', { class: 'item' }, `Sem ${tipo === 'linha' ? 'ponto' : 'coluna'}: sem estimativa para o município no ano`));
    if (tipo === 'linha' && !varios) itens.push(h('span', { class: 'item' }, `Eixo com pelo menos ${Math.round(C.FAIXA_MINIMA * 100)}% do maior valor do período`));
    if (itens.length) elementos.push(h('p', { class: 'legenda-consulta' }, itens));
    return elementos;
  };

  // As ressalvas da validação, junto do resultado: quebras com os documentos, anos sem dado, notas de município.
  // Mais as ausências que os dados mostram: município sem estimativa em algum ano.
  const blocoRessalvas = (i, modelo, ressalvas) => {
    const nomes = dados.nomes;
    const itens = ressalvas.map((r) => {
      if (r.tipo === 'quebra_de_serie') {
        return h('li', null, h('strong', null, `Quebra de série em ${r.a_partir_de}. `), r.texto,
          r.documentos ? h('span', { class: 'documentos' }, ' Documentos do IBGE: ',
            r.documentos.map((d, k) => [k ? '; ' : '', h('a', { href: d.url }, d.titulo)]), '.') : null);
      }
      if (r.tipo === 'sem_dado') return h('li', null, h('strong', null, `Sem dado em ${C.juntar(r.periodos)}. `), r.texto || '');
      if (r.tipo === 'fora_da_cobertura') return h('li', null, h('strong', null, 'Depois do último ano coletado. '), r.texto);
      if (r.tipo === 'nota_do_municipio') {
        return h('li', null, h('strong', null, `${C.juntar(r.municipios.map((m) => C.nomeDe(nomes, m)))} em ${C.juntar(r.periodos)}. `), r.texto);
      }
      return h('li', null, r.texto);
    });
    for (const codigo of modelo.municipios) {
      const sem = modelo.anos.filter((ano) => modelo.celulas[codigo][ano].situacao === 'sem_estimativa');
      if (sem.length) {
        itens.push(h('li', null, h('strong', null, `${C.nomeDe(nomes, codigo)} sem estimativa: ${C.faixas(sem)}. `),
          'A tabela do IBGE não traz valor para o município nesses anos. A ausência não é zero.'));
      }
    }
    if (!itens.length) return null;
    return h('section', { class: 'ressalvas', 'aria-labelledby': `p${i}-ressalvas` },
      h('h3', { id: `p${i}-ressalvas` }, 'Antes de comparar'), h('ul', null, itens));
  };

  // A tabela com os mesmos valores do gráfico: anos nas linhas, municípios nas colunas. Quando o período
  // atravessa uma quebra, cada trecho comparável abre com uma linha que diz de onde até onde ele vai.
  const celulaDaTabela = (c) => (c.valor === null ? h('td', { class: 'ausente' }, C.NA_CELULA[c.situacao])
    : h('td', null, h('data', { value: String(c.valor) }, C.numero(c.valor))));
  const tabela = (i, painel, modelo, metrica) => {
    const nomes = dados.nomes;
    const grupos = C.trechos(modelo);
    const comQuebra = new Set(grupos.filter((g) => g.tipo === 'linha').map((g) => g.comparavel)).size > 1;
    const colunas = modelo.municipios.length + 1;
    const corpo = grupos.map((g, k) => {
      const linhas = g.anos.map((ano) => h('tr', null, h('th', { scope: 'row' }, String(ano)),
        modelo.municipios.map((codigo) => celulaDaTabela(modelo.celulas[codigo][ano]))));
      if (!comQuebra) return linhas;
      const de = g.anos[0];
      const ate = g.anos[g.anos.length - 1];
      const anos = de === ate ? String(de) : `${de} a ${ate}`;
      const texto = g.tipo === 'linha' ? `${k && grupos.slice(0, k).some((x) => x.tipo === 'linha') ? 'Depois da quebra de série: ' : ''}${anos}, comparáveis entre si`
        : g.tipo === 'sem_dado' ? `${anos}: sem estimativa do IBGE` : `${anos}: ainda sem dado`;
      return [h('tr', { class: 'trecho' }, h('th', { scope: 'rowgroup', colspan: String(colunas) }, texto)), ...linhas];
    });
    const id = `p${i}-tabela`;
    const legenda = h('p', { class: 'tabela__titulo', id: `${id}-legenda` },
      `Tabela: ${metrica.rotulo.toLowerCase()}, em ${unidade(metrica)}, ${C.listaDeMunicipios(modelo.municipios, nomes, 10)}, ${C.textoDoPeriodo(painel.periodo)}`);
    const umAno = modelo.anos.length === 1 && modelo.municipios.length > 1;
    const cabeca = umAno ? h('tr', null, h('th', { scope: 'col' }, 'Município'), h('th', { scope: 'col' }, String(modelo.anos[0])))
      : h('tr', null, h('th', { scope: 'col' }, 'Ano'), modelo.municipios.map((codigo) => h('th', { scope: 'col' }, C.nomeDe(nomes, codigo))));
    const linhas = umAno ? modelo.municipios.map((codigo) => h('tr', null, h('th', { scope: 'row' }, C.nomeDe(nomes, codigo)), celulaDaTabela(modelo.celulas[codigo][modelo.anos[0]]))) : corpo;
    return [legenda, h('div', { class: 'rolagem', role: 'region', 'aria-labelledby': `${id}-legenda`, tabindex: '0' },
      h('table', { class: 'tabela tabela--consulta', id, 'aria-labelledby': `${id}-legenda` }, h('thead', null, cabeca), h('tbody', null, linhas)))];
  };

  const fonteEMetodo = (modelo, metrica, c, ficha) => {
    const f = metrica.fonte;
    const nomes = dados.nomes;
    const base = catalogo.bases[c.base] || {};
    const coletas = [...new Set(modelo.coletados.map((ano) => ficha.anos[ano].coletado_em))].sort();
    const exato = (codigo) => f.acesso.url.replace('{municipio}', codigo).replace('{periodo}', modelo.coletados.join(','));
    const ficheiro = new URL(C.enderecoDaFicha(c.arquivo), RAIZ_DO_SITE).href;
    const arquivo = new URL(c.arquivo, RAIZ_DO_SITE).href;
    const licenca = base.licenca ? `${base.licenca.atribuicao}. ${base.licenca.url ? '' : 'Licença não declarada na origem.'}` : '';
    const supressao = ficha.supressao && ficha.supressao.aplica === false ? `Sem supressão: ${ficha.supressao.motivo}` : 'Ver a ficha da série.';
    const linha = (dt, ...dd) => [h('dt', null, dt), h('dd', null, ...dd)];
    return h('details', { class: 'fonte' },
      h('summary', null, 'Fonte e método', SETA()),
      h('section', { class: 'fonte__bloco' },
        h('dl', { class: 'ficha' },
          linha('Órgão e pesquisa', `${f.orgao}, ${f.pesquisa}.`),
          linha('Acesso', 'Valores exatos na API da SIDRA, em JSON: ',
            modelo.municipios.map((codigo, k) => [k ? ', ' : '', h('a', { href: exato(codigo) }, C.nomeDe(nomes, codigo))]),
            '. ', h('a', { href: f.tabela }, 'Tabela completa'), ', para conferir outro município ou ano.'),
          linha('Período de referência', modelo.coletados.length ? C.faixas(modelo.coletados) : 'nenhum ano coletado no período pedido'),
          linha('Coleta', coletas.length === 1 ? `${C.dataHora(coletas[0])}, um pedido à SIDRA por ano.`
            : coletas.length ? `De ${C.dataHora(coletas[0])} a ${C.dataHora(coletas[coletas.length - 1])}, um pedido à SIDRA por ano.` : '—'),
          linha('Arquivo usado', h('a', { href: arquivo }, c.arquivo.split('/').pop()), ', lido neste navegador, e a ',
            h('a', { href: ficheiro }, 'ficha da série'), ', com o endereço, a data, o tamanho e o SHA-256 da resposta de cada ano.'),
          linha('Como a consulta foi feita', 'Cada número é uma linha do arquivo, do município e do ano pedidos, sem soma nem cálculo.'),
          (metrica.ressalvas || []).length ? linha('Ressalvas', h('ul', null, metrica.ressalvas.map((r) => h('li', null, r)))) : null,
          linha('Privacidade', supressao),
          linha('Licença', licenca))));
  };

  const botaoTentar = () => h('button', { type: 'button', class: 'acao', onclick: recomecar }, 'Tentar de novo');

  // Recusa, ambiguidade ou falha: mensagem e sugestões no lugar do gráfico, nunca um gráfico vazio.
  const blocoRecusa = (i, titulo, mensagem, sugestoes, extra) => h('div', { class: 'recusa' },
    h('h3', null, titulo),
    h('p', null, mensagem),
    sugestoes && sugestoes.length ? [h('p', { class: 'recusa__chamada' }, sugestoes.length === 1 ? 'Consulta que existe:' : 'Consultas que existem:'),
      h('ul', { class: 'sugestoes' }, sugestoes.map((sg) => h('li', null, h('a', { href: sg.href }, sg.texto, SETA()))))] : null,
    extra || null);

  const sugestoesDe = (i, lista) => lista.map((sg) => {
    const r = C.estadoCom(atual, i, sg.painel);
    return r.ok ? { href: href(r.texto), texto: C.descrever(catalogo, sg.painel, dados.nomes) } : null;
  }).filter(Boolean);

  const exportar = (i, painel, modelo, metrica, ressalvas, c, ficha, secao) => {
    const nome = `${painel.metrica}-${modelo.municipios.join('-')}-${painel.periodo.de}-${painel.periodo.ate}`;
    const contexto = { catalogo, painel, nomes: dados.nomes, ficha, ressalvas,
      enderecoFicha: new URL(C.enderecoDaFicha(c.arquivo), RAIZ_DO_SITE).href };
    return h('div', { class: 'exportar' },
      h('button', { type: 'button', class: 'acao', onclick: () => baixar(`${nome}.csv`, new Blob([C.csv(modelo, contexto)], { type: 'text/csv;charset=utf-8' })) }, 'Baixar CSV'),
      h('button', { type: 'button', class: 'acao', onclick: () => png(secao, painel, modelo, metrica, ressalvas, ficha)
        .then((b) => baixar(`${nome}.png`, b), () => anunciar('Não foi possível gerar o PNG neste navegador. O CSV tem os mesmos valores.')) }, 'Baixar PNG'),
      h('button', { type: 'button', class: 'acao', onclick: () => copiar() }, 'Copiar link'));
  };

  const baixar = (nome, blob) => {
    const a = h('a', { href: URL.createObjectURL(blob), download: nome });
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    anunciar(`${nome} baixado.`);
  };
  const copiar = () => {
    const feito = () => anunciar('Link copiado. Quem abrir vê esta mesma consulta.');
    if (navigator.clipboard) navigator.clipboard.writeText(location.href).then(feito, () => anunciar('Não foi possível copiar. O link é o endereço desta página.'));
    else anunciar('Não foi possível copiar. O link é o endereço desta página.');
  };

  // ---------------------------------------------------------------- PNG

  const PROPS = ['fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'opacity', 'font-size', 'font-family', 'font-weight', 'visibility'];
  const imagemDoSvg = (svg) => new Promise((resolve, reject) => {
    const clone = svg.cloneNode(true);
    const originais = svg.querySelectorAll('*');
    const copias = clone.querySelectorAll('*');
    originais.forEach((o, k) => {
      const estilo = getComputedStyle(o);
      copias[k].setAttribute('style', PROPS.map((p) => `${p}:${estilo.getPropertyValue(p)}`).join(';'));
    });
    const caixa = svg.viewBox.baseVal;
    clone.setAttribute('width', caixa.width * 2);
    clone.setAttribute('height', caixa.height * 2);
    clone.setAttribute('xmlns', SVG);
    const imagem = new Image();
    imagem.onload = () => resolve({ imagem, largura: caixa.width * 2, altura: caixa.height * 2 });
    imagem.onerror = reject;
    imagem.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(new XMLSerializer().serializeToString(clone))}`;
  });

  // O PNG leva o título, o gráfico (ou o número, ou a tabela), as ressalvas em uma linha cada, a fonte, a data
  // da coleta e o endereço com o estado: quem recebe a imagem chega à mesma consulta.
  const png = async (secao, painel, modelo, metrica, ressalvas, ficha) => {
    for (const caixa of secao.querySelectorAll('.grafico-consulta')) desenhar(caixa);
    const imagens = await Promise.all([...secao.querySelectorAll('.grafico-consulta')].map(async (caixa) => ({
      titulo: caixa.dataset.titulo, variacao: caixa.dataset.variacao || '', ...(await imagemDoSvg(caixa.querySelector('svg'))) })));
    const estilo = getComputedStyle(document.body);
    const fundo = estilo.backgroundColor;
    const tinta = estilo.color;
    const suave = getComputedStyle(document.documentElement).getPropertyValue('--tinta-suave').trim() || tinta;
    const fonte = (peso, tamanho) => `${peso} ${tamanho}px Overpass, system-ui, sans-serif`;
    const coletas = [...new Set(modelo.coletados.map((ano) => ficha.anos[ano].coletado_em))].sort();
    const notas = [
      ...ressalvas.filter((r) => r.tipo === 'quebra_de_serie').map((r) => `Quebra de série em ${r.a_partir_de}: os valores de antes e de depois não se comparam.`),
      ...ressalvas.filter((r) => r.tipo === 'sem_dado').map((r) => `Sem estimativa do IBGE em ${C.juntar(r.periodos)}.`),
      ...ressalvas.filter((r) => r.tipo === 'nota_do_municipio').map((r) => `Nota: ${C.juntar(r.municipios.map((m) => C.nomeDe(dados.nomes, m)))} em ${C.juntar(r.periodos)} (ver a página).`),
      `Fonte: ${metrica.fonte.orgao}, ${metrica.fonte.pesquisa}.`,
      coletas.length ? `Coletado em ${C.dataHora(coletas[coletas.length - 1])}.` : '',
      location.href,
    ].filter(Boolean);
    const tela = document.createElement('canvas');
    const ctx = tela.getContext('2d');
    const margem = 32;
    const titulo = C.titulo(catalogo, painel, dados.nomes);
    // Conteúdo: imagens empilhadas, o número ou a tabela.
    let largura = 0;
    let altura = 0;
    const desenhos = [];
    if (painel.grafico === 'numero') {
      const c = modelo.celulas[modelo.municipios[0]][modelo.anos[0]];
      const texto = c.valor === null ? C.NA_CELULA[c.situacao] : `${C.numero(c.valor)} ${unidade(metrica)}`;
      ctx.font = fonte(800, 96);
      largura = ctx.measureText(texto).width;
      altura = 120;
      desenhos.push((y) => { ctx.font = fonte(800, 96); ctx.fillStyle = tinta; ctx.fillText(texto, margem, y + 96); });
    } else if (!imagens.length) {
      // A tabela, como texto alinhado.
      const cab = ['Ano', ...modelo.municipios.map((codigo) => C.nomeDe(dados.nomes, codigo))];
      const linhas = modelo.anos.map((ano) => [String(ano), ...modelo.municipios.map((codigo) => {
        const c = modelo.celulas[codigo][ano];
        return c.valor === null ? C.NA_CELULA[c.situacao] : C.numero(c.valor);
      })]);
      ctx.font = fonte(600, 22);
      const larguras = cab.map((t, k) => Math.max(...[t, ...linhas.map((l) => l[k])].map((x) => ctx.measureText(x).width)) + 36);
      largura = larguras.reduce((a, b) => a + b, 0);
      altura = 40 * (linhas.length + 1);
      desenhos.push((y) => {
        [cab, ...linhas].forEach((l, r) => {
          let x = margem;
          ctx.font = fonte(r ? 500 : 700, 22);
          ctx.fillStyle = r ? tinta : suave;
          l.forEach((t, k) => { ctx.fillText(t, x, y + 30 + 40 * r); x += larguras[k]; });
        });
      });
    } else {
      for (const im of imagens) {
        const topo = altura + (im.titulo ? 40 : 0) + (im.variacao ? 30 : 0);
        largura = Math.max(largura, im.largura);
        desenhos.push((y) => {
          if (im.titulo) { ctx.font = fonte(700, 24); ctx.fillStyle = tinta; ctx.fillText(im.titulo, margem, y + topo - (im.variacao ? 42 : 12)); }
          if (im.variacao) { ctx.font = fonte(400, 20); ctx.fillStyle = suave; ctx.fillText(`Variação: ${im.variacao}`, margem, y + topo - 12); }
          ctx.drawImage(im.imagem, margem, y + topo);
        });
        altura = topo + im.altura + 16;
      }
    }
    ctx.font = fonte(800, 30);
    largura = Math.max(largura, ctx.measureText(titulo).width);
    ctx.font = fonte(400, 20);
    for (const n of [...notas, ...imagens.map((im) => `Variação: ${im.variacao}`)]) largura = Math.max(largura, ctx.measureText(n).width);
    tela.width = Math.ceil(largura) + 2 * margem;
    tela.height = 72 + altura + 24 + 30 * notas.length + margem;
    ctx.fillStyle = fundo;
    ctx.fillRect(0, 0, tela.width, tela.height);
    ctx.fillStyle = tinta;
    ctx.font = fonte(800, 30);
    ctx.fillText(titulo, margem, 50);
    for (const d of desenhos) d(72);
    ctx.font = fonte(400, 20);
    ctx.fillStyle = suave;
    notas.forEach((n, k) => ctx.fillText(n, margem, 72 + altura + 40 + 30 * k));
    return new Promise((resolve) => tela.toBlob(resolve, 'image/png'));
  };

  // ---------------------------------------------------------------- painéis

  const secaoDoPainel = (i, painel, r, varios) => {
    const titulo = C.titulo(catalogo, painel, dados.nomes);
    const secao = h('section', { class: 'resultado', id: `painel-${i + 1}`, 'aria-labelledby': `p${i}-titulo` },
      h('h2', { id: `p${i}-titulo`, class: 'resultado__titulo', tabindex: '-1' }, varios ? `Painel ${i + 1}: ${titulo}` : titulo));
    if (varios) {
      secao.append(entendido(i, painel, r.validacao),
        h('button', { type: 'button', class: 'acao acao--discreta', onclick: () => ir({ v: E.VERSAO, paineis: atual.paineis.filter((_, j) => j !== i) },
          null, `Painel ${i + 1} removido.`) }, 'Tirar este painel'));
    }
    const v = r.validacao;
    if (!v.ok && v.tipo === 'ambigua') {
      secao.append(blocoRecusa(i, 'Falta escolher uma coisa', v.mensagem,
        sugestoesDe(i, v.opcoes.filter((o) => o.validacao.ok).map((o) => ({ painel: o.painel })))));
      return { secao, depois: [] };
    }
    if (!v.ok) {
      const falha = dados.falhas[(C.comFicha(catalogo).find((x) => x.metricas.includes(painel.metrica)) || {}).id];
      const extra = v.motivo === 'cobertura_desconhecida' && falha
        ? h('p', { class: 'nota' }, `A ficha da série não foi lida: ${falha}. `, botaoTentar()) : null;
      const lista = [...(v.sugestoes || [])];
      if (v.motivo === 'periodo_indisponivel' || v.motivo === 'periodo_sem_dado') {
        const inteira = C.painelDaSerie(C.comCobertura(catalogo, dados.fichas), painel.territorio.municipios);
        if (inteira && !lista.some((sg) => JSON.stringify(sg.painel.periodo) === JSON.stringify(inteira.periodo))) lista.push({ painel: inteira });
      }
      secao.append(blocoRecusa(i, 'Esta consulta não está publicada', v.mensagem, sugestoesDe(i, lista), extra));
      return { secao, depois: [] };
    }
    if (r.falha) {
      let sugestoes = [];
      if (r.falha.motivo === 'municipio_desconhecido') {
        const restantes = painel.territorio.municipios.filter((codigo) => !r.falha.codigos.includes(codigo));
        const outro = { ...painel, territorio: { ...painel.territorio, municipios: restantes } };
        const val = restantes.length ? E.validar(C.comCobertura(catalogo, dados.fichas), outro) : { ok: false };
        if (val.ok) sugestoes = sugestoesDe(i, [{ painel: val.painel }]);
      }
      secao.append(blocoRecusa(i, r.falha.motivo === 'municipio_desconhecido' ? 'Município que não existe na lista' : 'A série não pôde ser lida',
        r.falha.mensagem, sugestoes, r.falha.tentarDeNovo ? botaoTentar() : null));
      return { secao, depois: [] };
    }
    const metrica = catalogo.metricas[painel.metrica];
    const depois = [];
    secao.append(...figura(painel, r.modelo, metrica, v.ressalvas, depois));
    const ressalvas = blocoRessalvas(i, r.modelo, v.ressalvas);
    if (ressalvas) secao.append(ressalvas);
    secao.append(...tabela(i, painel, r.modelo, metrica));
    secao.append(exportar(i, painel, r.modelo, metrica, v.ressalvas, r.cruzamento, r.ficha, secao));
    secao.append(fonteEMetodo(r.modelo, metrica, r.cruzamento, r.ficha));
    return { secao, depois };
  };

  const painelInicial = (codigo) => C.painelDaSerie(C.comCobertura(catalogo, dados.fichas), [codigo]);

  // Sem estado no endereço: a placa pede um município, e a coluna diz o que a série tem antes de mostrar.
  const inicio = () => {
    tituloDaPagina.textContent = ROTULO;
    lead.textContent = LEAD;
    const coberto = C.comFicha(C.comCobertura(catalogo, dados.fichas)).find((c) => c.anos.coletados);
    if (!coberto) {
      const c = C.comFicha(catalogo)[0];
      const probe = c ? E.validar(C.comCobertura(catalogo, dados.fichas), { metrica: c.metricas[0], territorio: { municipios: ['1100015'] },
        periodo: { de: String(c.anos.primeiro), ate: String(c.anos.primeiro) }, grafico: 'linha' }) : null;
      placa.replaceChildren();
      const falha = c && dados.falhas[c.id];
      raiz.replaceChildren(blocoRecusa(0, 'A consulta está fora do ar', probe ? probe.mensagem : 'Nenhuma série está publicada.', [],
        h('p', { class: 'nota' }, falha ? `A ficha da série não foi lida: ${falha}. ` : '', botaoTentar())));
      return;
    }
    placa.replaceChildren(h('form', { class: 'entendido', 'aria-label': 'Começar uma consulta', onsubmit: (e) => e.preventDefault() },
      h('div', { class: 'entendido__grupo' }, adicionar('p0', [], (codigo) => {
        const painel = painelInicial(codigo);
        if (painel) ir({ v: E.VERSAO, paineis: [painel] }, 'p0-busca', `${C.nomeDe(dados.nomes, codigo)}: série de ${painel.periodo.de} a ${painel.periodo.ate}.`);
      }, 'Escolha um município'))));
    const anos = coberto.anos.coletados;
    const quebras = (coberto.quebras || []).map((q) => q.a_partir_de);
    raiz.replaceChildren(h('div', { class: 'inicio' },
      h('p', null, `A série vai de ${anos[0]} a ${anos[anos.length - 1]}, com a estimativa de cada município em cada ano, como o IBGE publica. `
        + `${coberto.anos.sem_dado_ressalva || ''}`),
      quebras.length ? h('p', null, `A série muda de patamar em ${C.juntar(quebras.map(String))}. Quando o período escolhido atravessa uma quebra, `
        + 'a tela avisa junto do gráfico, com os documentos do IBGE que explicam a mudança.') : null,
      h('p', null, 'Tudo o que está na tela fica no endereço da página: o link mostra a mesma consulta a quem o abrir. Dá para baixar em CSV e PNG.')));
  };

  // Link que não abre: a mensagem da leitura e, se havia códigos de município nele, a série deles.
  const linkQuebrado = (lido, texto) => {
    tituloDaPagina.textContent = 'Este link não abre';
    lead.textContent = lido.mensagem;
    inicio();
    tituloDaPagina.textContent = 'Este link não abre';
    lead.textContent = lido.mensagem;
    const codigos = C.codigosNoTexto(texto);
    const painel = codigos.length ? C.painelDaSerie(C.comCobertura(catalogo, dados.fichas), codigos) : null;
    const r = painel ? E.serializar({ v: E.VERSAO, paineis: [painel] }) : null;
    const extra = lido.motivo === 'versao_mais_nova' ? h('p', null, h('button', { type: 'button', class: 'acao', onclick: () => location.reload() }, 'Recarregar a página')) : null;
    raiz.prepend(blocoRecusa(0, 'O que deu para entender', codigos.length
      ? `O link pedia ${codigos.length === 1 ? 'o município' : 'os municípios'} ${C.juntar(codigos.map((codigo) => C.nomeDe(dados.nomes, codigo)))}, mas o resto não pôde ser lido.`
      : 'Nenhum município pôde ser lido do link. Escolha um na placa acima.',
    r && r.ok ? [{ href: href(r.texto), texto: C.descrever(catalogo, painel, dados.nomes) }] : [], extra));
  };

  // ---------------------------------------------------------------- ciclo

  let geracao = 0;
  const restaurarFoco = (id) => {
    const alvo = id && document.getElementById(id);
    if (alvo && document.activeElement !== alvo) alvo.focus({ preventScroll: true });
  };

  const atualizar = async (focoPedido, rolar) => {
    const minha = ++geracao;
    const foco = focoPedido || (document.activeElement && document.activeElement.id);
    const texto = location.hash.replace(/^#/, '');
    await carregarBase();
    if (minha !== geracao) return;
    if (!texto) {
      atual = null;
      inicio();
      restaurarFoco(foco);
      return;
    }
    const aberto = C.abrir(catalogo, dados.fichas, texto);
    if (!aberto.ok) {
      atual = null;
      linkQuebrado(aberto, texto);
      return;
    }
    atual = aberto.estado;
    const precisa = aberto.paineis.filter((v) => v.ok).map((v) => C.cruzamento(catalogo, v.cruzamento));
    if (precisa.some((c) => !dados.prontas.has(c.id))) {
      raiz.replaceChildren(h('p', { class: 'carregando', role: 'status' }, 'Lendo a série do IBGE neste navegador…'));
    }
    const resultados = await Promise.all(aberto.paineis.map(async (v) => {
      if (!v.ok) return { validacao: v };
      const c = C.cruzamento(catalogo, v.cruzamento);
      const ficha = C.fichaDe(dados.fichas, c.id);
      try {
        const indice = await carregarSerie(c, ficha);
        const conferido = C.conferir(indice, ficha);
        if (!conferido.ok) return { validacao: v, falha: conferido };
        const modelo = C.executar(indice, v.consulta, c, ficha);
        return modelo.ok ? { validacao: v, modelo, cruzamento: c, ficha } : { validacao: v, falha: modelo };
      } catch (e) {
        return { validacao: v, falha: { motivo: 'sem_rede', mensagem: `Não foi possível baixar ou ler a série: ${e.message}.`, tentarDeNovo: true } };
      }
    }));
    if (minha !== geracao) return;
    const varios = atual.paineis.length > 1;
    if (varios) {
      tituloDaPagina.textContent = 'Consulta';
      lead.textContent = `${atual.paineis.length} painéis neste link. Cada um tem a própria interpretação, que você pode corrigir.`;
      placa.replaceChildren();
    } else {
      tituloDaPagina.textContent = ROTULO;
      lead.textContent = LEAD;
      placa.replaceChildren(entendido(0, atual.paineis[0], resultados[0].validacao));
    }
    const montados = atual.paineis.map((p, i) => secaoDoPainel(i, p, resultados[i], varios));
    raiz.replaceChildren(...montados.map((m) => m.secao));
    for (const m of montados) for (const d of m.depois) d();
    document.title = `${varios ? 'Consulta' : C.titulo(catalogo, atual.paineis[0], dados.nomes)} · ${document.title.split(' · ').pop()}`;
    if (rolar) window.scrollTo(0, 0);
    restaurarFoco(foco);
  };

  // Um link seguido dentro da página (uma sugestão, por exemplo): a leitura recomeça do alto, no título do resultado.
  window.addEventListener('hashchange', () => atualizar('p0-titulo', true));
  atualizar();
})();
