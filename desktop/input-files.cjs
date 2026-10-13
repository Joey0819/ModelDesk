const fs = require('node:fs');
const path = require('node:path');
const { TextDecoder } = require('node:util');

const MAX_FILE_BYTES = 20_000_000;
const MAX_IMAGE_BYTES = 10_000_000;
const MAX_BATCH_BYTES = 100_000_000;
const MAX_FILES = 100;
const TEXT_EXTENSIONS = ['txt', 'md', 'csv', 'json', 'py', 'js', 'ts', 'jsx', 'tsx', 'html', 'yaml', 'yml', 'rst', 'toml', 'ini', 'xml', 'sql', 'c', 'cpp', 'h', 'java', 'go', 'rs', 'sh'];
const INPUT_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp', 'pdf', 'docx', ...TEXT_EXTENSIONS];
const PRIVATE_DIRECTORIES = new Set(['.private', '.git', 'node_modules', '__pycache__', '.venv', '.venv-camel', '.venv-crewai']);

function safeSelectedPath(value) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || /[\x00-\x1f\x7f]/.test(value)) return false;
  const name = path.basename(value);
  if (name.startsWith('.') || Buffer.byteLength(name, 'utf8') > 255 || /^(?:credentials?|secrets?|tokens?|auth)(?:[._-]|$)/i.test(name)) return false;
  const parts = path.resolve(value).split(path.sep);
  if (parts.some(part => PRIVATE_DIRECTORIES.has(part))) return false;
  return INPUT_EXTENSIONS.includes(path.extname(name).slice(1).toLowerCase());
}

function inputMime(name, bytes) {
  const extension = path.extname(name).slice(1).toLowerCase();
  if (extension === 'png') return bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) ? 'image/png' : null;
  if (['jpg', 'jpeg'].includes(extension)) return bytes.subarray(0, 3).equals(Buffer.from([255,216,255])) ? 'image/jpeg' : null;
  if (extension === 'webp') return bytes.subarray(0, 4).toString() === 'RIFF' && bytes.subarray(8, 12).toString() === 'WEBP' ? 'image/webp' : null;
  if (extension === 'pdf') return bytes.subarray(0, 5).toString() === '%PDF-' ? 'application/pdf' : null;
  if (extension === 'docx') return bytes.subarray(0, 4).equals(Buffer.from([80,75,3,4])) ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' : null;
  if (TEXT_EXTENSIONS.includes(extension)) {
    try { if (bytes.includes(0)) return null; new TextDecoder('utf-8', { fatal: true }).decode(bytes); return 'text/plain'; } catch { return null; }
  }
  return null;
}

async function readSelectedFile(filename) {
  if (!safeSelectedPath(filename)) throw new Error('文件名称、类型或目录不适合导入');
  // Every component is checked; selection cannot turn a symlink into an implicit read.
  let component = path.parse(filename).root;
  for (const part of path.resolve(filename).slice(component.length).split(path.sep)) {
    component = path.join(component, part);
    if ((await fs.promises.lstat(component)).isSymbolicLink()) throw new Error('不导入符号链接文件');
  }
  const handle = await fs.promises.open(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const before = await handle.stat();
    if (!before.isFile() || !before.size || before.size > MAX_FILE_BYTES) throw new Error('文件为空、不是普通文件或超过20MB');
    const bytes = Buffer.alloc(Math.min(before.size + 1, MAX_FILE_BYTES + 1));
    let count = 0;
    while (count < bytes.length) {
      const read = await handle.read(bytes, count, bytes.length - count, count);
      if (!read.bytesRead) break;
      count += read.bytesRead;
    }
    const after = await handle.stat();
    if (count !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs) throw new Error('文件读取期间发生变化，请重新选择');
    const content = bytes.subarray(0, count);
    const type = inputMime(filename, content);
    if (!type) throw new Error('文件内容与支持的类型不匹配');
    if (type.startsWith('image/') && count > MAX_IMAGE_BYTES) throw new Error('图片超过10MB');
    return { name: path.basename(filename), type, size: count, data_base64: content.toString('base64') };
  } finally { await handle.close(); }
}

async function collectSelectedFiles(paths) {
  if (!Array.isArray(paths)) throw new Error('文件选择结果无效');
  const files = [], omitted = [];
  let total = 0;
  for (const [index, filename] of paths.entries()) {
    const label = typeof filename === 'string' ? path.basename(filename) : '无效文件';
    if (index >= MAX_FILES) { omitted.push({ path: label, reason: '每批最多100个文件' }); continue; }
    try {
      const file = await readSelectedFile(filename);
      if (total + file.size > MAX_BATCH_BYTES) throw new Error('每批总量最多100MB');
      files.push(file); total += file.size;
    } catch (error) { omitted.push({ path: label, reason: error.code ? '文件无法读取，请重新选择' : error.message }); }
  }
  return { files, omitted };
}

module.exports = { INPUT_EXTENSIONS, MAX_FILES, MAX_FILE_BYTES, MAX_IMAGE_BYTES, MAX_BATCH_BYTES, safeSelectedPath, inputMime, readSelectedFile, collectSelectedFiles };
