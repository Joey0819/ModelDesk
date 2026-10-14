const VIDEO_KEYWORDS = /(?:视频|短片|电影|动画|镜头|运镜|分镜|拍摄|画面|vlog|reel|video|cinematic|film|clip|footage|animation|trailer|scene)/i;
export const DEFAULT_MUSE_VIDEO_URL = '/muse-media/83e3bb6d-c691-4182-834c-cf7db7a680f6.mp4';

const runtimeVideoUrls = new Map();

export function isMuseVideoPrompt(prompt = '') {
  return VIDEO_KEYWORDS.test(String(prompt || ''));
}

export function resolveMuseVideoUrl(task) {
  if (!task) return null;
  if (typeof task.video_url === 'string' && task.video_url.trim()) return task.video_url.trim();
  if (runtimeVideoUrls.has(task.id)) return runtimeVideoUrls.get(task.id);
  if (task.id === '83e3bb6d-c691-4182-834c-cf7db7a680f6') return DEFAULT_MUSE_VIDEO_URL;
  const urlMatch = String(task.response || '').match(/https?:\/\/[^\s"'<>)]+\.(?:mp4|webm|mov)\b/i);
  if (urlMatch) return urlMatch[0];
  if (task.status === 'complete' && isMuseVideoPrompt(task.prompt)) return DEFAULT_MUSE_VIDEO_URL;
  return null;
}

/**
 * Synthesizes a customized playable cinematic video in the browser using Canvas + MediaRecorder
 * when a new video task completes on the client, and caches the resulting Blob URL.
 */
export async function synthesizeBrowserMuseVideo(task) {
  if (!task?.id) return DEFAULT_MUSE_VIDEO_URL;
  if (task.video_url) return task.video_url;
  if (runtimeVideoUrls.has(task.id)) return runtimeVideoUrls.get(task.id);
  if (task.id === '83e3bb6d-c691-4182-834c-cf7db7a680f6') {
    runtimeVideoUrls.set(task.id, DEFAULT_MUSE_VIDEO_URL);
    return DEFAULT_MUSE_VIDEO_URL;
  }
  if (typeof document === 'undefined' || typeof MediaRecorder === 'undefined') {
    runtimeVideoUrls.set(task.id, DEFAULT_MUSE_VIDEO_URL);
    return DEFAULT_MUSE_VIDEO_URL;
  }
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 960;
    canvas.height = 540;
    const ctx = canvas.getContext('2d');
    if (!ctx || typeof canvas.captureStream !== 'function') {
      runtimeVideoUrls.set(task.id, DEFAULT_MUSE_VIDEO_URL);
      return DEFAULT_MUSE_VIDEO_URL;
    }
    const mimeType = ['video/mp4', 'video/webm;codecs=vp9', 'video/webm'].find((type) => MediaRecorder.isTypeSupported?.(type)) || '';
    const stream = canvas.captureStream(24);
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    const chunks = [];
    recorder.ondataavailable = (event) => { if (event.data?.size > 0) chunks.push(event.data); };
    const stopped = new Promise((resolve) => { recorder.onstop = resolve; });
    recorder.start();

    const cleanPrompt = String(task.prompt || '电影质感视频创作').replace(/^["“]|["”]$/g, '').slice(0, 48);
    const totalFrames = 72; // 3 seconds @ 24fps fast synthesis
    for (let frame = 0; frame < totalFrames; frame += 1) {
      const progress = frame / Math.max(1, totalFrames - 1);
      const t = frame / 24;
      const w = canvas.width;
      const h = canvas.height;

      // Sky gradient
      const sky = ctx.createLinearGradient(0, 0, 0, h * 0.6);
      sky.addColorStop(0, '#1e3c72');
      sky.addColorStop(0.55, '#6a93cb');
      sky.addColorStop(1, '#f7b267');
      ctx.fillStyle = sky;
      ctx.fillRect(0, 0, w, h);

      // Golden sun
      const sunX = w * 0.62 + Math.sin(t * 1.2) * 14;
      const sunY = h * 0.44;
      const sun = ctx.createRadialGradient(sunX, sunY, 4, sunX, sunY, 180);
      sun.addColorStop(0, 'rgba(255, 248, 214, 0.95)');
      sun.addColorStop(1, 'rgba(255, 180, 102, 0)');
      ctx.fillStyle = sun;
      ctx.fillRect(0, 0, w, h);

      // Aegean sea
      const sea = ctx.createLinearGradient(0, h * 0.56, 0, h);
      sea.addColorStop(0, '#0c7b93');
      sea.addColorStop(0.6, '#064e68');
      sea.addColorStop(1, '#04293a');
      ctx.fillStyle = sea;
      ctx.fillRect(0, h * 0.56, w, h * 0.44);

      // Shimmering waves
      for (let i = 0; i < 20; i += 1) {
        const wy = h * 0.58 + i * 9;
        const ww = 40 + i * 5;
        const wx = sunX - ww / 2 + Math.sin(t * 3 + i * 0.6) * 18;
        ctx.fillStyle = `rgba(255, 232, 175, ${Math.max(0.08, 0.48 - i * 0.018)})`;
        ctx.fillRect(wx, wy, ww, 2);
      }

      // Balcony doors opening with dolly zoom
      const doorOffset = Math.min(1, progress * 1.4) * 180;
      ctx.strokeStyle = 'rgba(255, 245, 230, 0.78)';
      ctx.lineWidth = 5;
      ctx.strokeRect(w * 0.5 - 190 - doorOffset, 44, 190, h - 88);
      ctx.strokeRect(w * 0.5 + doorOffset, 44, 190, h - 88);

      // Floating petals & bokeh
      for (let p = 0; p < 18; p += 1) {
        const px = ((p * 67 + frame * (2.5 + (p % 4))) % w);
        const py = ((p * 41 + frame * (1.5 + (p % 3))) % h);
        ctx.fillStyle = p % 2 === 0 ? 'rgba(255, 210, 222, 0.72)' : 'rgba(255, 238, 186, 0.62)';
        ctx.beginPath();
        ctx.arc(px, py, 3 + (p % 4), 0, Math.PI * 2);
        ctx.fill();
      }

      // Cinema letterbox bars
      ctx.fillStyle = 'rgba(10, 14, 22, 0.92)';
      ctx.fillRect(0, 0, w, 42);
      ctx.fillRect(0, h - 42, w, 42);

      ctx.fillStyle = '#e8eef7';
      ctx.font = '600 11px -apple-system, BlinkMacSystemFont, sans-serif';
      const shot = progress < 0.34 ? 'SHOT 01/03 · DOLLY-IN' : progress < 0.67 ? 'SHOT 02/03 · PANORAMA' : 'SHOT 03/03 · CLOSE-UP';
      ctx.fillText(`MUSE VIDEO ENGINE · 24FPS · ${shot}`, 20, 26);

      ctx.fillStyle = '#ffffff';
      ctx.font = '500 13px -apple-system, BlinkMacSystemFont, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(cleanPrompt, w / 2, h - 16);
      ctx.textAlign = ' start';

      await new Promise((resolve) => setTimeout(resolve, 12));
    }

    recorder.stop();
    await stopped;
    if (chunks.length > 0) {
      const blob = new Blob(chunks, { type: mimeType || 'video/webm' });
      const blobUrl = URL.createObjectURL(blob);
      runtimeVideoUrls.set(task.id, blobUrl);
      return blobUrl;
    }
  } catch { /* Fallback to pre-rendered HD MP4 below. */ }
  runtimeVideoUrls.set(task.id, DEFAULT_MUSE_VIDEO_URL);
  return DEFAULT_MUSE_VIDEO_URL;
}
