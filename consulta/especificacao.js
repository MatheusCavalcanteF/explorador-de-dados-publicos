// Especificação de consulta: o mesmo objeto para pergunta, gráfico e dashboard, e o que vai para a URL.
// Três partes, todas sem tela e sem rede: o formato (normalizar), a validação no catálogo (validar) e o
// texto da URL (serializar, ler, abrir). Nada aqui lê dado nem calcula número: o que sai de uma validação
// aceita são os parâmetros de uma consulta de modelo sobre um cruzamento pré-calculado.
//
// Fica fora de web/ enquanto não estiver ligada às páginas: o build publica tudo o que está lá.
const Especificacao = (() => {
  const VERSAO = 1;
  // Versões antigas que ainda sabemos converter: { 0: (estado) => estadoNaVersao1 }. Nenhuma, por enquanto.
  const MIGRACOES = {};

  // O link inteiro deve caber em 2.000 caracteres, o que sobrevive a mensageiros, e-mail e QR code.
  // 200 ficam reservados para o endereço da página; o resto é o estado, depois do "#".
  const LIMITE_ESTADO = 1800;
  const LIMITES = { paineis: 12, municipios: 10, valores: 30 };

  const PAPEIS = { residencia: 'municipio_residencia', hospital: 'municipio_hospital' };
  const SIGLA_DO_PAPEL = { residencia: 'r', hospital: 'h' };
  const PAPEL_DA_SIGLA = { r: 'residencia', h: 'hospital' };
  const ROTULO_DO_PAPEL = { residencia: 'município de residência', hospital: 'município do hospital' };
  // O município sem papel: o da população residente, que já vem agregada da fonte. Um cruzamento com esta
  // dimensão não pergunta "de residência ou do hospital?", e um painel com papel não cabe nele.
  const SEM_PAPEL = 'municipio';
  const TERRITORIOS = [...Object.values(PAPEIS), SEM_PAPEL];
  // "numero" é o valor único em destaque: só vale quando o painel resolve para uma célula.
  const GRAFICOS = ['linha', 'barras', 'tabela', 'numero'];
  const TEMPOS = ['mes', 'tri', 'ano'];
  const ROTULO_DO_TEMPO = { mes: 'por mês', tri: 'por trimestre', ano: 'por ano' };

  const ID = /^[a-z][a-z0-9_]{0,39}$/;
  const VALOR = /^[A-Za-z0-9+_-]{1,16}$/;
  const MUNICIPIO = /^[1-5]\d{6}$/;
  const PERIODO = { ano: /^(19|20)\d\d$/, tri: /^(19|20)\d\d-T[1-4]$/, mes: /^(19|20)\d\d-(0[1-9]|1[0-2])$/ };

  const objeto = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
  const lista = (x, padrao, max) => Array.isArray(x) && x.length <= max && x.every((v) => typeof v === 'string')
    && new Set(x).size === x.length && x.every((v) => padrao.test(v));
  const sobras = (x, chaves) => Object.keys(x).filter((k) => !chaves.includes(k));

  const tempoDe = (periodo) => TEMPOS.find((t) => PERIODO[t].test(periodo)) || null;

  // Um município, um período, nada detalhado e um valor por filtro: o painel é uma célula do cruzamento.
  const umaCelula = (p) => p.territorio.municipios.length === 1 && p.periodo.de === p.periodo.ate
    && !p.detalhar.length && Object.values(p.filtros).every((valores) => valores.length === 1);

  // ---------------------------------------------------------------- formato

  // Confere a forma de um painel e devolve a cópia canônica: só as chaves conhecidas, filtros em ordem
  // alfabética. Não existe campo de texto livre: tudo é identificador do catálogo, código ou período.
  const normalizarPainel = (p, onde) => {
    const erros = [];
    const erro = (texto) => { erros.push(`${onde}: ${texto}`); };
    if (!objeto(p)) return { erros: [`${onde}: não é um objeto`] };
    for (const k of sobras(p, ['tema', 'metrica', 'territorio', 'filtros', 'detalhar', 'periodo', 'grafico'])) {
      erro(`campo desconhecido "${k}"`);
    }
    const painel = {};
    if (p.tema !== undefined) {
      if (typeof p.tema === 'string' && ID.test(p.tema)) painel.tema = p.tema;
      else erro('tema inválido');
    }
    if (typeof p.metrica === 'string' && ID.test(p.metrica)) painel.metrica = p.metrica;
    else erro('métrica ausente ou inválida');

    const t = p.territorio;
    if (!objeto(t) || sobras(t, ['papel', 'municipios']).length) erro('território ausente ou com campo desconhecido');
    else {
      painel.territorio = {};
      if (t.papel !== undefined) {
        if (Object.hasOwn(PAPEIS, t.papel)) painel.territorio.papel = t.papel;
        else erro('papel do território deve ser "residencia" ou "hospital"');
      }
      if (lista(t.municipios, MUNICIPIO, LIMITES.municipios) && t.municipios.length) {
        painel.territorio.municipios = [...t.municipios];
      } else erro(`municípios: de 1 a ${LIMITES.municipios} códigos do IBGE de 7 dígitos, sem repetir`);
    }

    const filtros = p.filtros === undefined ? {} : p.filtros;
    painel.filtros = {};
    if (!objeto(filtros)) erro('filtros deve ser um objeto');
    else {
      for (const dim of Object.keys(filtros).sort()) {
        if (!ID.test(dim) || TERRITORIOS.includes(dim)) {
          erro(`filtro "${dim}" inválido: o município vai em território, com o papel quando a métrica tem um`);
        } else if (!lista(filtros[dim], VALOR, LIMITES.valores) || !filtros[dim].length) {
          erro(`filtro ${dim}: de 1 a ${LIMITES.valores} valores, sem repetir`);
        } else painel.filtros[dim] = [...filtros[dim]];
      }
    }

    const detalhar = p.detalhar === undefined ? [] : p.detalhar;
    if (!lista(detalhar, ID, 8) || detalhar.some((d) => TERRITORIOS.includes(d))) {
      erro('detalhar deve ser uma lista de dimensões');
      painel.detalhar = [];
    } else {
      painel.detalhar = [...detalhar].sort();
      for (const d of painel.detalhar) {
        if (Object.hasOwn(painel.filtros, d)) erro(`${d} está em filtros e em detalhar ao mesmo tempo`);
      }
    }

    const per = p.periodo;
    if (!objeto(per) || sobras(per, ['de', 'ate']).length || typeof per.de !== 'string' || typeof per.ate !== 'string') {
      erro('período precisa de "de" e "ate"');
    } else {
      const tempo = tempoDe(per.de);
      if (!tempo || tempoDe(per.ate) !== tempo) erro('período: use 2025, 2025-T1 ou 2025-03, com "de" e "ate" no mesmo formato');
      else if (per.de > per.ate) erro('período: "de" vem depois de "ate"');
      else painel.periodo = { de: per.de, ate: per.ate };
    }

    if (GRAFICOS.includes(p.grafico)) painel.grafico = p.grafico;
    else erro(`gráfico deve ser um de: ${GRAFICOS.join(', ')}`);
    return erros.length ? { erros } : { painel };
  };

  // O estado inteiro: { v, paineis }. Uma pergunta ou um gráfico é um estado de um painel só.
  const normalizar = (estado) => {
    if (!objeto(estado)) return { ok: false, motivo: 'formato', erros: ['o estado não é um objeto'] };
    if (estado.v !== VERSAO) return { ok: false, motivo: 'formato', erros: [`versão ${JSON.stringify(estado.v)}: esperada ${VERSAO}`] };
    const erros = sobras(estado, ['v', 'paineis']).map((k) => `campo desconhecido "${k}"`);
    const paineis = [];
    if (!Array.isArray(estado.paineis) || !estado.paineis.length || estado.paineis.length > LIMITES.paineis) {
      erros.push(`painéis: de 1 a ${LIMITES.paineis}`);
    } else {
      estado.paineis.forEach((p, i) => {
        const r = normalizarPainel(p, `painel ${i + 1}`);
        if (r.erros) erros.push(...r.erros);
        else paineis.push(r.painel);
      });
    }
    return erros.length ? { ok: false, motivo: 'formato', erros } : { ok: true, estado: { v: VERSAO, paineis } };
  };

  // ---------------------------------------------------------------- catálogo

  // Os do catálogo que passam por supressão e os que já vêm agregados da fonte oficial: para a consulta,
  // são a mesma coisa, um cruzamento pré-calculado.
  const todosOsCruzamentos = (catalogo) => [...(catalogo.cruzamentos || []), ...(catalogo.cruzamentos_da_origem || [])];
  const papelDoCruzamento = (c) => Object.keys(PAPEIS).find((papel) => c.dimensoes.includes(PAPEIS[papel])) || null;
  const territorioDoCruzamento = (c) => c.dimensoes.find((d) => TERRITORIOS.includes(d)) || null;
  const dimensoesLivres = (c) => c.dimensoes.filter((d) => !TERRITORIOS.includes(d));

  // Anos de um cruzamento que declara os seus (`anos`). O catálogo diz o primeiro e os sem dado; o último é
  // o que a fonte tiver, e por isso não fica no catálogo: vem da coleta, pela ficha publicada ao lado do
  // arquivo, e entra em `anos.coletados` por comCobertura. Sem essa lista, a validação não sabe até onde a
  // série vai, e recusa em vez de supor.
  const temDado = (c, ano) => (c.anos.coletados || []).includes(ano);
  const anosDoPeriodo = (periodo) => {
    const anos = [];
    for (let ano = Number(periodo.de); ano <= Number(periodo.ate); ano += 1) anos.push(ano);
    return anos;
  };
  const enumerar = (anos) => (anos.length > 1 ? `${anos.slice(0, -1).join(', ')} e ${anos[anos.length - 1]}` : String(anos[0]));
  // O período pedido, levado para onde o cruzamento tem dado: o começo vai para o primeiro ano coletado
  // dali em diante (ou para o último, se o pedido começa depois dele), e o fim, para o último até ali.
  const ajustarAosAnos = (periodo, c) => {
    const coletados = c.anos && c.tempo === 'ano' ? c.anos.coletados || [] : [];
    if (!coletados.length) return periodo;
    const ultimo = coletados[coletados.length - 1];
    const de = coletados.find((ano) => ano >= Number(periodo.de)) || ultimo;
    const ate = [...coletados].reverse().find((ano) => ano <= Number(periodo.ate) && ano >= de) || de;
    return { de: String(de), ate: String(ate) };
  };

  // O catálogo com os anos que a coleta trouxe para cada cruzamento que declara `anos`. `fichas` são os
  // JSON publicados ao lado dos arquivos (dados/ibge/populacao_por_ano.json): { cruzamento, anos: { 2013: … } }.
  // Uma ficha que contradiz o catálogo (ano antes do primeiro, ou declarado sem dado) não é usada, e o
  // cruzamento fica sem cobertura: toda consulta a ele é recusada.
  const comCobertura = (catalogo, fichas) => {
    const copia = { ...catalogo, cruzamentos_da_origem: (catalogo.cruzamentos_da_origem || []).map((c) => ({ ...c })) };
    for (const c of copia.cruzamentos_da_origem) {
      const ficha = (Array.isArray(fichas) ? fichas : []).find((f) => objeto(f) && f.cruzamento === c.id && objeto(f.anos));
      if (!c.anos || !ficha) continue;
      const anos = Object.keys(ficha.anos).filter((a) => PERIODO.ano.test(a)).map(Number).sort((a, b) => a - b);
      const coerente = anos.length === Object.keys(ficha.anos).length && anos.length
        && anos.every((ano) => ano >= c.anos.primeiro && !(c.anos.sem_dado || []).includes(ano));
      if (coerente) c.anos = { ...c.anos, coletados: anos };
    }
    return copia;
  };
  // O que a tela precisa dizer junto do resultado, sem número: as quebras de série que o período atravessa,
  // os anos sem dado dentro dele e as notas dos municípios pedidos nos anos pedidos. Um ano só não atravessa
  // quebra nenhuma.
  const ressalvasDoPainel = (c, painel) => {
    const saida = [];
    if (!c.anos || c.tempo !== 'ano') return saida;
    const de = Number(painel.periodo.de);
    const ate = Number(painel.periodo.ate);
    for (const q of c.quebras || []) {
      if (de < q.a_partir_de && q.a_partir_de <= ate) {
        // O documento que explica a quebra vai junto, com o link, para a tela citar a fonte da ressalva.
        const documentos = (q.documentos || []).map((d) => ({ titulo: d.titulo, url: d.url }));
        saida.push({ tipo: 'quebra_de_serie', a_partir_de: String(q.a_partir_de), texto: q.ressalva,
          ...(documentos.length ? { documentos } : {}) });
      }
    }
    const faltam = (c.anos.sem_dado || []).filter((ano) => ano >= de && ano <= ate);
    if (faltam.length) saida.push({ tipo: 'sem_dado', periodos: faltam.map(String), texto: c.anos.sem_dado_ressalva });
    // Anos pedidos depois do último coletado: o gráfico termina antes do fim do período, e a tela diz por quê.
    const ultimo = c.anos.coletados[c.anos.coletados.length - 1];
    const alem = anosDoPeriodo(painel.periodo).filter((ano) => ano > ultimo);
    if (alem.length) {
      saida.push({ tipo: 'fora_da_cobertura', periodos: alem.map(String),
        texto: `Sem dado em ${enumerar(alem)}: o último ano coletado da fonte é ${ultimo}.` });
    }
    for (const nota of c.notas || []) {
      const municipios = painel.territorio.municipios.filter((m) => nota.municipios.includes(m));
      const anos = nota.anos.filter((ano) => ano >= de && ano <= ate);
      if (municipios.length && anos.length) {
        saida.push({ tipo: 'nota_do_municipio', municipios, periodos: anos.map(String), texto: nota.ressalva });
      }
    }
    return saida;
  };

  // As bases de onde a métrica sai; a calculada depende das bases de cada parcela.
  const basesDaMetrica = (catalogo, nome, vistas = new Set()) => {
    const metrica = catalogo.metricas[nome];
    if (!metrica || vistas.has(nome)) return [];
    vistas.add(nome);
    if (metrica.formula) return (metrica.fontes || []).flatMap((parcela) => basesDaMetrica(catalogo, parcela, vistas));
    return [metrica.base];
  };

  const foraDoSite = (catalogo, nome) => basesDaMetrica(catalogo, nome)
    .filter((b) => ((catalogo.bases[b] || {}).publicacao || {}).site === false);

  // Os cruzamentos em que a métrica está de fato publicada: o cruzamento a lista, a métrica não o exclui,
  // a base vai para o site e, quando a base declara uma superfície, o cruzamento e a medida estão nela.
  const cruzamentosPublicados = (catalogo, nome) => {
    const metrica = catalogo.metricas[nome];
    if (!metrica || metrica.formula || foraDoSite(catalogo, nome).length) return [];
    const superficie = (catalogo.bases[metrica.base] || {}).superficie;
    return todosOsCruzamentos(catalogo).filter((c) => c.metricas.includes(nome)
      && territorioDoCruzamento(c)
      && (!metrica.cruzamentos || metrica.cruzamentos.includes(c.id))
      && (!superficie || ((superficie.cruzamentos[c.id] || {}).medidas || []).includes(nome)));
  };

  // Valores que uma dimensão aceita, ou null quando o catálogo não os lista.
  const valoresDaDimensao = (catalogo, dim) => {
    const d = catalogo.dimensoes[dim];
    if (d.valores) return d.valores.map(String);
    if (dim === 'capitulo_cid') {
      const faixas = ((catalogo.referencias || {}).cid10_capitulos || {}).faixas;
      if (faixas) return ['0', ...faixas.map((f) => String(f[0]))];   // 0 é "sem capítulo"
    }
    return null;
  };

  const converterPeriodo = (periodo, tempo) => {
    if (tempoDe(periodo.de) === tempo) return { ...periodo };
    const de = periodo.de.slice(0, 4);
    const ate = periodo.ate.slice(0, 4);
    if (tempo === 'ano') return { de, ate };
    return tempo === 'tri' ? { de: `${de}-T1`, ate: `${ate}-T4` } : { de: `${de}-01`, ate: `${ate}-12` };
  };

  // O painel pedido, reescrito para o grão de um cruzamento que existe.
  // Filtro de dimensão que o cruzamento não tem sai; valor que a dimensão não tem, também. O tema não
  // vai junto: a sugestão pode ser de outro.
  const adaptar = (catalogo, painel, metrica, c) => {
    const livres = dimensoesLivres(c);
    const filtros = {};
    for (const d of Object.keys(painel.filtros).filter((dim) => livres.includes(dim))) {
      const aceitos = valoresDaDimensao(catalogo, d);
      const valores = painel.filtros[d].filter((v) => !aceitos || aceitos.includes(v));
      if (valores.length) filtros[d] = valores;
    }
    const papel = papelDoCruzamento(c);
    const adaptado = {
      metrica,
      territorio: { ...(papel ? { papel } : {}), municipios: [...painel.territorio.municipios] },
      filtros,
      detalhar: livres.filter((d) => !Object.hasOwn(filtros, d)).sort(),
      periodo: ajustarAosAnos(converterPeriodo(painel.periodo, c.tempo), c),
      grafico: painel.grafico,
    };
    // O número em destaque não sobrevive a um grão com mais de uma célula: a sugestão vira tabela.
    if (adaptado.grafico === 'numero' && !umaCelula(adaptado)) adaptado.grafico = 'tabela';
    return adaptado;
  };

  const distancia = (painel, c) => {
    const pedidas = new Set([...Object.keys(painel.filtros), ...painel.detalhar]);
    const livres = dimensoesLivres(c);
    // Perder uma dimensão pedida pesa mais do que ganhar uma que não foi pedida.
    const diferentes = 2 * livres.filter((d) => !pedidas.has(d)).length + 3 * [...pedidas].filter((d) => !livres.includes(d)).length;
    // Papel trocado pesa; papel pedido num cruzamento que não tem papel nenhum pesa mais do que qualquer
    // outra diferença, e mais do que a ordem das métricas: quem perguntou por residência ou hospital vê
    // primeiro o que responde a isso.
    const pedido = painel.territorio.papel;
    const papel = pedido && pedido !== papelDoCruzamento(c) ? (papelDoCruzamento(c) ? 4 : 1000) : 0;
    return papel + diferentes + Math.abs(TEMPOS.indexOf(c.tempo) - TEMPOS.indexOf(tempoDe(painel.periodo.de)));
  };

  const descrever = (catalogo, painel) => {
    const metrica = catalogo.metricas[painel.metrica];
    const dims = [...Object.keys(painel.filtros), ...painel.detalhar].sort();
    return [metrica.rotulo, `por ${ROTULO_DO_PAPEL[painel.territorio.papel] || 'município'}`, ROTULO_DO_TEMPO[tempoDe(painel.periodo.de)],
      ...(dims.length ? [`com ${dims.join(' e ')}`] : [])].join(', ');
  };

  // Até três consultas que existem, das mais parecidas com a pedida para as menos. Cada uma é conferida
  // pela própria validação antes de sair: sugestão que não valida não é sugestão.
  const sugerir = (catalogo, painel, metricas) => {
    const candidatas = [];
    metricas.forEach((nome, ordem) => {
      for (const c of cruzamentosPublicados(catalogo, nome)) {
        candidatas.push({ nome, c, custo: 100 * ordem + distancia(painel, c) });
      }
    });
    // No empate, o tempo mais fino: quem pediu trimestre e não tem, vê os meses antes do ano.
    candidatas.sort((a, b) => a.custo - b.custo || TEMPOS.indexOf(a.c.tempo) - TEMPOS.indexOf(b.c.tempo)
      || a.c.id.localeCompare(b.c.id));
    const saida = [];
    for (const { nome, c } of candidatas) {
      const sugerido = adaptar(catalogo, painel, nome, c);
      const r = validarNormalizado(catalogo, sugerido, false);
      if (r.ok && !saida.some((s) => s.cruzamento === c.id && s.painel.metrica === nome)) {
        saida.push({ painel: r.painel, cruzamento: c.id, descricao: descrever(catalogo, r.painel) });
      }
      if (saida.length === 3) break;
    }
    return saida;
  };

  // Métricas publicadas, as do mesmo tema primeiro.
  const outrasMetricas = (catalogo, tema, menos) => Object.keys(catalogo.metricas)
    .filter((nome) => nome !== menos && cruzamentosPublicados(catalogo, nome).length)
    .sort((a, b) => (catalogo.metricas[b].tema === tema) - (catalogo.metricas[a].tema === tema));

  const validarNormalizado = (catalogo, painel, comSugestoes = true) => {
    const recusa = (motivo, mensagem, metricas) => {
      const sugestoes = comSugestoes ? sugerir(catalogo, painel, metricas) : [];
      const fim = sugestoes.length ? '' : ' Nenhuma consulta parecida está publicada no momento.';
      return { ok: false, tipo: 'recusa', motivo, mensagem: mensagem + fim, sugestoes };
    };
    const metrica = Object.hasOwn(catalogo.metricas, painel.metrica) ? catalogo.metricas[painel.metrica] : null;
    if (!metrica) {
      return recusa('metrica_desconhecida', `A métrica "${painel.metrica}" não existe no catálogo.`,
        outrasMetricas(catalogo, painel.tema, null));
    }
    const rotulo = metrica.rotulo;
    if (painel.tema !== undefined && painel.tema !== metrica.tema && !(metrica.etiquetas || []).includes(painel.tema)) {
      return recusa('tema_incompativel', `${rotulo} não pertence ao tema "${painel.tema}".`, [painel.metrica]);
    }
    const fora = foraDoSite(catalogo, painel.metrica);
    if (fora.length) {
      const nomes = [...new Set(fora)].map((b) => catalogo.bases[b].nome).join('; ');
      return recusa('nao_publicada', `${rotulo} ainda não está publicada no site (${nomes}).`,
        outrasMetricas(catalogo, metrica.tema, painel.metrica));
    }
    const publicados = cruzamentosPublicados(catalogo, painel.metrica);
    if (!publicados.length) {
      return recusa('sem_cruzamento', `${rotulo} não tem nenhum cruzamento publicado para consulta.`,
        outrasMetricas(catalogo, metrica.tema, painel.metrica));
    }
    for (const dim of [...Object.keys(painel.filtros), ...painel.detalhar]) {
      if (!Object.hasOwn(catalogo.dimensoes || {}, dim)) {
        return recusa('dimensao_desconhecida', `A dimensão "${dim}" não existe no catálogo.`, [painel.metrica]);
      }
      const aceitos = valoresDaDimensao(catalogo, dim);
      const estranhos = (painel.filtros[dim] || []).filter((v) => aceitos && !aceitos.includes(v));
      if (estranhos.length) {
        return recusa('valor_desconhecido', `${dim} não tem o valor ${estranhos.map((v) => `"${v}"`).join(', ')}.`, [painel.metrica]);
      }
    }
    const temPapel = publicados.some((c) => papelDoCruzamento(c));
    if (!temPapel && painel.territorio.papel) {
      return recusa('papel_nao_se_aplica', `${rotulo} é do município, sem a distinção entre residência e hospital.`,
        [painel.metrica]);
    }
    if (temPapel && !painel.territorio.papel) {
      // "Em Santos": do hospital ou de residência? Sem padrão: quem pergunta escolhe.
      const opcoes = Object.keys(PAPEIS).map((papel) => {
        const escolhido = { ...painel, territorio: { ...painel.territorio, papel } };
        return { papel, rotulo: ROTULO_DO_PAPEL[papel], painel: escolhido, validacao: validarNormalizado(catalogo, escolhido, comSugestoes) };
      });
      return { ok: false, tipo: 'ambigua', motivo: 'papel_do_territorio',
        mensagem: 'Os municípios são os de residência de quem foi atendido ou os do hospital?', opcoes };
    }
    const pedidas = [painel.territorio.papel ? PAPEIS[painel.territorio.papel] : SEM_PAPEL,
      ...Object.keys(painel.filtros), ...painel.detalhar];
    const tempo = tempoDe(painel.periodo.de);
    const cruzamento = publicados.find((c) => c.tempo === tempo && c.dimensoes.length === pedidas.length
      && pedidas.every((d) => c.dimensoes.includes(d)));
    if (!cruzamento) {
      // Nunca chega a este grão somando outro: células ocultas dariam um total errado e silencioso.
      return recusa('grao_indisponivel', `${descrever(catalogo, painel)}: este cruzamento não está publicado.`, [painel.metrica]);
    }
    if (cruzamento.anos && tempo === 'ano') {
      const coletados = cruzamento.anos.coletados || [];
      if (!coletados.length) {
        return recusa('cobertura_desconhecida', `${rotulo}: não foi possível saber quais anos estão publicados.`, [painel.metrica]);
      }
      const primeiro = cruzamento.anos.primeiro;
      const ultimo = coletados[coletados.length - 1];
      // Fora das pontas da série, nem que seja em parte no começo: a sugestão traz o período para dentro dela.
      if (Number(painel.periodo.de) < primeiro || Number(painel.periodo.de) > ultimo) {
        return recusa('periodo_indisponivel', `${rotulo} está publicada de ${primeiro} a ${ultimo}.`, [painel.metrica]);
      }
      const anos = anosDoPeriodo(painel.periodo);
      if (anos.every((ano) => !temDado(cruzamento, ano))) {
        return recusa('periodo_sem_dado', `${rotulo} não tem dado em ${enumerar(anos)}. ${cruzamento.anos.sem_dado_ressalva || ''}`.trim(),
          [painel.metrica]);
      }
    }
    if (painel.grafico === 'numero' && !umaCelula(painel)) {
      // Mostrar um número só exigiria somar ou escolher entre células; a sugestão é a mesma consulta em tabela.
      return recusa('numero_pede_uma_celula', 'O número em destaque mostra uma célula só: um município, um período, '
        + 'nada detalhado e um valor em cada filtro.', [painel.metrica]);
    }
    return {
      ok: true,
      painel,
      cruzamento: cruzamento.id,
      // Quebras de série, anos sem dado e notas de município dentro do pedido: a tela mostra junto do gráfico, sempre.
      ressalvas: ressalvasDoPainel(cruzamento, painel),
      // Os parâmetros da consulta de modelo: escolher linhas de um cruzamento, sem somar nem juntar.
      consulta: {
        cruzamento: cruzamento.id,
        metrica: painel.metrica,
        territorio: { dimensao: territorioDoCruzamento(cruzamento), municipios: [...painel.territorio.municipios] },
        filtros: painel.filtros,
        detalhar: painel.detalhar,
        tempo,
        de: painel.periodo.de,
        ate: painel.periodo.ate,
      },
    };
  };

  // Liga um painel a exatamente um cruzamento publicado, ou diz por que não: recusa (com sugestões que
  // validam) ou ambiguidade (com as opções). Nunca devolve número.
  const validar = (catalogo, painel) => {
    const r = normalizarPainel(painel, 'painel');
    if (r.erros) return { ok: false, tipo: 'recusa', motivo: 'formato', mensagem: r.erros.join('; '), sugestoes: [] };
    return validarNormalizado(catalogo, r.painel);
  };

  // ---------------------------------------------------------------- URL

  const codificar = (valor) => encodeURIComponent(valor);

  const textoDoPainel = (p) => {
    const campos = [`m:${p.metrica}`];
    if (p.tema) campos.push(`k:${p.tema}`);
    campos.push(`t:${[p.territorio.papel ? SIGLA_DO_PAPEL[p.territorio.papel] : 'n', ...p.territorio.municipios].join(',')}`);
    for (const dim of Object.keys(p.filtros)) campos.push(`f:${dim}=${p.filtros[dim].map(codificar).join(',')}`);
    if (p.detalhar.length) campos.push(`d:${p.detalhar.join(',')}`);
    campos.push(`p:${p.periodo.de === p.periodo.ate ? p.periodo.de : `${p.periodo.de},${p.periodo.ate}`}`);
    campos.push(`g:${p.grafico}`);
    return campos.join(';');
  };

  // O estado como texto para depois do "#": v=1&p=<painel>&p=<painel>. O mesmo estado dá sempre o mesmo
  // texto. Acima do limite não sai link nenhum, nem truncado: quem chama bloqueia o painel novo.
  const serializar = (estado, limite = LIMITE_ESTADO) => {
    const r = normalizar(estado);
    if (!r.ok) return r;
    const texto = [`v=${VERSAO}`, ...r.estado.paineis.map((p) => `p=${textoDoPainel(p)}`)].join('&');
    if (texto.length > limite) {
      return { ok: false, motivo: 'grande_demais', tamanho: texto.length, limite,
        mensagem: `Este dashboard não cabe num link: o endereço teria ${texto.length} caracteres, e o limite é ${limite}. `
          + 'Tire um painel, ou reduza municípios e filtros.' };
    }
    return { ok: true, texto, tamanho: texto.length };
  };

  const ilegivel = { ok: false, motivo: 'ilegivel',
    mensagem: 'Este link está incompleto ou foi alterado, e não dá para saber qual consulta ele pedia.' };

  const painelDoTexto = (texto) => {
    const p = { filtros: {} };
    for (const campo of texto.split(';')) {
      const chave = campo.slice(0, 2);
      const resto = campo.slice(2);
      if (chave === 'f:') {
        const igual = resto.indexOf('=');
        const dim = resto.slice(0, igual);
        if (igual < 1 || Object.hasOwn(p.filtros, dim)) return null;
        p.filtros[dim] = resto.slice(igual + 1).split(',').map(decodeURIComponent);
        continue;
      }
      const nome = { 'm:': 'metrica', 'k:': 'tema', 't:': 'territorio', 'd:': 'detalhar', 'p:': 'periodo', 'g:': 'grafico' }[chave];
      if (!nome || p[nome] !== undefined) return null;
      const partes = resto.split(',');
      if (nome === 'territorio') {
        if (partes[0] !== 'n' && !Object.hasOwn(PAPEL_DA_SIGLA, partes[0])) return null;
        p.territorio = { municipios: partes.slice(1) };
        if (partes[0] !== 'n') p.territorio.papel = PAPEL_DA_SIGLA[partes[0]];
      } else if (nome === 'detalhar') p.detalhar = partes;
      else if (nome === 'periodo') {
        if (partes.length > 2) return null;
        p.periodo = { de: partes[0], ate: partes[partes.length - 1] };
      } else if (partes.length !== 1) return null;
      else p[nome] = resto;
    }
    return p;
  };

  // Lê o texto de uma URL. Devolve o estado canônico ou um motivo com mensagem para a tela: versão que o
  // site não lê mais, versão mais nova que o site aberto, texto ilegível ou grande demais.
  const ler = (texto, limite = LIMITE_ESTADO) => {
    if (typeof texto !== 'string') return ilegivel;
    const limpo = texto.replace(/^#/, '');
    if (limpo.length > limite) {
      return { ok: false, motivo: 'grande_demais', tamanho: limpo.length, limite,
        mensagem: `Este link passa do tamanho que o site aceita (${limpo.length} caracteres, limite de ${limite}).` };
    }
    const partes = limpo.split('&');
    const versao = /^v=(\d{1,4})$/.exec(partes[0]);
    if (!versao) return ilegivel;
    const v = Number(versao[1]);
    if (v > VERSAO) {
      return { ok: false, motivo: 'versao_mais_nova', versao: v,
        mensagem: 'Este link foi criado numa versão mais nova do site. Recarregue a página para atualizar e abra o link de novo.' };
    }
    if (v !== VERSAO && !Object.hasOwn(MIGRACOES, v)) {
      return { ok: false, motivo: 'versao_antiga', versao: v,
        mensagem: 'Este link foi criado numa versão antiga do site, que não é mais lida. Monte a consulta de novo a partir da busca.' };
    }
    let estado;
    try {
      const paineis = partes.slice(1).map((parte) => (parte.startsWith('p=') ? painelDoTexto(parte.slice(2)) : null));
      if (paineis.includes(null)) return ilegivel;
      estado = { v, paineis };
    } catch {
      return ilegivel;   // sequência de % inválida
    }
    if (v !== VERSAO) estado = MIGRACOES[v](estado);
    const r = normalizar(estado);
    return r.ok ? r : { ...ilegivel, erros: r.erros };
  };

  // Abre um link: lê e valida cada painel no catálogo de agora. Um painel cujo cruzamento saiu do
  // catálogo volta como recusa, com mensagem e sugestões, para a tela mostrar no lugar do gráfico.
  const abrir = (catalogo, texto, limite = LIMITE_ESTADO) => {
    const r = ler(texto, limite);
    if (!r.ok) return r;
    return { ok: true, estado: r.estado, paineis: r.estado.paineis.map((p) => validarNormalizado(catalogo, p)) };
  };

  return { VERSAO, LIMITE_ESTADO, LIMITES, GRAFICOS, normalizar, validar, comCobertura, serializar, ler, abrir };
})();

if (typeof module !== 'undefined') module.exports = Especificacao;
