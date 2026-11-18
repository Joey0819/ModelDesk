import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { startNavigationFixture } from './navigation-fixture.mjs';

const fixture = await startNavigationFixture();
try {
  const output = process.env.BOMBADIL_OUTPUT || await mkdtemp(join(tmpdir(), 'modeldesk-navigation-'));
  const specification = fileURLToPath(new URL('./navigation.bombadil.ts', import.meta.url));
  const [width, height] = process.argv.includes('--mobile') ? ['390', '844'] : ['1320', '900'];
  console.log(`Bombadil navigation results: ${output}`);
  const run = spawn(process.env.BOMBADIL_BIN || 'bombadil', [
    'browser', 'test', '--headless', '--width', width, '--height', height, '--time-limit', '30s',
    '--exit-on-violation', '--output-path', output, fixture.origin, specification,
  ], { stdio: 'inherit' });
  const [status] = await once(run, 'exit');
  if (fixture.writes.length) throw new Error(`Navigation attempted fixture writes: ${fixture.writes.join(', ')}`);
  if (status === 0) {
    const trace = await readFile(join(output, 'trace.jsonl'), 'utf8');
    const entries = trace.trim().split('\n').map((line) => JSON.parse(line));
    const explored = entries.filter(({ action }) => action && action !== 'Wait').length;
    if (!explored) throw new Error('Bombadil did not execute any navigation actions');
    if (!entries.at(-1).snapshots.some(({ name, value }) => name === 'navigation' && value)) {
      throw new Error('The workspace was unavailable at the end of the navigation check');
    }
    console.log(`Checked ${explored} navigation actions.`);
  }
  process.exitCode = status ?? 1;
} finally {
  await fixture.close();
}
