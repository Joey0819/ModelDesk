"""Bounded visual evidence from owned, hash-verified deliverables.

Pixels are ephemeral model inputs. Only their provenance, timestamps and
hashes enter run records. Video sampling never claims audio/full-film review.
"""
import asyncio
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from fractions import Fraction

MAX_PARTS = 12
MAX_ASSETS = 12
VIDEO_SAMPLES = 12


def _motion(ffmpeg, source, policy, duration):
    """Decode bounded real frames; numerical change is not semantic approval."""
    frame_size, max_frames = 64 * 36, 36000
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-nostdin', '-threads', '1', *policy,
        '-i', str(source), '-map', '0:v:0', '-an', '-vf', 'scale=64:36,format=gray',
        '-fps_mode', 'passthrough', '-frames:v', str(max_frames + 1), '-threads', '1',
        '-f', 'rawvideo', 'pipe:1'], capture_output=True, timeout=45, check=True)
    pixels = decoded.stdout
    count = len(pixels) // frame_size
    if len(pixels) % frame_size or count < 2 or count > max_frames:
        return {'status': 'limited', 'reason': '逐帧解码超过检查预算或帧结构不完整'}
    changes, bins = [], []
    for index in range(1, count):
        previous = pixels[(index - 1) * frame_size:index * frame_size]
        current = pixels[index * frame_size:(index + 1) * frame_size]
        changes.append(sum(abs(a - b) for a, b in zip(previous, current)) / frame_size)
    size = max(1, math.ceil(len(changes) / 12))
    for start in range(0, len(changes), size):
        values = changes[start:start + size]
        bins.append({'start_seconds': round(duration * start / len(changes), 3),
                     'end_seconds': round(duration * min(start + size, len(changes)) / len(changes), 3),
                     'mean_pixel_change': round(sum(values) / len(values), 4),
                     'changed_frame_fraction': round(sum(v > .1 for v in values) / len(values), 4)})
    return {'status': 'decoded', 'decoded_frames': count, 'full_video_decoded': True,
            'comparison_resolution': [64, 36], 'change_threshold_gray_levels': .1,
            'changed_frame_fraction': round(sum(v > .1 for v in changes) / len(changes), 4),
            'intervals': bins, 'scope': '全部视频帧解码及低分辨率像素变化统计；不证明故事语义、身份一致性或音画同步'}


def _image(raw):
    from PIL import Image
    from ..delivery_media import _validate_image
    _validate_image(raw)
    with Image.open(io.BytesIO(raw)) as source:
        dimensions = list(source.size)
        source.thumbnail((1024, 1024))
        output = io.BytesIO()
        source.convert('RGB').save(output, format='JPEG', quality=85)
    return output.getvalue(), dimensions


def _video(raw, sample_count=VIDEO_SAMPLES):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('视频抽帧工具未就绪，尚未检查画面')
    with tempfile.TemporaryDirectory(prefix='modeldesk-review-') as folder:
        source = Path(folder) / 'media'
        source.write_bytes(raw)
        policy = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm']
        probe = subprocess.run([ffprobe, '-v', 'error', *policy,
            '-show_entries', 'stream=codec_type,width,height,avg_frame_rate:format=duration', '-of', 'json', str(source)],
            capture_output=True, timeout=10, check=True)
        data = json.loads(probe.stdout)
        duration = float(data['format']['duration'])
        stream = next(item for item in data['streams'] if item.get('codec_type') == 'video')
        audio_streams = sum(item.get('codec_type') == 'audio' for item in data['streams'])
        width, height = int(stream['width']), int(stream['height'])
        if not math.isfinite(duration) or not 0 < duration <= 3600 or not 0 < width * height <= 40_000_000:
            raise ValueError('视频超出画面检查范围')
        frames = []
        try:
            rate = float(Fraction(stream.get('avg_frame_rate') or '0'))
        except (ValueError, ZeroDivisionError):
            rate = 0
        last_frame = max(0, duration - (1 / rate if rate > 0 else .2))
        for fraction in ((index + .5) / sample_count for index in range(sample_count)):
            timestamp = round(min(duration * fraction, last_frame), 3)
            frame = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-threads', '1',
                *policy, '-ss', str(timestamp), '-i', str(source), '-map', '0:v:0', '-frames:v', '1',
                '-vf', 'scale=768:768:force_original_aspect_ratio=decrease', '-threads', '1',
                '-f', 'image2pipe', '-c:v', 'mjpeg', 'pipe:1'], capture_output=True, timeout=15, check=True)
            image, _ = _image(frame.stdout)
            frames.append((timestamp, image))
        try:
            motion = _motion(ffmpeg, source, policy, duration)
        except (subprocess.SubprocessError, OSError, ValueError):
            motion = {'status': 'unavailable', 'reason': '完整解码或逐帧变化检查未完成；不能据此确认连续动作'}
    return {'duration_seconds': round(duration, 3), 'width': width, 'height': height, 'audio_streams': audio_streams,
            'motion_evidence': motion}, frames


def inspect_media(job, assets):
    from ..delivery_media import store
    records, parts = [], []
    for index, asset in enumerate(assets[:MAX_ASSETS]):
        record = {'asset_id': asset['id'], 'kind': asset['kind'], 'status': 'unavailable', 'samples': []}
        records.append(record)
        remaining_cost = sum(3 if item['kind'] == 'video' else 1 for item in assets[index + 1:MAX_ASSETS])
        needed = min(VIDEO_SAMPLES, max(3, MAX_PARTS - len(parts) - remaining_cost)) if asset['kind'] == 'video' else 1
        if len(parts) + needed > MAX_PARTS:
            record['reason'] = '已到本轮画面检查数量上限'
            continue
        try:
            manifest, raw = store.read(job, asset['id'])
            if manifest['sha256'] != asset['sha256'] or manifest['kind'] != asset['kind']:
                raise ValueError('交付文件与清单不一致')
            record['sha256'] = manifest['sha256']
            if asset['kind'] == 'image':
                image, dimensions = _image(raw)
                record.update(width=dimensions[0], height=dimensions[1])
                frames = [(None, image)]
            else:
                metadata, frames = _video(raw, needed)
                record.update(metadata)
            for timestamp, image in frames:
                record['samples'].append({'input_index': len(parts) + 1, 'timestamp_seconds': timestamp,
                                          'sha256': hashlib.sha256(image).hexdigest()})
                parts.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(image).decode()}})
            record['status'] = 'sampled'
        except Exception:
            record['reason'] = '未取得可信的画面输入；此文件的视觉要求仍需核查'
    return {'scope': '图片缩略图与每视频最多十二处抽帧；逐帧解码及运动统计见各文件证据，不等于全片语义审查；不包含音轨内容或音画同步检查',
            'assets': records, 'omitted_assets': max(0, len(assets) - len(records)), 'input_count': len(parts)}, parts


async def media_evidence(job, assets):
    return await asyncio.to_thread(inspect_media, job, assets)
