const fs = require('node:fs/promises');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const sharp = require('sharp');
const execFileAsync = promisify(execFile);

async function main() {
  const assets = path.join(__dirname, '..', 'assets');
  const source = path.join(assets, 'ModelDesk.svg');
  const iconset = path.join(assets, 'ModelDesk.iconset');
  await fs.mkdir(iconset, { recursive: true });
  const outputs = [
    [16, 'icon_16x16.png'], [32, 'icon_16x16@2x.png'],
    [32, 'icon_32x32.png'], [64, 'icon_32x32@2x.png'],
    [128, 'icon_128x128.png'], [256, 'icon_128x128@2x.png'],
    [256, 'icon_256x256.png'], [512, 'icon_256x256@2x.png'],
    [512, 'icon_512x512.png'], [1024, 'icon_512x512@2x.png'],
  ];
  await Promise.all(outputs.map(([size, name]) => sharp(source).resize(size, size).png().toFile(path.join(iconset, name))));
  await sharp(source).png().toFile(path.join(assets, 'ModelDesk-1024.png'));
  await execFileAsync('/usr/bin/iconutil', ['-c', 'icns', iconset, '-o', path.join(assets, 'ModelDesk.icns')]);
  console.log('ModelDesk icon ready');
}

main().catch(() => { console.error('Could not build the ModelDesk icon.'); process.exitCode = 1; });
