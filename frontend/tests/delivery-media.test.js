import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { managedDeliveryMedia, mediaBundleUrl, resolveManagedMediaUrl, resolveReusedMediaLink, embeddedMediaIds, normalizeMediaCapabilities, loadMediaCapabilities, deliveryMediaWarnings } from '../src/delivery-media.js';
import { deriveArtifacts, deriveRoomArtifacts } from '../src/artifacts.js';
import { groupMessageRequest, groupOutputOptions, readGroupDraft } from '../src/group-state.js';

const jobId = '00000000-1111-2222-3333-444444444444';
const projectId = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const imageId = '11111111-2222-3333-4444-555555555555';
const videoId = '22222222-3333-4444-5555-666666666666';
const base = 'http://localhost:8001';
const phone = 'https://macbook-pro.example.ts.net:8443';
function asset(id = imageId, kind = 'image', extra = {}) {
  const url = `/api/media/jobs/${jobId}/assets/${id}/content?project_id=${projectId}`;
  return { id, job_id: jobId, project_id: projectId, kind, mime_type: kind === 'image' ? 'image/png' : 'video/mp4', name: kind === 'image' ? '配图.png' : '视频.mp4', url, download_url: `${url}&download=1`, sha256: 'a'.repeat(64), size: 123456, provider: 'gemini', model: 'image-model', slot_id: 'cover', caption: kind === 'image' ? '实际生成的封面' : '实际生成的视频', ...extra };
}
function job(media = [asset()], extra = {}) {
  return { id: jobId, project_id: projectId, mode: 'group', group_delivery_mode: 'auto', status: 'complete', result: { delivery: { status: 'complete', title: '图文文章', content: '# 完整正文\n\n实际完成的内容。', media } }, ...extra };
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const compiled = buildSync({ stdin: { contents: 'export {GroupDeliveryProgress} from "./src/components/GroupWorkspace.jsx"; export {MediaDelivery,DeliveryMediaProgress} from "./src/components/MediaDelivery.jsx"; export {default as ArtifactPanel} from "./src/components/ArtifactPanel.jsx"; export {default as DeliveryOutputOptions,MediaCapabilityStatus} from "./src/components/DeliveryOutputOptions.jsx";', resolveDir: root }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', write: false, external: ['react', 'react/jsx-runtime'], define: { 'import.meta.env': '{}' }, loader: { '.css': 'empty' } });
const filename = path.join(root, 'tests/media-virtual.cjs');
const module = new Module(filename); module.paths = Module._nodeModulePaths(path.dirname(filename)); module._compile(compiled.outputFiles[0].text, filename);
const { GroupDeliveryProgress, MediaDelivery, DeliveryMediaProgress, ArtifactPanel, DeliveryOutputOptions, MediaCapabilityStatus } = module.exports;
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));

test('managed media resolves on the paired phone API origin and retains exact job and project scope', () => {
  const image = asset();
  assert.equal(resolveManagedMediaUrl(image.url, image, job(), base), `${base}${image.url}`);
  assert.equal(resolveManagedMediaUrl(image.url, image, job(), phone), `${phone}${image.url}`);
  assert.equal(managedDeliveryMedia(job(), phone)[0].download_url, `${phone}${image.download_url}`);
  assert.doesNotMatch(managedDeliveryMedia(job(), phone)[0].url, /localhost/);
  const standalone = { ...image, project_id: null, url: image.url.split('?')[0], download_url: `${image.url.split('?')[0]}?download=1` };
  assert.equal(managedDeliveryMedia(job([standalone], { project_id: null }), base).length, 1);
});

test('older draft links follow verified reuse lineage without accepting forged scope or hash', () => {
  const immediateJob = '33333333-4444-5555-6666-777777777777';
  const originJob = '44444444-5555-6666-7777-888888888888';
  const originId = '55555555-6666-7777-8888-999999999999';
  const currentAsset = asset(imageId, 'image', { source_task_id: immediateJob, slot_id: `reused-${videoId}` });
  const current = job([currentAsset]);
  const origin = { asset_id: imageId, source_asset_id: originId, source_job_id: originJob, sha256: currentAsset.sha256 };
  current.result.delivery.media_reuse_origins = [origin];
  const oldUrl = `/api/media/jobs/${originJob}/assets/${originId}/content?project_id=${projectId}`;
  assert.equal(resolveReusedMediaLink(oldUrl, currentAsset, current, base), base + currentAsset.url);
  assert.equal(resolveReusedMediaLink(oldUrl + '&download=1', currentAsset, current, phone), phone + currentAsset.download_url);
  for (const url of [oldUrl + '&extra=1', 'https://other.example' + oldUrl, oldUrl.replace(projectId, originId)]) {
    assert.equal(resolveReusedMediaLink(url, currentAsset, current, base), null);
  }
  for (const alias of [{ ...origin, sha256: '0'.repeat(64) }, { ...origin, asset_id: videoId }]) {
    current.result.delivery.media_reuse_origins = [alias];
    assert.equal(resolveReusedMediaLink(oldUrl, currentAsset, current, base), null);
  }
});

test('in-progress harness media counts keep the full requirement while files arrive', () => {
  const current = job([asset()]);
  current.result.delivery.task_spec = { media: { images: 9, videos: 0 } };
  current.result.delivery.status = 'working';
  const html = render(DeliveryMediaProgress, { job: current });
  assert.match(html, /配图 1\/9/);
  assert.doesNotMatch(html, /配图 1\/1/);
});

test('media URLs reject foreign jobs, projects, external hosts, credentials and executable protocols', () => {
  const image = asset();
  for (const value of [`https://evil.example${image.url}`, `http://localhost:5173${image.url}`, `//evil.example${image.url}`, 'data:image/png;base64,AAAA', 'file:///tmp/image.png', 'javascript:alert(1)', `${image.url}#fragment`, `${image.url}&x=1`, `${image.url}&download=true`, `${image.url}&project_id=${projectId}`, image.url.replace(jobId, videoId), image.url.replace(projectId, videoId), `http://user:password@localhost:8001${image.url}`]) {
    assert.equal(resolveManagedMediaUrl(value, image, job(), base), null, value);
  }
  assert.equal(resolveManagedMediaUrl(image.url, { ...image, job_id: videoId }, job(), base), null);
  assert.equal(resolveManagedMediaUrl(image.url, { ...image, project_id: videoId }, job(), base), null);
});

test('media manifest rejects missing integrity, active SVG, non-media files and duplicates', () => {
  const invalid = [asset(imageId, 'image', { sha256: '' }), asset(imageId, 'image', { size: 0 }), asset(imageId, 'image', { mime_type: 'image/svg+xml' }), asset(imageId, 'image', { mime_type: 'text/html' }), asset(imageId, 'image', { download_url: 'https://evil.example/file' })];
  for (const entry of invalid) assert.deepEqual(managedDeliveryMedia(job([entry]), base), []);
  assert.equal(managedDeliveryMedia(job([asset(), asset()]), base).length, 1);
});

test('article renders generated images inline once, remaining assets as gallery, with originals and provenance', () => {
  const first = asset(); const second = asset(videoId, 'image', { name: '示意图.png', caption: '第二张实际图片' });
  const current = job([first, second]);
  const content = `# 可发布文章\n\n![封面说明](${first.url})\n\n完整的图文正文。`;
  const html = render(MediaDelivery, { job: current, content, apiBase: phone });
  assert.equal((html.match(/<img /g) || []).length, 2);
  assert.equal((html.match(new RegExp(`src="${phone}${first.url.replace('?', '\\?')}`, 'g')) || []).length, 1);
  assert.match(html, /本次生成的图片与视频/); assert.match(html, /第二张实际图片/);
  assert.match(html, /下载原始图片/); assert.match(html, /gemini · image-model/); assert.match(html, /download=1/);
  assert.match(html, /loading="lazy"/); assert.doesNotMatch(html, /localhost/);
});

test('reference Markdown images deduplicate while code samples and escaped image syntax do not hide files', () => {
  const current = job(); const media = managedDeliveryMedia(current, base); const image = asset();
  const reference = `![封面][cover]\n\n[cover]: ${image.url}`;
  assert.deepEqual([...embeddedMediaIds(reference, media)], [imageId]);
  assert.equal((render(MediaDelivery, { job: current, content: reference, apiBase: base }).match(/<img /g) || []).length, 1);
  for (const content of [`\`![封面](${image.url})\``, `\`\`\`md\n![封面](${image.url})\n\`\`\``, `\\![封面](${image.url})`]) assert.equal(embeddedMediaIds(content, media).size, 0);
});

test('video uses a real managed asset with native controls and source-bound download', () => {
  const video = asset(videoId, 'video');
  const html = render(MediaDelivery, { job: job([video]), content: '完整视频交付说明', apiBase: phone });
  assert.match(html, /<video /); assert.match(html, /controls=""/); assert.match(html, /playsInline=""/); assert.match(html, /preload="metadata"/);
  assert.match(html, /下载原始视频/); assert.match(html, new RegExp(videoId));
  assert.doesNotMatch(html, /autoplay|<iframe|blob:|data:/i);
});

test('article media links resolve through the backend port and paired phone origin while ordinary links stay intact', () => {
  const video = asset(videoId, 'video');
  const content = `[视频：实际生成的视频](${video.url})\n\n[下载视频原件](${video.download_url})\n\n[来源](https://example.test/article)`;
  for (const apiBase of [base, phone]) {
    const html = render(MediaDelivery, { job: job([video]), content, apiBase });
    assert.ok(html.includes(`<a href="${apiBase}${video.url}">视频：实际生成的视频</a>`));
    assert.ok(html.includes(`<a href="${apiBase}${video.download_url.replace('&', '&amp;')}">下载视频原件</a>`));
    assert.match(html, /<a href="https:\/\/example\.test\/article">来源<\/a>/);
    assert.doesNotMatch(html, /href="\/api\/media\/jobs\//);
  }
});

test('unverified Markdown media never fetches external, data, file, or another job asset URLs', () => {
  const current = job([]);
  const content = `![external](https://evil.example/pixel.png)\n\n![data](data:image/png;base64,AAAA)\n\n![file](file:///tmp/a.png)\n\n![foreign](${asset().url.replace(jobId, videoId)})\n\n<script>execute()</script>`;
  const html = render(MediaDelivery, { job: current, content, apiBase: base });
  assert.doesNotMatch(html, /<img|<video|<iframe|<script|src=|evil\.example|file:\/\//);
  assert.match(html, /尚未保存/);
});

test('reused video source links open the verified current copy on desktop and phone', () => {
  const oldJob = '33333333-4444-5555-6666-777777777777';
  for (const scope of [projectId, null]) {
    const suffix = scope ? `?project_id=${scope}` : '';
    const source = `/api/media/jobs/${oldJob}/assets/${imageId}/content${suffix}`;
    const current = `/api/media/jobs/${jobId}/assets/${videoId}/content${suffix}`;
    const download = `${current}${scope ? '&' : '?'}download=1`;
    const video = asset(videoId, 'video', { project_id: scope, url: current, download_url: download, source_task_id: oldJob, slot_id: `reused-${imageId}` });
    const content = `[查看原视频](${source})\n\n[下载原视频](${source}${scope ? '&' : '?'}download=1)`;
    for (const apiBase of [base, phone]) {
      const html = render(MediaDelivery, { job: job([video], { project_id: scope }), content, apiBase });
      assert.ok(html.includes(`<a href="${apiBase}${current}">查看原视频</a>`));
      assert.ok(html.includes(`<a href="${apiBase}${download.replace('&', '&amp;')}">下载原视频</a>`));
      assert.doesNotMatch(html, new RegExp(oldJob));
      assert.equal((html.match(/<video /g) || []).length, 1);
    }
  }
});

test('reused links require exact provenance, project scope and safe API origin', () => {
  const oldJob = '33333333-4444-5555-6666-777777777777';
  const source = `/api/media/jobs/${oldJob}/assets/${imageId}/content?project_id=${projectId}`;
  const video = asset(videoId, 'video', { source_task_id: oldJob, slot_id: `reused-${imageId}` });
  for (const url of [source.replace(oldJob, videoId), source.replace(imageId, videoId), source.replace(projectId, jobId), source.split('?')[0], `https://other.test${source}`, `${source}&redirect=1`, `${source}&download=0`, `${source}#fragment`]) {
    assert.equal(resolveReusedMediaLink(url, video, job([video]), base), null, url);
  }
  assert.equal(resolveReusedMediaLink(source, { ...video, slot_id: 'cover' }, job([video]), base), null);
  assert.equal(resolveReusedMediaLink(source, { ...video, job_id: oldJob }, job([video]), base), null);
});

test('reused inline and reference illustrations retain their article position without a duplicate gallery', () => {
  const oldJob = '33333333-4444-5555-6666-777777777777';
  for (const scope of [projectId, null]) {
    const suffix = scope ? `?project_id=${scope}` : '';
    const source = `/api/media/jobs/${oldJob}/assets/${imageId}/content${suffix}`;
    const current = `/api/media/jobs/${jobId}/assets/${videoId}/content${suffix}`;
    const image = asset(videoId, 'image', { project_id: scope, url: current, download_url: `${current}${scope ? '&' : '?'}download=1`, source_task_id: oldJob, slot_id: `reused-${imageId}` });
    for (const apiBase of [base, phone]) {
      for (const content of [`Before\n\n![保留的章节配图](${source})\n\nAfter`, `Before\n\n![保留的章节配图][figure]\n\nAfter\n\n[figure]: ${source}`]) {
        const html = render(MediaDelivery, { job: job([image], { project_id: scope }), content, apiBase });
        assert.equal((html.match(/<img /g) || []).length, 1);
        assert.ok(html.includes(`src="${apiBase}${current}"`));
        assert.ok(html.indexOf('Before') < html.indexOf('<img '));
        assert.ok(html.indexOf('<img ') < html.indexOf('After'));
        assert.doesNotMatch(html, /尚未保存|delivery-media-gallery/);
        assert.doesNotMatch(html, new RegExp(oldJob));
      }
      const code = render(MediaDelivery, { job: job([image], { project_id: scope }), content: `\`![sample](${source})\``, apiBase });
      assert.match(code, /delivery-media-gallery/);
      assert.equal((code.match(/<img /g) || []).length, 1);
    }
  }
});

test('reused image references never bypass source, project or host checks', () => {
  const oldJob = '33333333-4444-5555-6666-777777777777';
  const source = `/api/media/jobs/${oldJob}/assets/${imageId}/content?project_id=${projectId}`;
  const image = asset(videoId, 'image', { source_task_id: oldJob, slot_id: `reused-${imageId}` });
  for (const invalid of [source.replace(oldJob, videoId), source.replace(projectId, jobId), `https://evil.example${source}`, `${source}&redirect=1`, 'data:image/png;base64,AAAA']) {
    const html = render(MediaDelivery, { job: job([image]), content: `![不匹配的来源](${invalid})`, apiBase: base });
    assert.match(html, /尚未保存/);
    assert.match(html, /delivery-media-gallery/);
    assert.equal((html.match(/<img /g) || []).length, 1);
    assert.doesNotMatch(html, /evil\.example|data:image|redirect=1/);
  }
});

test('saved media stays viewable and downloadable before prose and after failed synthesis', () => {
  for (const kind of ['image', 'video']) {
    for (const status of ['running', 'error', 'cancelled', 'interrupted']) {
      const current = job([asset(imageId, kind)], { status });
      current.result.delivery.content = '';
      current.result.delivery.status = status === 'running' ? 'working' : 'partial';
      current.result.delivery.limitations = ['交付正文尚未完成'];
      const original = JSON.stringify(current);
      const artifacts = deriveArtifacts(current);
      assert.equal(artifacts.length, 1);
      assert.equal(artifacts[0].content, '');
      assert.equal(artifacts[0].media_only, true);
      assert.equal(artifacts[0].status, status === 'running' ? 'live' : 'partial');
      assert.equal(JSON.stringify(current), original);
      const html = render(ArtifactPanel, { job: current });
      assert.match(html, kind === 'image' ? /<img / : /<video /);
      assert.match(html, /媒体预览|下载图文成果包/);
      assert.match(html, /下载原件/);
      assert.match(html, /交付正文尚未完成/);
      assert.doesNotMatch(html, /artifact-empty|下载 Markdown|下载当前成果 Markdown|复制内容/);
      const gallery = deriveRoomArtifacts({ jobs: [current] });
      assert.equal(gallery[0].raw_id, 'group:delivery');
      assert.equal(gallery[0].category, 'media');
      assert.equal(gallery[0].origin_job, current);
    }
  }
});

test('media-only delivery requires a valid scoped manifest and follows the paired phone origin', () => {
  for (const invalid of [[], [asset(imageId, 'image', { sha256: '' })], [asset(imageId, 'image', { job_id: videoId })], [asset(imageId, 'image', { project_id: videoId })], [asset(imageId, 'image', { url: 'https://evil.example/file.png' })]]) {
    const current = job(invalid, { status: 'error' });
    current.result.delivery.content = '';
    assert.deepEqual(deriveArtifacts(current), []);
    const html = render(ArtifactPanel, { job: current });
    assert.match(html, /artifact-empty/);
    assert.doesNotMatch(html, /<img |<video |下载图文成果包|下载 Markdown/);
  }
  const image = asset();
  const current = job([{ ...image, url: `${phone}${image.url}`, download_url: `${phone}${image.download_url}` }], { status: 'running' });
  current.result.delivery.content = '';
  assert.equal(deriveArtifacts(current, phone).length, 1);
  assert.deepEqual(deriveArtifacts(current, base), []);
});

test('bundle and artifact links preserve project scope, work on phone, and accompany Markdown', () => {
  const current = job();
  assert.equal(mediaBundleUrl(current, phone), `${phone}/api/media/jobs/${jobId}/bundle?project_id=${projectId}`);
  assert.equal(mediaBundleUrl({ ...current, id: '../unsafe' }, phone), null);
  assert.equal(mediaBundleUrl({ ...current, mode: 'chat' }, phone), null);
  assert.deepEqual(deriveArtifacts(current)[0].media, current.result.delivery.media);
  const html = render(ArtifactPanel, { job: current });
  assert.match(html, /下载图文成果包/); assert.match(html, /下载 Markdown/); assert.match(html, /下载原始图片/);
  assert.match(html, new RegExp(`/api/media/jobs/${jobId}/bundle\\?project_id=${projectId}`));
  assert.doesNotMatch(render(ArtifactPanel, { job: job([]) }), /下载图文成果包/);
});

test('media progress reports saved files and real task errors instead of promoting a generation plan', () => {
  const current = job();
  current.result.delivery.media_requirements = { images: 2, videos: 1 };
  current.result.delivery.media_tasks = [
    { id: 'one', kind: 'image', status: 'complete', asset_id: imageId, provider: 'gemini' },
    { id: 'two', kind: 'image', status: 'error', error: '没有收到图片文件', provider: 'openai' },
    { id: 'three', kind: 'video', status: 'complete', provider: 'muse' },
  ];
  const html = render(DeliveryMediaProgress, { job: current, apiBase: base });
  assert.match(html, /配图 1\/2/); assert.match(html, /视频 0\/1/); assert.match(html, /生成失败/); assert.match(html, /没有收到图片文件/);
  assert.match(html, /未收到已保存文件/); assert.match(html, /gemini/); assert.match(html, /muse/);
});

test('native media progress accepts pinned asset sets and cancelled work retains an explicit stopped state', () => {
  const current = job([asset(), asset(videoId, 'video')]);
  current.result.delivery.media_requirements = { images: 1, videos: 1 };
  current.result.delivery.media_tasks = [
    { id: 'native-image', kind: 'image', status: 'complete', asset_id: imageId, asset_ids: [imageId], provider: 'muse' },
    { id: 'native-video', kind: 'video', status: 'complete', asset_id: videoId, asset_ids: [videoId], provider: 'muse' },
    { id: 'cancelled-image', kind: 'image', status: 'cancelled', provider: 'openai' },
  ];
  const html = render(DeliveryMediaProgress, { job: current, apiBase: base });
  assert.match(html, /配图 1\/1/); assert.match(html, /视频 1\/1/); assert.match(html, /已生成/); assert.match(html, /已取消/);
  assert.doesNotMatch(html, /未收到已保存文件|状态待同步|small-spinner/);
  current.result.delivery.media_tasks = [{ id: 'wrong-kind', kind: 'video', status: 'complete', asset_id: imageId }, { id: 'incomplete-set', kind: 'image', status: 'complete', asset_ids: [imageId, videoId] }];
  assert.equal((render(DeliveryMediaProgress, { job: current, apiBase: base }).match(/未收到已保存文件/g) || []).length, 2);
});

test('unused native resources stay in collapsed safe diagnostics without downgrading delivered media', () => {
  const warning = 'Muse 返回的一个资源未通过下载或真实媒体校验，未作为成品展示。';
  const current = job();
  current.result.delivery.media_requirements = { images: 1, videos: 0 };
  current.result.delivery.media_warnings = [warning, warning, { message: 'not text' }, 'https://user:secret@example.test/file', '<script>execute()</script>', '/Users/private/secret.png'];
  assert.deepEqual(deliveryMediaWarnings(current.result.delivery), [warning]);
  const html = render(DeliveryMediaProgress, { job: current, apiBase: base });
  assert.match(html, /配图 1\/1/); assert.match(html, /<details class="delivery-media-warnings"><summary>未采用的资源说明（1）<\/summary>/);
  assert.match(html, /未通过下载或真实媒体校验/); assert.doesNotMatch(html, /open=|secret|<script|execute|<a |部分输出|未收到已保存文件/);
  assert.equal(deriveArtifacts(current)[0].status, 'complete');
  assert.doesNotMatch(render(DeliveryMediaProgress, { job: job(), apiBase: base }), /未采用的资源说明/);
  const diagnosticsOnly = job([]); diagnosticsOnly.result.delivery.media_warnings = [warning];
  assert.match(render(DeliveryMediaProgress, { job: diagnosticsOnly, apiBase: base }), /未采用的资源说明（1）/);
});

test('output format settings survive per-room drafts and only bounded explicit fields reach the request', () => {
  const draft = readGroupDraft({ getItem: () => JSON.stringify({ outputFormat: 'illustrated', imageCount: 4, mediaProvider: 'gemini' }) }, projectId, jobId);
  assert.deepEqual(groupOutputOptions(draft), { outputFormat: 'illustrated', imageCount: 4, mediaProvider: 'gemini' });
  const request = groupMessageRequest({ prompt: 'Write article', recipientIds: ['writer'], context: { output_format: 'video', image_count: 999, api_key: 'secret' }, ...groupOutputOptions(draft) });
  assert.equal(request.output_format, 'illustrated'); assert.equal(request.image_count, 4); assert.equal(request.media_provider, 'gemini');
  assert.deepEqual(request.recipient_member_ids, ['writer']); assert.equal(request.api_key, undefined);
  assert.deepEqual(groupOutputOptions({ outputFormat: 'unsafe', imageCount: 100, mediaProvider: 'other' }), { outputFormat: 'auto', imageCount: 2, mediaProvider: 'auto' });
  assert.equal(groupOutputOptions({ outputFormat: 'video', mediaProvider: 'openai' }).mediaProvider, 'auto');
  assert.equal(groupMessageRequest({ prompt: 'Discuss', recipientIds: [], context: {}, deliveryMode: 'discussion', outputFormat: 'illustrated' }).delivery_mode, 'discussion');
});

test('simple output controls keep advanced settings collapsed and text tasks free of generation choices', () => {
  const html = render(DeliveryOutputOptions, { value: { outputFormat: 'illustrated', imageCount: 2, mediaProvider: 'auto' }, onChange() {} });
  assert.match(html, /aria-label="成果形式"/); assert.match(html, /value="illustrated" selected=""/); assert.match(html, /<details class="group-output-settings"><summary>生成设置/);
  assert.match(html, /配图数量/); assert.match(html, /复用当前登录网关/); assert.doesNotMatch(html, /必须.*API|open=""/);
  assert.doesNotMatch(render(DeliveryOutputOptions, { value: { outputFormat: 'text' }, onChange() {} }), /生成设置|配图数量/);
  const video = render(DeliveryOutputOptions, { value: { outputFormat: 'video' }, disabled: true, onChange() {} });
  assert.match(video, /Muse 原生生成仅在 Mac 上可用/); assert.match(video, /disabled=""/); assert.doesNotMatch(video, /OpenAI 生图|Gemini 生图|配图数量/);
});

test('changing from OpenAI illustrations to video resets the actual saved and submitted provider', () => {
  const illustrated = { outputFormat: 'illustrated', imageCount: 3, mediaProvider: 'openai' };
  const video = groupOutputOptions(illustrated, { outputFormat: 'video' });
  assert.deepEqual(video, { outputFormat: 'video', imageCount: 3, mediaProvider: 'auto' });
  const restored = readGroupDraft({ getItem: () => JSON.stringify(video) }, projectId, jobId);
  assert.equal(restored.mediaProvider, 'auto');
  const request = groupMessageRequest({ prompt: 'Make a video', context: {}, recipientIds: [], ...video });
  assert.equal(request.output_format, 'video'); assert.equal(request.media_provider, 'auto');
  const html = render(DeliveryOutputOptions, { value: video, onChange() {} });
  assert.match(html, /value="auto" selected=""/); assert.doesNotMatch(html, /value="openai"/);
  assert.equal(groupOutputOptions({ ...illustrated, mediaProvider: 'muse' }, { outputFormat: 'video' }).mediaProvider, 'muse');
  assert.equal(groupOutputOptions(video, { outputFormat: 'illustrated' }).mediaProvider, 'auto');
});

test('capability metadata distinguishes verified image receipts from candidates and strips private fields', () => {
  const entries = normalizeMediaCapabilities([
    { id: 'gpt-image-1', provider: 'openai', model: 'gpt-image-1', kind: 'image', available: true, verified: true, api_key: 'secret', base_url: 'https://user:secret@example.test' },
    { id: 'gemini-image', provider: 'gemini', kind: 'image', available: true, verified: 'true', status: 'verified' },
    { id: 'grok-image', provider: 'xai', kind: 'image', available: false, verified: true },
    { id: 'text', provider: 'openai', kind: 'text', available: true, verified: true },
  ]);
  assert.equal(entries.length, 3); assert.equal(entries[0].verified, true); assert.equal(entries[1].verified, false); assert.equal(entries[2].verified, false);
  assert.doesNotMatch(JSON.stringify(entries), /secret|api_key|base_url/);
  const html = render(MediaCapabilityStatus, { state: { status: 'ready', entries }, onRefresh() {} });
  assert.match(html, /实际生图能力/); assert.match(html, /ChatGPT \/ OpenAI/); assert.match(html, /gpt-image-1/);
  assert.match(html, /已实测生图/); assert.match(html, /待验证/); assert.match(html, /暂不可用/);
  assert.match(html, /曾返回并保存了实际图片/); assert.doesNotMatch(html, /secret|添加 API Key/);
});

test('capability request uses the current phone origin and reports a safe non-blocking failure', async () => {
  let called;
  const request = async (url) => { called = url; return { ok: true, json: async () => [{ id: 'gpt-image-1', model: 'gpt-image-1', provider: 'openai', kind: 'image', available: true, verified: true }] }; };
  assert.equal((await loadMediaCapabilities(phone, request))[0].verified, true);
  assert.equal(called, `${phone}/api/media/capabilities`);
  await assert.rejects(loadMediaCapabilities(phone, async () => { throw new Error('https://user:secret@example.test'); }), (error) => /可继续提交任务/.test(error.message) && !/secret|https:/.test(error.message));
  await assert.rejects(loadMediaCapabilities(phone, async () => ({ ok: false, json: async () => ({ token: 'secret' }) })), /执行时会再次检查/);
  const html = render(MediaCapabilityStatus, { state: { status: 'error', entries: [], error: '暂时无法读取生图能力，可继续提交任务。' }, onRefresh() {} });
  assert.match(html, /可继续提交任务/); assert.match(html, /重新检查/); assert.doesNotMatch(html, /disabled|必须.*API|secret/);
  assert.match(render(MediaCapabilityStatus, { state: { status: 'ready', entries: [] }, onRefresh() {} }), /检查已有登录/);
});


test('chat exposes saved media after synthesis fails and keeps long execution evidence collapsed', () => {
  const current = job([asset(videoId, 'video')], { status: 'error' });
  current.result.delivery.content = '';
  current.result.delivery.status = 'partial';
  current.result.delivery.steps = [{ id: 'synthesis', member_name: 'Editor', status: 'error', instruction: 'Combine actual files', error: 'Synthesis timed out' }];
  current.result.delivery.limitations = ['Final prose is missing'];
  const html = render(GroupDeliveryProgress, { job: current, onOpenArtifacts() {} });
  assert.match(html, /查看部分成果/);
  assert.match(html, /<details class="group-execution-details"><summary>/);
  assert.match(html, /有步骤失败/);
  assert.match(html, /Synthesis timed out/);
  assert.match(html, /<details class="group-delivery-receipt"><summary>/);
  assert.doesNotMatch(html, /完整成果已生成|尚未生成完整成果/);
});
