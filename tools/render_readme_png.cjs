// Developer-only SVG raster export. See render_readme_art.py for setup.
const fs = require('node:fs');
const path = require('node:path');
const { Resvg } = require(process.argv[2] || '@resvg/resvg-js');
const assets = path.resolve(__dirname, '../docs/assets');
for (const name of ['social-preview', 'social-preview.zh-CN', 'reasoning-loop', 'reasoning-loop.zh-CN']) {
  const svg = fs.readFileSync(path.join(assets, `${name}.svg`), 'utf8');
  const png = new Resvg(svg, { font: { loadSystemFonts: true } }).render().asPng();
  fs.writeFileSync(path.join(assets, `${name}.png`), png);
  console.log(`${name}.png: ${png.length} bytes`);
}
