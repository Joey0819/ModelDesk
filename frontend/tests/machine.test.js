import test from 'node:test';
import assert from 'node:assert/strict';
import { machineInfo, machineLabel, machineShort, hostIsMacos, platformKnown } from '../src/machine.js';

test('machine labels come from the status platform block and never guess Mac without it', () => {
  // The neutral fallback must be platform-neutral when status is absent.
  for (const missing of [null, undefined, {}, { platform: null }, { platform: undefined }, { platform: {} }, { platform: 'broken' }]) {
    const neutral = machineInfo(missing);
    assert.equal(neutral.label, '运行 ModelDesk 的这台电脑');
    assert.equal(neutral.short, '这台电脑');
    assert.equal(neutral.isMacos, false);
    assert.equal(neutral.known, false);
    assert.equal(hostIsMacos(missing), false);
    assert.equal(platformKnown(missing), false);
    assert.doesNotMatch(neutral.label, /Mac/);
    assert.doesNotMatch(neutral.short, /Mac/);
  }
  assert.equal(machineLabel(null), '运行 ModelDesk 的这台电脑');
  assert.equal(machineShort(null), '这台电脑');
});

test('a Linux backend platform block yields the host machine labels verbatim', () => {
  const status = { platform: { system: 'Linux', is_macos: false, is_linux: true, is_windows: false, machine_label: '运行 ModelDesk 的这台电脑', machine_short: '这台电脑' } };
  const linux = machineInfo(status);
  assert.equal(linux.label, '运行 ModelDesk 的这台电脑');
  assert.equal(linux.short, '这台电脑');
  assert.equal(linux.system, 'Linux');
  assert.equal(linux.isMacos, false);
  assert.equal(linux.known, true);
  assert.equal(machineLabel(status), '运行 ModelDesk 的这台电脑');
  assert.equal(machineShort(status), '这台电脑');
  assert.equal(hostIsMacos(status), false);
  assert.equal(platformKnown(status), true);
});

test('a macOS backend platform block keeps the Mac-specific labels and gates', () => {
  const status = { platform: { system: 'Darwin', is_macos: true, is_linux: false, is_windows: false, machine_label: '这台 Mac', machine_short: 'Mac' } };
  const mac = machineInfo(status);
  assert.equal(mac.label, '这台 Mac');
  assert.equal(mac.short, 'Mac');
  assert.equal(mac.isMacos, true);
  assert.equal(hostIsMacos(status), true);
  // Only an explicit macOS report may claim Mac.
  assert.equal(hostIsMacos({ platform: { system: 'Darwin', is_macos: false } }), false);
});

test('partial payloads from older backends still resolve consistent labels', () => {
  const macosWithoutShort = machineInfo({ platform: { system: 'Darwin', is_macos: true, machine_label: '这台 Mac' } });
  assert.equal(macosWithoutShort.label, '这台 Mac');
  assert.equal(macosWithoutShort.short, 'Mac');
  const linuxWithoutLabels = machineInfo({ platform: { system: 'Linux', is_macos: false } });
  assert.equal(linuxWithoutLabels.label, '运行 ModelDesk 的这台电脑');
  assert.equal(linuxWithoutLabels.short, '这台电脑');
  const unknownSystem = machineInfo({ platform: { system: 'Windows', is_macos: false } });
  assert.equal(unknownSystem.isMacos, false);
  assert.equal(unknownSystem.known, true);
});
