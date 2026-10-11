const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const execFileAsync = promisify(execFile);

async function main() {
  if (process.platform !== 'darwin' || process.arch !== 'arm64') throw new Error('This package target requires an Apple Silicon Mac.');
  const root = path.join(__dirname, '..');
  const packageJSON = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
  const staging = path.join(root, 'dist', 'app-source');
  await fs.rm(staging, { recursive: true, force: true });
  await fs.mkdir(staging, { recursive: true });
  for (const name of ['main.cjs', 'runtime.cjs', 'status.html']) {
    await fs.copyFile(path.join(root, name), path.join(staging, name));
  }
  // Explicit allowlist: runtime data, OAuth files, node_modules, and tests never enter the bundle.
  await fs.writeFile(path.join(staging, 'package.json'), JSON.stringify({
    name: packageJSON.name,
    productName: packageJSON.productName,
    version: packageJSON.version,
    description: packageJSON.description,
    private: true,
    main: packageJSON.main,
  }, null, 2));
  const { packager } = await import('@electron/packager');
  const checksums = JSON.parse(await fs.readFile(path.join(root, 'node_modules', 'electron', 'checksums.json'), 'utf8'));
  const zipName = `electron-v${packageJSON.devDependencies.electron}-darwin-arm64.zip`;
  const zipDirectory = path.join(root, 'dist', 'vendor');
  let verifiedZipDirectory;
  try {
    const archive = await fs.readFile(path.join(zipDirectory, zipName));
    if (crypto.createHash('sha256').update(archive).digest('hex') !== checksums[zipName]) {
      throw new Error('The cached Electron archive does not match its official SHA256 checksum.');
    }
    verifiedZipDirectory = zipDirectory;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const bundles = await packager({
    dir: staging,
    name: 'ModelDesk',
    executableName: 'ModelDesk',
    platform: 'darwin',
    arch: 'arm64',
    electronVersion: packageJSON.devDependencies.electron,
    appVersion: packageJSON.version,
    appBundleId: 'local.modeldesk.workbench',
    appCategoryType: 'public.app-category.productivity',
    icon: path.join(root, 'assets', 'ModelDesk.icns'),
    out: path.join(root, 'dist', 'package'),
    overwrite: true,
    asar: true,
    extendInfo: { NSHighResolutionCapable: true },
    prune: false,
    download: { checksums },
    electronZipDir: verifiedZipDirectory,
  });
  const sourceBundle = path.join(bundles[0], 'ModelDesk.app');
  const installedRoot = process.env.MODEL_DESK_WORKSPACE || path.resolve(root, '..', '..', 'llm-tools');
  if (!path.isAbsolute(installedRoot)) throw new Error('MODEL_DESK_WORKSPACE must be an absolute path.');
  const destination = path.join(installedRoot, 'releases', 'ModelDesk.app');
  await fs.mkdir(path.dirname(destination), { recursive: true });
  const stagingBundle = path.join(root, 'dist', 'install', 'ModelDesk.app');
  const previousBundle = path.join(root, 'dist', 'previous', 'ModelDesk.app');
  await fs.rm(path.dirname(stagingBundle), { recursive: true, force: true });
  await fs.mkdir(path.dirname(stagingBundle), { recursive: true });
  await fs.cp(sourceBundle, stagingBundle, { recursive: true, verbatimSymlinks: true });
  await execFileAsync('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', stagingBundle]);
  await execFileAsync('/usr/bin/codesign', ['--verify', '--deep', '--strict', stagingBundle]);
  await fs.rm(path.dirname(previousBundle), { recursive: true, force: true });
  await fs.mkdir(path.dirname(previousBundle), { recursive: true });
  let hadPrevious = false;
  try {
    await fs.rename(destination, previousBundle);
    hadPrevious = true;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  try {
    await fs.rename(stagingBundle, destination);
  } catch (error) {
    if (hadPrevious) await fs.rename(previousBundle, destination);
    throw error;
  }
  await fs.rm(previousBundle, { recursive: true, force: true });
  console.log(`Packaged ModelDesk ${packageJSON.version}: ${destination}`);
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
