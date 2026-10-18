import base64
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from PIL import Image
from backend import delivery_media
from backend.harness.media_evidence import inspect_media


class MediaEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = delivery_media.DeliveryMediaStore(Path(self.tmp.name).resolve() / 'assets')
        self.job = {'id': str(uuid.uuid4()), 'project_id': str(uuid.uuid4())}

    def test_images_are_hash_checked_bounded_and_pixels_stay_out_of_record(self):
        raw = io.BytesIO()
        Image.new('RGB', (1600, 800), 'blue').save(raw, 'PNG')
        asset = self.store.save_bytes(self.job, raw.getvalue(), kind='image', provider='fixture')
        with patch.object(delivery_media, 'store', self.store):
            report, parts = inspect_media(self.job, [asset])
            wrong, empty = inspect_media(self.job, [dict(asset, sha256='0' * 64)])
            foreign, foreign_parts = inspect_media(dict(self.job, project_id=str(uuid.uuid4())), [asset])
        self.assertEqual(report['input_count'], 1)
        self.assertEqual(report['assets'][0]['width'], 1600)
        self.assertNotIn('base64', json.dumps(report))
        pixels = base64.b64decode(parts[0]['image_url']['url'].split(',')[1])
        with Image.open(io.BytesIO(pixels)) as image:
            self.assertEqual(image.size, (1024, 512))
        self.assertFalse(empty or foreign_parts)
        self.assertEqual(wrong['assets'][0]['status'], 'unavailable')
        self.assertEqual(foreign['assets'][0]['status'], 'unavailable')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg is optional')
    def test_video_duration_and_three_real_frame_inputs(self):
        path = Path(self.tmp.name) / 'fixture.mp4'
        subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=160x120:r=10:d=2', '-c:v', 'libx264', str(path)], check=True)
        asset = self.store.save_bytes(self.job, path.read_bytes(), kind='video', provider='fixture')
        with patch.object(delivery_media, 'store', self.store):
            report, parts = inspect_media(self.job, [asset])
        self.assertEqual(len(parts), 3, report)
        record = report['assets'][0]
        self.assertEqual(record['duration_seconds'], 2)
        self.assertEqual([sample['timestamp_seconds'] for sample in record['samples']], [.2, 1, 1.8])
        self.assertIn('不包含音轨', report['scope'])


if __name__ == '__main__':
    unittest.main()
