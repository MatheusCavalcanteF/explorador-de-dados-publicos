// Gera web/leitor.js: o leitor de Parquet do site (hyparquet, sem descompressor) num arquivo só, para a página
// de consulta carregar com <script>. A versão é a mesma de ferramentas/leitor, que confere o site publicado.
// Uso: npm ci --prefix ferramentas/empacotar && node ferramentas/empacotar/empacotar.mjs
// O arquivo gerado vai para o git; tests/test_consulta.py confere a versão do cabeçalho com os dois package-lock.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const aqui = dirname(fileURLToPath(import.meta.url));
const pacote = JSON.parse(readFileSync(join(aqui, 'node_modules/hyparquet/package.json'), 'utf-8'));
const licenca = readFileSync(join(aqui, 'node_modules/hyparquet/LICENSE'), 'utf-8').trim();

const resultado = await build({
  stdin: { contents: "export { parquetReadObjects } from 'hyparquet';", resolveDir: aqui, loader: 'js' },
  bundle: true,
  minify: true,
  format: 'iife',
  globalName: 'Leitor',
  target: 'es2020',
  legalComments: 'none',
  write: false,
  banner: { js: `/* hyparquet ${pacote.version}, empacotado por ferramentas/empacotar/empacotar.mjs.\n${licenca}\n*/` },
  footer: { js: "if (typeof module !== 'undefined') module.exports = Leitor;" },
});
const destino = join(aqui, '../../web/leitor.js');
writeFileSync(destino, resultado.outputFiles[0].text.replace(/\r\n/g, '\n'));
console.log(`web/leitor.js: hyparquet ${pacote.version}, ${resultado.outputFiles[0].contents.length} bytes`);
