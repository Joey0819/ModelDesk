"""Image routes and byte evidence, without real accounts or provider keys."""
import base64
import asyncio
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

import httpx

from backend import media_providers as media
from backend import model_connections
from backend.delivery_media import DeliveryMediaStore

RAW = b"\x89PNG\r\n\x1a\nfixture-byte-validation-is-the-storage-responsibility"
B64 = base64.b64encode(RAW).decode()
DATA_URI = "data:image/png;base64," + B64
KEY = "secret-test-provider-key"


def candidate(model="gemini-fixture-image", provider="gemini", **changes):
    return {"id": model, "provider": provider, "model": model, "kind": "image",
            "protocol": "chat_completions_images" if provider == "gemini" else "images_generations",
            "available": True, "verified": False, **changes}


class ImageExtractionTests(unittest.TestCase):
    def test_gemini_proxy_images_and_typed_chat_content(self):
        for message in [{"images": [{"type": "image_url", "image_url": {"url": DATA_URI}}]},
                        {"content": [{"type": "image_url", "image_url": {"url": DATA_URI}}]}]:
            self.assertEqual(media.extract_images({"choices": [{"message": message}]}),
                             [{"bytes": RAW, "mime_type": "image/png"}])

    def test_openai_images_and_responses_tool_result(self):
        for payload in [{"data": [{"b64_json": B64}]},
                        {"output": [{"type": "image_generation_call", "result": B64}]}]:
            self.assertEqual(media.extract_images(payload), [{"bytes": RAW, "mime_type": None}])

    def test_native_gemini_camelcase_and_snakecase(self):
        for inline in [{"inlineData": {"mimeType": "image/png", "data": B64}},
                       {"inline_data": {"mime_type": "image/png", "data": B64}}]:
            payload = {"candidates": [{"content": {"parts": [{"text": "Here is your image"}, inline]}}]}
            self.assertEqual(media.extract_images(payload), [{"bytes": RAW, "mime_type": "image/png"}])

    def test_typed_url_is_not_a_saved_image_until_imported(self):
        self.assertEqual(media.extract_images({"data": [{"url": "https://cdn.example/image.png"}]}),
                         [{"url": "https://cdn.example/image.png", "mime_type": None}])

    def test_prose_markdown_and_tool_arguments_never_count_as_images(self):
        for payload in [{"choices": [{"message": {"content": "![Generated](https://example.com/a.png)"}}]},
                        {"output": [{"type": "function_call", "arguments": B64}]},
                        {"candidates": [{"content": {"parts": [{"text": DATA_URI}]}}]},
                        {"error": {"message": KEY}}]:
            with self.assertRaises(media.MediaProviderError) as failure:
                media.extract_images(payload)
            self.assertNotIn(KEY, str(failure.exception))

    def test_bounded_base64_strict_encoding_and_mime(self):
        for value in ["!not-base64", "", None]:
            with self.assertRaises(media.MediaProviderError):
                media._decode(value)
        with patch.object(media, "MAX_IMAGE_BYTES", 4):
            with self.assertRaises(media.MediaProviderError):
                media._decode(B64)
        with self.assertRaises(media.MediaProviderError):
            media.extract_images({"data": [{"url": "data:text/html;base64," + B64}]})


class ImageProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.connections = model_connections.ConnectionStore(Path(self.temporary.name) / "connections")
        self.patches = [patch.object(model_connections, "store", self.connections),
                        patch.object(media, "EVIDENCE_PATH", Path(self.temporary.name) / "verified.json")]
        for context in self.patches:
            context.start()
        self.manifest = {"id": "saved", "kind": "image", "sha256": "verified-sha", "url": "/api/delivery-media/saved"}
        self.storage = SimpleNamespace(store=SimpleNamespace(save_bytes=Mock(return_value=self.manifest)),
                                       import_url=AsyncMock(return_value=self.manifest))
        self.patches.extend([patch.dict(sys.modules, {"backend.delivery_media": self.storage}),
                             patch("backend.delivery_media", self.storage, create=True)])
        for context in self.patches[2:]:
            context.start()

    async def asyncTearDown(self):
        for context in reversed(self.patches):
            context.stop()
        self.temporary.cleanup()

    def connection(self, model, base="https://api.example/v1", **changes):
        return self.connections.save(model_connections.ConnectionInput(
            name="Fixture image service", provider="openai_compatible", base_url=base,
            models=[{"id": model}], api_key=KEY, **changes))

    def wire(self, handler):
        original = httpx.AsyncClient
        return patch.object(media.httpx, "AsyncClient", side_effect=lambda **kwargs:
                            original(transport=httpx.MockTransport(handler), **kwargs))

    async def test_capabilities_only_image_candidates_and_no_text_test_promotion(self):
        connection = self.connection("gpt-image-fixture")
        self.connections.record_test(connection["id"], {"ok": True},
                                     self.connections.get_private(connection["id"]), "gpt-image-fixture")
        def handler(request):
            self.assertEqual(request.method, "GET")
            return httpx.Response(200, json={"data": [{"id": "gpt-6.1-sol"},
                                                     {"id": "gemini-fixture-image"}, {"id": "gpt-image-fixture"}]})
        with self.wire(handler):
            values = await media.capabilities()
        self.assertEqual(len(values), 3)
        self.assertTrue(all(not item["verified"] and item["status"] == "candidate" for item in values))
        self.assertNotIn(KEY, json.dumps(values))
        self.assertNotIn("gpt-6.1-sol", [item["id"] for item in values])

    async def test_connection_candidates_survive_offline_gateway(self):
        connection = self.connection("gpt-image-fixture")
        def handler(request):
            raise httpx.ConnectError("Offline " + KEY, request=request)
        with self.wire(handler):
            values = await media.capabilities()
        self.assertEqual([item["id"] for item in values], [connection["models"][0]["alias"]])

    async def test_corrupt_optional_receipt_does_not_block_working_image_candidates(self):
        def handler(request):
            return httpx.Response(200, json={"data": [{"id": "gpt-image-fixture"}]})
        for value in [None, "broken", []]:
            with self.subTest(receipt=value), self.wire(handler), patch.object(media, "_evidence", return_value={"gpt-image-fixture": value}):
                entries = await media.capabilities()
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0]["available"])
            self.assertFalse(entries[0]["verified"])

    async def test_openai_images_endpoint_and_private_connection_resolution(self):
        connection = self.connection("gpt-image-fixture")
        entry = candidate("gpt-image-fixture", "openai", id=connection["models"][0]["alias"])
        options = media._resolve(entry, "Draw")
        self.assertEqual(options["url"], "https://api.example/v1/images/generations")
        self.assertEqual(options["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(options["json"], {"model": "gpt-image-fixture", "prompt": "Draw", "n": 1,
                                            "size": "1024x1024", "quality": "low"})
        self.assertNotIn("messages", options["json"])
        self.assertNotIn("modalities", options["json"])

    async def test_native_gemini_uses_native_body_and_key_header(self):
        connection = self.connection("gemini-fixture-image", "https://generativelanguage.googleapis.com/v1beta/openai")
        entry = candidate(id=connection["models"][0]["alias"], protocol="gemini_generate_content")
        options = media._resolve(entry, "Draw")
        self.assertEqual(options["url"], "https://generativelanguage.googleapis.com/v1beta/models/gemini-fixture-image:generateContent")
        self.assertEqual(options["headers"]["x-goog-api-key"], KEY)
        self.assertNotIn("Authorization", options["headers"])
        self.assertEqual(options["json"]["generationConfig"]["responseModalities"], ["TEXT", "IMAGE"])

    async def test_gateway_gemini_uses_image_modalities_and_openai_uses_images(self):
        gemini = media._resolve(candidate(), "Draw")
        self.assertTrue(gemini["url"].endswith("/chat/completions"))
        self.assertEqual(gemini["json"]["modalities"], ["text", "image"])
        openai = media._resolve(candidate("gpt-image-fixture", "openai"), "Draw")
        self.assertTrue(openai["url"].endswith("/images/generations"))
        with self.assertRaises(media.MediaProviderError):
            media._resolve(candidate("gpt-6.1-sol", "openai"), "Draw")

    async def test_real_image_manifest_required_and_records_verification(self):
        entry = candidate()
        with patch.object(media, "capabilities", AsyncMock(return_value=[entry])), \
                patch.object(media, "_request", AsyncMock(return_value={"data": [{"b64_json": B64}]})):
            result = await media.generate_image({"id": "job"}, "Draw", slot_id="cover", caption="Caption")
        self.assertEqual(result, self.manifest)
        self.storage.store.save_bytes.assert_called_once_with({"id": "job"}, RAW, kind="image", provider="gemini",
                                                               model=entry["id"], slot_id="cover", caption="Caption", mime_type=None)
        self.assertEqual(media._evidence()[entry["id"]]["sha256"], "verified-sha")

    async def test_explicit_provider_binding_never_crosses_to_gemini(self):
        with patch.object(media, "capabilities", AsyncMock(return_value=[candidate()])), \
                patch.object(media, "_request", AsyncMock()) as request:
            with self.assertRaises(media.MediaProviderError):
                await media.generate_image({"id": "job"}, "Draw", provider="openai")
            request.assert_not_awaited()

    async def test_auto_text_only_gemini_falls_back_to_openai_real_saved_image(self):
        from PIL import Image
        content = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(content, format="PNG")
        raw = content.getvalue()
        self.storage.store = DeliveryMediaStore(Path(self.temporary.name).resolve() / "real-media")
        job = {"id": str(uuid.uuid4()), "project_id": str(uuid.uuid4())}
        entries = [candidate(verified=True), candidate("gemini-second-image", verified=True),
                   candidate("gpt-image-fixture", "openai", verified=True)]
        request = AsyncMock(side_effect=[{"choices": [{"message": {"content": "No image returned"}}]},
                                        {"data": [{"b64_json": base64.b64encode(raw).decode()}]}])
        with patch.object(media, "capabilities", AsyncMock(return_value=entries)), patch.object(media, "_request", request):
            manifest = await media.generate_image(job, "Draw", slot_id="cover")
        self.assertEqual(request.await_count, 2)
        self.assertEqual([call.args[0]["json"]["model"] for call in request.await_args_list],
                         ["gemini-fixture-image", "gpt-image-fixture"])
        self.assertEqual(manifest["provider"], "openai")
        self.assertEqual(manifest["model"], "gpt-image-fixture")
        self.assertEqual(manifest["kind"], "image")
        self.assertEqual(self.storage.store.read(job, manifest["id"])[1], raw)
        self.assertNotIn("gemini-fixture-image", media._evidence())
        self.assertEqual(media._evidence()["gpt-image-fixture"]["sha256"], manifest["sha256"])

    async def test_explicit_gemini_or_exact_model_never_uses_alternate_provider(self):
        entries = [candidate(verified=True), candidate("gpt-image-fixture", "openai", verified=True)]
        for settings in [{"provider": "gemini"}, {"provider": "auto", "model": "gemini-fixture-image"}]:
            request = AsyncMock(return_value={"choices": [{"message": {"content": "Only text"}}]})
            with patch.object(media, "capabilities", AsyncMock(return_value=entries)), patch.object(media, "_request", request):
                with self.assertRaisesRegex(media.MediaProviderError, "没有真实图片"):
                    await media.generate_image({"id": "job"}, "Draw", **settings)
            self.assertEqual(request.await_count, 1)
            self.assertEqual(request.await_args.args[0]["json"]["model"], "gemini-fixture-image")

    async def test_auto_fallback_is_bounded_to_two_distinct_providers_and_safe_final_error(self):
        entries = [candidate(verified=True), candidate("gemini-second-image", verified=True),
                   candidate("gpt-image-fixture", "openai", verified=True), candidate("grok-fixture-image", "xai")]
        request = AsyncMock(side_effect=[ValueError(KEY), ValueError(KEY), {"data": [{"b64_json": B64}]}])
        with patch.object(media, "capabilities", AsyncMock(return_value=entries)), patch.object(media, "_request", request):
            with self.assertRaises(media.MediaProviderError) as failure:
                await media.generate_image({"id": "job"}, "Draw")
        self.assertEqual(request.await_count, 2)
        self.assertIn("两个不同提供商", str(failure.exception))
        self.assertNotIn(KEY, str(failure.exception))
        self.storage.store.save_bytes.assert_not_called()

    async def test_auto_cancellation_never_dispatches_fallback(self):
        entries = [candidate(verified=True), candidate("gpt-image-fixture", "openai", verified=True)]
        request = AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(media, "capabilities", AsyncMock(return_value=entries)), patch.object(media, "_request", request):
            with self.assertRaises(asyncio.CancelledError):
                await media.generate_image({"id": "job"}, "Draw")
        self.assertEqual(request.await_count, 1)
        self.storage.store.save_bytes.assert_not_called()

    async def test_url_output_requires_verified_import(self):
        with patch.object(media, "capabilities", AsyncMock(return_value=[candidate()])), \
                patch.object(media, "_request", AsyncMock(return_value={"data": [{"url": "https://cdn.example/image.png"}]})):
            self.assertEqual(await media.generate_image({"id": "job"}, "Draw"), self.manifest)
        self.storage.import_url.assert_awaited_once()
        self.storage.store.save_bytes.assert_not_called()

    async def test_no_text_only_output_or_unverified_manifest_can_complete(self):
        with patch.object(media, "capabilities", AsyncMock(return_value=[candidate()])), \
                patch.object(media, "_request", AsyncMock(return_value={"choices": [{"message": {"content": "done"}}]})):
            with self.assertRaisesRegex(media.MediaProviderError, "没有真实图片"):
                await media.generate_image({"id": "job"}, "Draw")
        self.storage.store.save_bytes.assert_not_called()
        self.assertEqual(media._evidence(), {})
        self.storage.store.save_bytes.return_value = {"id": "fake", "kind": "image"}
        with patch.object(media, "capabilities", AsyncMock(return_value=[candidate()])), \
                patch.object(media, "_request", AsyncMock(return_value={"data": [{"b64_json": B64}]})):
            with self.assertRaises(media.MediaProviderError):
                await media.generate_image({"id": "job"}, "Draw")
        self.assertEqual(media._evidence(), {})

    async def test_upstream_body_key_and_arbitrary_exception_are_redacted(self):
        request = httpx.Request("POST", "https://api.example/v1/images/generations", headers={"Authorization": KEY})
        response = httpx.Response(401, request=request, json={"error": {"message": KEY}})
        errors = [httpx.HTTPStatusError(KEY, request=request, response=response), ValueError(KEY),
                  httpx.ConnectError(KEY, request=request)]
        for error in errors:
            with patch.object(media, "capabilities", AsyncMock(return_value=[candidate()])), \
                    patch.object(media, "_request", AsyncMock(side_effect=error)):
                with self.assertRaises(media.MediaProviderError) as failure:
                    await media.generate_image({"id": "job"}, "Draw")
                self.assertNotIn(KEY, str(failure.exception))

    async def test_request_response_size_limit(self):
        def handler(request):
            return httpx.Response(200, content=b"{" + b" " * 100)
        with self.wire(handler), patch.object(media, "MAX_RESPONSE_BYTES", 16):
            with self.assertRaisesRegex(media.MediaProviderError, "大小限制"):
                await media._request({"url": media.GATEWAY + "/images/generations", "json": {}})

    async def test_changed_connection_invalidates_prior_image_receipt(self):
        connection = self.connection("gpt-image-fixture")
        def handler(request):
            return httpx.Response(200, json={"data": []})
        with self.wire(handler):
            initial = (await media.capabilities())[0]
            media._record_verified(initial, self.manifest)
            self.assertTrue((await media.capabilities())[0]["verified"])
            self.connections.save(model_connections.ConnectionInput(
                name="Changed", provider="openai_compatible", base_url="https://other.example/v1",
                api_key="different-key", models=[{"id": "gpt-image-fixture"}]), connection["id"])
            self.assertFalse((await media.capabilities())[0]["verified"])


if __name__ == "__main__":
    unittest.main()
