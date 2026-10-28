"""Project-file evidence, genuine pixels, immutable history, and input boundaries."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from PIL import Image, PngImagePlugin

from backend import attachments, attachment_io, projects
from backend.platform_store import PlatformStore, Resource
from backend.workbench import JobRequest, public_error


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_store = projects.ProjectStore(self.root / "projects")
        self.assets = attachments.AssetStore(self.root / "assets")
        self.inputs = self.root / "data/workbench/attachment-inputs"
        self.project = self.project_store.register("普通项目", description="只需名称的资料项目")
        self.other = self.project_store.register("其他项目")
        self.project_patch = patch.object(projects, "store", self.project_store)
        self.image_patch = patch.object(attachment_io, "INPUT_ROOT", self.inputs)
        self.project_patch.start(); self.image_patch.start()

    def tearDown(self):
        self.image_patch.stop(); self.project_patch.stop(); self.temp.cleanup()

    def image(self):
        image = Image.new("RGB", (32, 24), "red")
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("private-metadata", "metadata-should-be-removed")
        output = io.BytesIO(); image.save(output, format="PNG", pnginfo=metadata)
        return output.getvalue()

    def job(self, project=None, ids=None):
        return {"id": str(uuid.uuid4()), "project_id": (project or self.project)["id"], "attachment_ids": ids or [], "include_project_assets": True}

    def freeze(self, job):
        job["attachment_manifest"] = self.assets.freeze(job, self.inputs)
        job["attachments"] = job["attachment_manifest"]["items"]
        return job

    def test_managed_project_registration_durable_no_git_and_optional_snapshot(self):
        self.assertTrue(self.project["managed"])
        self.assertTrue(Path(self.project["path"]).is_dir())
        self.assertFalse((Path(self.project["path"]) / ".git").exists())
        self.assertEqual(projects.ProjectStore(self.project_store.directory).get(self.project["id"])["description"], "只需名称的资料项目")
        JobRequest(mode="chat", prompt="普通项目", models=["model"], project_id=self.project["id"])
        with self.assertRaises(ValueError):
            JobRequest(mode="review", prompt="审查", models=["model"], project_id=self.project["id"])
        with self.assertRaises(ValueError):
            JobRequest(mode="chat", prompt="快照", models=["model"], snapshot_id=str(uuid.uuid4()))

    def test_pixels_preserved_metadata_removed_and_tampering_rejected(self):
        image = self.assets.add(self.project["id"], "image.png", self.image())
        stored = self.assets.content_path(image)
        self.assertNotIn(b"metadata-should-be-removed", stored.read_bytes())
        with Image.open(stored) as decoded:
            self.assertEqual(decoded.getpixel((4, 4)), (255, 0, 0))
        job = self.freeze(self.job(ids=[image["id"]]))
        _, _, descriptors = self.assets.prepared(job, self.inputs)
        content = attachment_io.image_content_parts({"_attachments": descriptors})
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("base64", json.dumps(job))
        Path(descriptors[0]["path"]).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            attachment_io.image_content_parts({"_attachments": descriptors})

    def test_shared_assets_turn_selection_and_foreign_project_rejection(self):
        shared = self.assets.add(self.project["id"], "shared.md", "共用代号 ALPHA".encode(), shared=True)
        private = self.assets.add(self.project["id"], "private.txt", b"ONLY_CURRENT")
        manifest = self.assets.freeze(self.job(ids=[private["id"]]), self.inputs)
        self.assertEqual({item["id"] for item in manifest["items"]}, {shared["id"], private["id"]})
        job = self.job(); job["include_project_assets"] = False
        self.assertEqual(self.assets.freeze(job, self.inputs)["items"], [])
        with self.assertRaises(HTTPException) as denied:
            self.assets.validate(self.job(project=self.other, ids=[private["id"]]))
        self.assertEqual(denied.exception.status_code, 403)

    def test_history_picture_chain_survives_removal_and_retains_original_provenance(self):
        image = self.assets.add(self.project["id"], "image.png", self.image())
        original = self.freeze(self.job(ids=[image["id"]])); original["status"] = "complete"
        self.assets.remove(self.project["id"], image["id"])
        previous = original
        jobs = {original["id"]: original}
        for _ in range(2):
            current = self.freeze(self.job())
            _, _, images = self.assets.prepared(current, self.inputs)
            history = {"metadata": {"references": [{"job_id": previous["id"]}]}}
            inherited = self.assets.inherit_images(current, history, jobs, images, self.inputs)
            self.assertEqual(inherited["items"][0]["source_job_id"], original["id"])
            self.assertEqual(len(attachment_io.image_content_parts({"_attachments": images})), 1)
            current.update(history_attachment_manifest=inherited, attachments=inherited["items"], status="complete")
            jobs[current["id"]] = current; previous = current
        foreign = self.freeze(self.job(project=self.other))
        isolated = self.assets.inherit_images(foreign, history, jobs, [], self.inputs)
        self.assertEqual(isolated["items"], [])

    def test_failed_task_keeps_frozen_document_and_image_for_followup(self):
        from backend.task_context import build_task_context, include_task_attachments
        document = self.assets.add(self.project['id'], 'brief.md', b'USER_ATTACHMENT_CONSTRAINT')
        image = self.assets.add(self.project['id'], 'reference.png', self.image())
        old = self.freeze(self.job(ids=[document['id'], image['id']]))
        old.update(mode='group', conversation_id='room', group_room_id='room', status='error',
                   group_context_mode='fresh', prompt='Follow the attached brief', created_at='2026-10-28T00:00:00Z')
        self.assets.remove(self.project['id'], document['id'])
        self.assets.remove(self.project['id'], image['id'])
        new = self.freeze(self.job())
        new.update(mode='group', conversation_id='room', group_room_id='room', prompt='Continue', created_at='2026-10-28T01:00:00Z')
        context = build_task_context({old['id']: old}, new)
        with patch.object(attachments, 'store', self.assets):
            include_task_attachments(context, {old['id']: old}, new, self.inputs)
        self.assertIn('USER_ATTACHMENT_CONSTRAINT', context['reference_documents'][0]['content'])
        images = []
        result = self.assets.inherit_images(new, {'metadata': {'references': [{'job_id': old['id']}]}}, {old['id']: old}, images, self.inputs)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(len(attachment_io.image_content_parts({'_attachments': images})), 1)

    def test_folder_inventory_and_reads_exclude_links_credentials_caches_and_escape(self):
        folder = self.root / "local"; folder.mkdir()
        (folder / "safe.txt").write_text("safe")
        (folder / ".env").write_text("synthetic excluded value")
        (folder / "node_modules").mkdir(); (folder / "node_modules/cache.txt").write_text("excluded")
        outside = self.root / "outside.txt"; outside.write_text("outside")
        (folder / "link.txt").symlink_to(outside)
        linked = self.project_store.add_folder(self.project["id"], str(folder))
        inventory = attachments.folder_files(linked)
        self.assertEqual([item["relative_path"] for item in inventory["files"]], ["safe.txt"])
        self.assertEqual(attachments.read_local(folder, "safe.txt"), b"safe")
        for name in ["../outside.txt", ".env", "node_modules/cache.txt", "link.txt"]:
            with self.assertRaises((ValueError, OSError)):
                attachments.read_local(folder, name)

    def test_docx_extraction_and_unsupported_files_do_not_claim_reading(self):
        from docx import Document
        document = Document(); document.add_paragraph("DOCX evidence 928")
        output = io.BytesIO(); document.save(output)
        result = self.assets.add(self.project["id"], "document.docx", output.getvalue())
        self.assertIn("DOCX evidence 928", result["text_preview"])
        unsupported = self.assets.add(self.project["id"], "archive.bin", b"unknown format")
        self.assertEqual(unsupported["status"], "unsupported")
        with self.assertRaises(HTTPException):
            self.assets.validate(self.job(ids=[unsupported["id"]]))
        with self.assertRaises(ValueError):
            self.assets.add(self.project["id"], "broken.png", b"not image bytes")

    def test_more_than_eight_current_images_rejected_shared_overflow_is_explicit(self):
        images = [self.assets.add(self.project["id"], f"image-{i}.png", self.image(), shared=True) for i in range(9)]
        manifest = self.assets.freeze(self.job(), self.inputs)
        self.assertEqual(manifest["coverage"]["image_count"], 8)
        self.assertTrue(manifest["coverage"]["truncated"])
        self.assertEqual(len(manifest["coverage"]["omitted"]), 1)
        with self.assertRaises(HTTPException):
            self.assets.freeze(self.job(ids=[item["id"] for item in images]), self.inputs)

    def test_resource_scopes_and_error_messages_exclude_private_image_payload(self):
        store = PlatformStore(self.root / "resources.json")
        global_item = store.put("prompts", Resource(name="global", content="global"))
        own = store.put("prompts", Resource(name="own", content="own", project_id=self.project["id"]))
        store.put("prompts", Resource(name="other", content="other", project_id=self.other["id"]))
        self.assertEqual({item["id"] for item in store.list("prompts", project_id=self.project["id"], scope="all")}, {own["id"], global_item["id"]})
        self.assertEqual([item["id"] for item in store.list("prompts", project_id=self.project["id"], scope="project")], [own["id"]])
        message = public_error(FileNotFoundError("missing /Users/example/data/workbench/attachment-inputs/job/file.png data:image/png;base64,YWJj"))
        self.assertNotIn("attachment-inputs/", message); self.assertNotIn("base64", message)


if __name__ == "__main__":
    unittest.main()
