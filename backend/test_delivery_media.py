import hashlib
import io
import ipaddress
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
import uuid
import zipfile

import httpx
from fastapi import FastAPI, HTTPException
from PIL import Image

from backend import delivery_media as media
from backend import super_platform, workbench


def image_bytes(color="blue"):
    output = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(output, format="PNG")
    return output.getvalue()


class DeliveryMediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.store = media.DeliveryMediaStore(self.root / "media")
        self.job = {"id": str(uuid.uuid4()), "project_id": str(uuid.uuid4()), "status": "complete",
                    "result": {"delivery": {"title": "图文成果", "content": "真正的正文", "status": "complete", "media": []}}}
        self.manager = workbench.JobManager(self.root / "jobs")
        self.manager.jobs[self.job["id"]] = self.job
        self.app = FastAPI()
        self.app.include_router(media.router)
        self.raw = image_bytes()

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, **kwargs):
        return self.store.save_bytes(self.job, self.raw, kind="image", provider="fixture", model="image-model", **kwargs)

    def asset_path(self, entry):
        return self.store.directory / self.job["id"] / entry["id"]

    def test_real_image_hash_scope_permissions_and_restart(self):
        entry = self.save(slot_id="cover", caption="说明", source_task_id="actual-source")
        restarted = media.DeliveryMediaStore(self.store.directory)
        restored, actual = restarted.read(self.job, entry["id"])
        self.assertEqual(actual, self.raw)
        self.assertEqual(restored, entry)
        self.assertEqual(entry["sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(entry["mime_type"], "image/png")
        self.assertTrue(entry["download_url"].endswith("&download=1"))
        self.assertNotIn("http", entry["url"])
        for directory in [self.store.directory, self.store.directory / self.job["id"], self.asset_path(entry)]:
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for path in self.asset_path(entry).iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_forged_image_mismatched_mime_and_size_bounds_are_rejected(self):
        for raw in [b"", b"<html><img src='result.png'></html>", b"https://example.com/image.png", b"\x89PNG\r\n\x1a\ninvalid"]:
            with self.assertRaises(HTTPException):
                self.store.save_bytes(self.job, raw, kind="image", provider="fixture")
        with self.assertRaises(HTTPException):
            self.save(mime_type="image/jpeg")
        with patch.object(media, "MAX_IMAGE_BYTES", 2):
            with self.assertRaises(HTTPException):
                self.save()
        self.assertFalse(self.store.directory.exists())

    def test_content_and_manifest_tampering_fail_integrity(self):
        entry = self.save()
        target = self.asset_path(entry) / entry["name"]
        target.write_bytes(image_bytes("red"))
        with self.assertRaises(HTTPException) as caught:
            self.store.read(self.job, entry["id"])
        self.assertEqual(caught.exception.status_code, 409)
        target.write_bytes(self.raw)
        metadata = self.asset_path(entry) / "manifest.json"
        altered = dict(entry, project_id=str(uuid.uuid4()))
        metadata.write_text(json.dumps(altered))
        with self.assertRaises(HTTPException):
            self.store.read(self.job, entry["id"])

    def test_symlink_files_asset_job_and_ancestor_are_rejected(self):
        entry = self.save()
        original = self.asset_path(entry) / entry["name"]
        outside = self.root / "outside.png"
        outside.write_bytes(self.raw)
        original.unlink()
        original.symlink_to(outside)
        with self.assertRaises(HTTPException):
            self.store.read(self.job, entry["id"])
        original.unlink()
        original.write_bytes(self.raw)
        metadata = self.asset_path(entry) / "manifest.json"
        metadata_content = metadata.read_bytes()
        metadata.unlink()
        outside_json = self.root / "outside.json"
        outside_json.write_bytes(metadata_content)
        metadata.symlink_to(outside_json)
        with self.assertRaises(HTTPException):
            self.store.read(self.job, entry["id"])
        linked = self.root / "linked"
        linked.symlink_to(self.store.directory, target_is_directory=True)
        with self.assertRaises(HTTPException):
            media.DeliveryMediaStore(linked).save_bytes(self.job, self.raw, kind="image", provider="fixture")
        with self.assertRaises(HTTPException):
            media.DeliveryMediaStore(linked / "nested").save_bytes(self.job, self.raw, kind="image", provider="fixture")
        self.assertFalse((self.store.directory / "nested").exists())
        asset = self.asset_path(entry)
        moved = self.root / "moved"
        asset.rename(moved)
        asset.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(HTTPException):
            self.store.read(self.job, entry["id"])
        asset.unlink()
        moved.rename(asset)
        job_path = asset.parent
        moved_job = self.root / "moved-job"
        job_path.rename(moved_job)
        job_path.symlink_to(moved_job, target_is_directory=True)
        with self.assertRaises(HTTPException):
            self.store.read(self.job, entry["id"])

    def test_hardlinks_and_traversal_never_escape_store(self):
        entry = self.save()
        import os
        os.link(self.asset_path(entry) / entry["name"], self.root / "duplicate.png")
        with self.assertRaises(HTTPException):
            self.store.read(self.job, entry["id"])
        for identifier in ["../outside", "../../", "/tmp", entry["id"].upper()]:
            with self.assertRaises(HTTPException):
                self.store.read(self.job, identifier)

    def test_total_resource_and_byte_limits_are_durable(self):
        self.save()
        with patch.object(media, "MAX_JOB_ASSETS", 1):
            with self.assertRaises(HTTPException):
                self.save()
        with patch.object(media, "MAX_JOB_BYTES", len(self.raw) * 2 - 1):
            with self.assertRaises(HTTPException):
                self.save()
        self.assertEqual(len(list((self.store.directory / self.job["id"]).iterdir())), 1)

    def test_failed_atomic_write_does_not_leave_counted_partial_asset(self):
        original = media._atomic_write
        def interrupted(descriptor, name, raw):
            if name == "manifest.json":
                raise OSError("fixture disk failure")
            return original(descriptor, name, raw)
        with patch.object(media, "_atomic_write", side_effect=interrupted):
            with self.assertRaises(HTTPException):
                self.save()
        self.assertEqual(list((self.store.directory / self.job["id"]).iterdir()), [])
        self.save()

    def test_video_rejects_html_audio_or_unprobed_header_and_bounds_probe(self):
        for raw in [b"<html>video.mp4</html>", b"video.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64]:
            with patch.object(media.shutil, "which", return_value=None):
                with self.assertRaises(HTTPException):
                    self.store.save_bytes(self.job, raw, kind="video", provider="fixture")
        raw = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 12 + b"mdat" + b"data" * 10 + b"moov"
        valid = subprocess.CompletedProcess([], 0, json.dumps({"streams": [{"codec_type": "video", "width": 16, "height": 16}],
                                                                    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"}}).encode(), b"")
        with patch.object(media.shutil, "which", return_value="/fixture/ffprobe"), patch.object(media.subprocess, "run", return_value=valid) as probe:
            entry = self.store.save_bytes(self.job, raw, kind="video", provider="fixture")
        self.assertEqual(entry["mime_type"], "video/mp4")
        self.assertEqual(probe.call_args.kwargs["timeout"], 15)
        arguments = probe.call_args.args[0]
        self.assertEqual(arguments[arguments.index("-protocol_whitelist") + 1], "pipe")
        invalid = subprocess.CompletedProcess([], 0, b'{"streams": [{"codec_type":"audio"}]}', b"")
        with patch.object(media.shutil, "which", return_value="/fixture/ffprobe"), patch.object(media.subprocess, "run", return_value=invalid):
            with self.assertRaises(HTTPException):
                self.store.save_bytes(self.job, raw, kind="video", provider="fixture")

    async def test_http_requires_known_job_explicit_project_scope_and_verified_bytes(self):
        entry = self.save()
        with patch.object(media, "store", self.store), patch.object(workbench, "manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                path = f'/api/media/jobs/{self.job["id"]}/assets/{entry["id"]}/content'
                self.assertEqual((await client.get(path)).status_code, 404)
                self.assertEqual((await client.get(path, params={"project_id": str(uuid.uuid4())})).status_code, 404)
                self.assertEqual((await client.get(path.replace(self.job["id"], str(uuid.uuid4())), params={"project_id": self.job["project_id"]})).status_code, 404)
                response = await client.get(entry["url"])
                self.assertEqual(response.content, self.raw)
                self.assertEqual(response.headers["content-type"], "image/png")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                self.assertIn("attachment;", (await client.get(entry["download_url"])).headers["content-disposition"])
                (self.asset_path(entry) / entry["name"]).write_bytes(b"not image")
                self.assertEqual((await client.get(entry["url"])).status_code, 409)

    async def test_bundle_contains_actual_canonical_assets_portable_markdown_and_safe_offline_html(self):
        entry = self.save(caption='<img src=x onerror="alert(1)">')
        extra = self.save(caption="not part of final delivery")
        delivery = self.job["result"]["delivery"]
        delivery.update(title="<script>alert(1)</script>", content=f'# 正文\n![封面]({entry["url"]})\n<script>alert(2)</script>\n![外部](https://bad.example/track.png)', media=[entry])
        with patch.object(media, "store", self.store), patch.object(workbench, "manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                self.assertEqual((await client.get(f'/api/media/jobs/{self.job["id"]}/bundle')).status_code, 404)
                response = await client.get(f'/api/media/jobs/{self.job["id"]}/bundle', params={"project_id": self.job["project_id"]})
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {"index.html", "article.md", "manifest.json", "media/" + entry["name"]})
            self.assertEqual(archive.read("media/" + entry["name"]), self.raw)
            article = archive.read("article.md").decode()
            self.assertIn("media/" + entry["name"], article)
            self.assertNotIn("/api/media/", article)
            document = archive.read("index.html").decode()
            self.assertNotIn("<script>", document)
            self.assertNotIn('src="https://', document)
            self.assertIn("&lt;script&gt;", document)
            self.assertIn("Content-Security-Policy", document)
            self.assertIn('<img src="media/', document)
            self.assertNotIn("![封面]", document)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["media"][0]["sha256"], entry["sha256"])
            self.assertNotIn(extra["id"], str(manifest))

    async def test_scoped_media_ranges_for_phone_playback_retain_integrity_and_etag(self):
        entry = self.save()
        with patch.object(media, "store", self.store), patch.object(workbench, "manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                for value, expected in [("bytes=0-1", self.raw[:2]), ("bytes=3-", self.raw[3:]), ("bytes=-5", self.raw[-5:]), ("bytes=0-999999", self.raw)]:
                    response = await client.get(entry["url"], headers={"Range": value})
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(response.content, expected)
                    self.assertIn("bytes ", response.headers["content-range"])
                    self.assertEqual(response.headers["etag"], '"' + entry["sha256"] + '"')
                for value in ["bytes=999999-", "bytes=-0", "bytes=2-1", "bytes=0-1,4-5", "bytes=-", "bytes=abc"]:
                    response = await client.get(entry["url"], headers={"Range": value})
                    self.assertEqual(response.status_code, 416, value)
                    self.assertEqual(response.headers["content-range"], f"bytes */{len(self.raw)}")
                response = await client.get(entry["url"], headers={"Range": "bytes=0-1", "If-Range": '"old-hash"'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, self.raw)
                self.assertEqual((await client.get(entry["url"].split("?")[0], headers={"Range": "bytes=0-1"})).status_code, 404)

    async def test_export_is_one_frozen_delivery_while_job_finishes_during_file_read(self):
        entry = self.save()
        delivery = self.job["result"]["delivery"]
        delivery.update(content="旧的部分正文", status="working", media=[entry])
        entered, release = threading.Event(), threading.Event()
        original = self.store.read
        def paused(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test synchronization timed out")
            return original(*args)
        with patch.object(media, "store", self.store), patch.object(workbench, "manager", self.manager), patch.object(self.store, "read", side_effect=paused):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                pending = asyncio.create_task(client.get(f'/api/media/jobs/{self.job["id"]}/bundle', params={"project_id": self.job["project_id"]}))
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                delivery.update(content="最终完成正文", status="complete")
                release.set()
                response = await pending
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.read("article.md").decode(), "旧的部分正文")
            self.assertEqual(json.loads(archive.read("manifest.json"))["status"], "working")

    def test_bundle_rejects_cross_job_tampered_or_duplicate_canonical_entries(self):
        entry = self.save()
        for entries in [[dict(entry, job_id=str(uuid.uuid4()))], [dict(entry, sha256="0" * 64)], [entry, entry]]:
            self.job["result"]["delivery"]["media"] = entries
            with patch.object(media, "store", self.store):
                with self.assertRaises(HTTPException):
                    media.delivery_bundle(self.job)
        other_job = dict(self.job, id=str(uuid.uuid4()))
        foreign = self.store.save_bytes(other_job, self.raw, kind="image", provider="fixture")
        self.job["result"]["delivery"]["media"] = [foreign]
        with patch.object(media, "store", self.store):
            with self.assertRaises(HTTPException):
                media.delivery_bundle(self.job)

    async def test_import_pins_public_dns_host_sni_no_environment_or_credentials(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, content=self.raw, headers={"content-type": "image/png"})
        real_client = httpx.AsyncClient
        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            self.assertNotEqual(kwargs.get("verify"), False)
            return real_client(transport=httpx.MockTransport(handle), **kwargs)
        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch.object(super_platform.socket, "getaddrinfo", return_value=public), patch.object(media.httpx, "AsyncClient", side_effect=client):
            entry = await self.store.import_url(self.job, "https://provider.example/render?temporary=signed-secret", kind="image", provider="fixture")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "93.184.216.34")
        self.assertEqual(requests[0].headers["host"], "provider.example")
        self.assertEqual(requests[0].extensions["sni_hostname"], "provider.example")
        self.assertNotIn("authorization", requests[0].headers)
        self.assertNotIn("cookie", requests[0].headers)
        self.assertNotIn("signed-secret", json.dumps(entry))
        self.assertEqual(self.store.read(self.job, entry["id"])[1], self.raw)

    async def test_independent_job_has_explicit_none_scope_and_auto_image_sniffing(self):
        self.job["project_id"] = None
        entry = self.store.save_bytes(self.job, self.raw, kind="auto", provider="muse")
        self.assertEqual(entry["kind"], "image")
        self.assertIsNone(entry["project_id"])
        self.assertNotIn("?", entry["url"])
        self.assertEqual(entry["download_url"], entry["url"] + "?download=1")
        self.job["result"]["delivery"]["media"] = [entry]
        with patch.object(media, "store", self.store), patch.object(workbench, "manager", self.manager):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                self.assertEqual((await client.get(entry["url"])).content, self.raw)
                self.assertEqual((await client.get(entry["url"], params={"project_id": str(uuid.uuid4())})).status_code, 404)
                self.assertEqual((await client.get(f'/api/media/jobs/{self.job["id"]}/bundle')).status_code, 200)
        with self.assertRaises(HTTPException):
            self.store.save_bytes(self.job, b"not a real media file", kind="auto", provider="muse")

    async def test_capabilities_handler_returns_current_non_secret_provider_candidates(self):
        from backend import media_providers
        candidates = [{"id": "gemini-image", "provider": "gemini", "available": True, "verified": False}]
        lookup = AsyncMock(return_value=candidates)
        with patch.object(media_providers, "capabilities", lookup):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                response = await client.get("/api/media/capabilities")
        self.assertEqual(response.json(), candidates)
        lookup.assert_awaited_once()

    async def test_import_rejects_private_dns_credential_urls_and_every_unsafe_redirect(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
        real_client = httpx.AsyncClient
        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch.object(super_platform.socket, "getaddrinfo", return_value=public), patch.object(media.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs)):
            with self.assertRaises(HTTPException):
                await self.store.import_url(self.job, "https://provider.example/result", kind="image", provider="fixture")
        self.assertEqual(len(requests), 1)
        for url in ["https://user:secret@provider.example/image", "http://provider.example/image", "https://localhost/image", "https://provider.example:80/image"]:
            with patch.object(super_platform.socket, "getaddrinfo", return_value=public):
                with self.assertRaises(HTTPException):
                    await self.store.import_url(self.job, url, kind="image", provider="fixture")
        private = [(2, 1, 6, "", ("192.168.1.10", 443))]
        with patch.object(super_platform.socket, "getaddrinfo", return_value=private), patch.object(media.httpx, "AsyncClient") as client:
            with self.assertRaises(HTTPException):
                await self.store.import_url(self.job, "https://provider.example/result", kind="image", provider="fixture")
        client.assert_not_called()

    async def test_import_revalidates_each_redirect_domain_and_stream_size(self):
        seen = []
        def handle(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(302, headers={"location": "https://next.example/image"})
            return httpx.Response(200, content=self.raw)
        lookup = AsyncMock(side_effect=[(urlsplit_fixture("https://provider.example/result"), "provider.example", [ipaddress.ip_address("93.184.216.34")]),
                                      (urlsplit_fixture("https://next.example/image"), "next.example", [ipaddress.ip_address("1.1.1.1")])])
        real_client = httpx.AsyncClient
        with patch.object(super_platform, "public_target", lookup), patch.object(media.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs)):
            await self.store.import_url(self.job, "https://provider.example/result", kind="image", provider="fixture")
        self.assertEqual(lookup.await_count, 2)
        self.assertEqual([request.url.host for request in seen], ["93.184.216.34", "1.1.1.1"])
        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        for headers in [{}, {"content-length": "999999999"}]:
            with patch.object(media, "MAX_IMAGE_BYTES", len(self.raw) - 1), patch.object(super_platform.socket, "getaddrinfo", return_value=public), patch.object(media.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=self.raw, headers=headers)), **kwargs)):
                with self.assertRaises(HTTPException):
                    await self.store.import_url(self.job, "https://provider.example/result", kind="image", provider="fixture")


def urlsplit_fixture(value):
    from urllib.parse import urlsplit
    return urlsplit(value)


if __name__ == "__main__":
    unittest.main()
