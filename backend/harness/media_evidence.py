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

MAX_PARTS = 12
MAX_ASSETS = 8


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


def _video(raw):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('视频抽帧工具未就绪，尚未检查画面')
    with tempfile.TemporaryDirectory(prefix='modeldesk-review-') as folder:
        source = Path(folder) / 'media'
        source.write_bytes(raw)
        policy = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm']
        probe = subprocess.run([ffprobe, '-v', 'error', *policy, '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height:format=duration', '-of', 'json', str(source)],
            capture_output=True, timeout=10, check=True)
        data = json.loads(probe.stdout)
        duration = float(data['format']['duration'])
        stream = data['streams'][0]
        width, height = int(stream['width']), int(stream['height'])
        if not math.isfinite(duration) or not 0 < duration <= 3600 or not 0 < width * height <= 40_000_000:
            raise ValueError('视频超出画面检查范围')
        frames = []
        for fraction in (.1, .5, .9):
            timestamp = round(duration * fraction, 3)
            frame = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-threads', '1',
                *policy, '-ss', str(timestamp), '-i', str(source), '-map', '0:v:0', '-frames:v', '1',
                '-vf', 'scale=768:768:force_original_aspect_ratio=decrease', '-threads', '1',
                '-f', 'image2pipe', '-c:v', 'mjpeg', 'pipe:1'], capture_output=True, timeout=15, check=True)
            image, _ = _image(frame.stdout)
            frames.append((timestamp, image))
    return {'duration_seconds': round(duration, 3), 'width': width, 'height': height}, frames


def inspect_media(job, assets):
    from ..delivery_media import store
    records, parts = [], []
    for asset in assets[:MAX_ASSETS]:
        record = {'asset_id': asset['id'], 'kind': asset['kind'], 'status': 'unavailable', 'samples': []}
        records.append(record)
        needed = 3 if asset['kind'] == 'video' else 1
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
                metadata, frames = _video(raw)
                record.update(metadata)
            for timestamp, image in frames:
                record['samples'].append({'input_index': len(parts) + 1, 'timestamp_seconds': timestamp,
                                          'sha256': hashlib.sha256(image).hexdigest()})
                parts.append({'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(image).decode()}})
            record['status'] = 'sampled'
        except Exception:
            record['reason'] = '未取得可信的画面输入；此文件的视觉要求仍需核查'
    return {'scope': '图片缩略图与视频三处抽帧；不包含音轨、连续动作或全片检查',
            'assets': records, 'omitted_assets': max(0, len(assets) - len(records)), 'input_count': len(parts)}, parts


async def media_evidence(job, assets):
    return await asyncio.to_thread(inspect_media, job, assets)
