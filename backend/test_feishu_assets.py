import hashlib
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from backend import delivery_media, document_delivery, feishu_assets
from backend.feishu_sdk import FeishuAPIError
from backend.test_delivery_media import image_bytes


class FeishuArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.media = delivery_media.DeliveryMediaStore(self.root / "media")
        self.documents = document_delivery.DocumentStore(self.root / "documents")
        self.job = {"id": str(uuid.uuid4()), "project_id": str(uuid.uuid4())}
        self.content = "# 完整成果\n\n真实说明。"
        self.raw = image_bytes()
        self.image = self.media.save_bytes(self.job, self.raw, kind="image", provider="fixture")
        html = b"<!doctype html><html><body>Verified report</body></html>"
        self.document = self.documents.save(self.job, "group:delivery", {
            "content": html, "format": "html", "layout": "report", "media_type": "text/html; charset=utf-8",
            "sha256": hashlib.sha256(html).hexdigest(), "source_sha256": hashlib.sha256(self.content.encode()).hexdigest(),
            "partial": False, "warnings": [],
        })
        self.delivery = {"content": self.content, "documents": [self.document], "media": [self.image]}
        self.addCleanup(patch.stopall)
        patch.object(document_delivery, "store", self.documents).start()
        patch.object(delivery_media, "store", self.media).start()

    def test_real_document_and_image_bytes_are_scoped_normalized_and_deduplicated(self):
        self.delivery["media"].append(self.image.copy())
        artifacts = feishu_assets.verified_artifacts(self.job, self.delivery)
        self.assertEqual(len(artifacts), 2)
        self.assertEqual(artifacts[0][0], "document:" + self.document["id"])
        key, metadata, raw = artifacts[1]
        self.assertEqual(key, "media:" + self.image["id"])
        self.assertEqual(metadata["filename"], self.image["name"])
        self.assertEqual(metadata["format"], "stream")
        self.assertEqual(raw, self.raw)
        with self.assertRaises(FeishuAPIError):
            feishu_assets.verified_artifacts({**self.job, "project_id": str(uuid.uuid4())}, self.delivery)

    def test_stale_document_or_corrupt_later_media_prevents_returning_any_batch(self):
        with self.assertRaisesRegex(FeishuAPIError, "正文版本"):
            feishu_assets.verified_artifacts(self.job, {**self.delivery, "content": "new unreviewed body"})
        path = self.media.directory / self.job["id"] / self.image["id"] / self.image["name"]
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(FeishuAPIError, "完整性"):
            feishu_assets.verified_artifacts(self.job, self.delivery)

    def test_transport_limit_is_checked_before_returning_any_batch(self):
        with patch.object(feishu_assets, "MAX_FILE_BYTES", 16):
            with self.assertRaisesRegex(FeishuAPIError, "20 MB"):
                feishu_assets.verified_artifacts(self.job, self.delivery)
        self.assertEqual(self.media.read(self.job, self.image["id"])[1], self.raw)

