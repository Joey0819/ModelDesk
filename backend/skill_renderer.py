"""Bounded, offline rendering of a coding Agent's actual animation.

Uses Lemo's window.render(t) / window.DUR contract. No generated shell command
runs here; only Chromium frame capture, optional system TTS, and fixed FFmpeg.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from .delivery_media import store


ADAPTER = '''
ModelDesk 的 Lemo 执行适配：此规则是执行环境约束，优先于上游安装说明。
不要运行 setup、安装依赖、联网或打开桌面应用；平台已提供导演参考资料。
在隔离工作区先写 TREATMENT.md，选择符合用户题材的风格，再制作原创动画。
将成片页面保存为 film/index.html，纯自包含 HTML/CSS/Canvas/JS；不加载外部脚本、字体或 URL。
必须实现 window.render(t) 同步绘制指定秒的画面，以及 window.DUR（视频秒数，1–120）。
画面 1280×720，平台按 24fps 实际渲染；window.READY=true 表示资源已就绪。
不要用 GIF、循环计时器或只写剧本代替动画。必须有随时间变化的场景和动作。
优先通过 inputs/文件名 引用 film/inputs/ 中实际存在的图片；避免将图片编码为大段 base64；不要修改输入图。浏览器不支持本地文件路径或网络资源。
字幕直接绘制到画面。若用户需要中文旁白，写 film/narration.txt，平台用系统中文声音生成并混音；
按配音文字长度控制节奏，片长遵循用户明确要求。无旁白要求则不创建 narration.txt。
不要在页面里宣称完成验证。平台会收集真实 MP4 和两张画面，并继续独立审阅。
'''


def ready():
    from playwright.sync_api import sync_playwright
    ffmpeg = shutil.which('ffmpeg')
    with sync_playwright() as playwright:
        browser = Path(playwright.chromium.executable_path)
    return bool(ffmpeg and browser.is_file())


def read_regular(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > limit:
        raise ValueError('动画产物不是可读取的普通文件或超过限制')
    return path.read_bytes()


async def command(args, *, timeout=90):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise ValueError('媒体处理未完成：' + error.decode(errors='replace')[-500:])
        return output
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def render_film(item, workspace, directory):
    from playwright.async_api import async_playwright
    root = workspace / 'film'
    if root.is_symlink():
        raise ValueError('动画文件夹不能是链接')
    html = read_regular(root / 'index.html', 8_000_000)
    output = directory / 'rendered'
    output.mkdir(exist_ok=True)
    video = output / 'silent.mp4'
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('动画渲染需要本机 FFmpeg')
    frames = []
    browser = None
    process = None
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(viewport={'width': 1280, 'height': 720}, service_workers='block', accept_downloads=False)
            async def route(request):
                url = urlsplit(request.request.url)
                if url.scheme == 'http' and url.netloc == 'modeldesk.invalid' and url.path == '/index.html':
                    await request.fulfill(body=html, content_type='text/html', headers={'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src http://modeldesk.invalid data:; connect-src 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'; form-action 'none'; base-uri 'none'"})
                elif url.scheme == 'http' and url.netloc == 'modeldesk.invalid' and __import__('re').fullmatch(r'/inputs/[a-f0-9-]+\.(png|jpg|jpeg|webp)', url.path):
                    path = root / url.path.lstrip('/')
                    if path.parent.is_symlink():
                        await request.abort()
                    else:
                        await request.fulfill(body=read_regular(path, 25_000_000), content_type='image/' + path.suffix.lstrip('.').replace('jpg', 'jpeg'))
                else:
                    await request.abort()
            await context.route('**/*', route)
            await context.route_web_socket('**/*', lambda socket: socket.close())
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)[:300]))
            await page.goto('http://modeldesk.invalid/index.html')
            await page.wait_for_function('window.READY === true && typeof window.render === "function"', timeout=20000)
            duration = await page.evaluate('window.DUR')
            if type(duration) not in {int, float} or not 1 <= duration <= 120:
                raise ValueError('动画需要 window.DUR 为 1–120 秒；超出范围请拆分任务')
            process = await asyncio.create_subprocess_exec(ffmpeg, '-y', '-v', 'error', '-f', 'image2pipe', '-framerate', '24', '-c:v', 'mjpeg', '-i', '-', '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p', str(video), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            error_reader = asyncio.create_task(process.stderr.read(100000))
            total = round(duration * 24)
            async with asyncio.timeout(900):
                for frame in range(total):
                    await page.evaluate('(t) => window.render(t)', frame / 24)
                    if errors:
                        raise ValueError('动画脚本错误：' + errors[-1])
                    if frame in {int(total * .2), int(total * .7)}:
                        frames.append(await page.screenshot(type='png'))
                    process.stdin.write(await page.screenshot(type='jpeg', quality=88))
                    await process.stdin.drain()
                process.stdin.close()
                await process.wait()
            error = await error_reader
            if process.returncode:
                raise ValueError('视频编码失败：' + error.decode(errors='replace')[-300:])
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            if browser:
                await browser.close()
    narration = root / 'narration.txt'
    final = video
    if narration.exists():
        text = read_regular(narration, 18000).decode().strip()
        if text:
            if not Path('/usr/bin/say').is_file():
                raise ValueError('本机没有可用的系统旁白引擎；静音稿已保留，未计为完整交付')
            audio = output / 'narration.aiff'
            await command(['/usr/bin/say', '-v', 'Tingting', '-r', '190', '-f', str(narration), '-o', str(audio)])
            probe = json.loads(await command([shutil.which('ffprobe') or 'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(audio)]))
            if float(probe['format']['duration']) > duration + .2:
                raise ValueError('旁白长于视频，已保留初稿；需要缩短旁白或调整片长，未截断音轨')
            final = output / 'film.mp4'
            await command([ffmpeg, '-y', '-v', 'error', '-i', str(video), '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-af', 'apad', '-t', str(duration), str(final)])
    job = {'id': item.get('parent_job_id') or item['id'], 'project_id': item.get('project_id')}
    assets = [store.save_bytes(job, read_regular(final, 150_000_000), kind='video', provider='skill', model='Lemo-Opuscar / Codex', slot_id='skill-video', caption='Lemo 动画成片', source_task_id=item['id'])]
    evidence_frames = list(store.save_bytes(job, raw, kind='image', provider='skill', model='Lemo-Opuscar / Codex', slot_id=f'skill-frame-{index}', caption=f'成片画面 {index + 1}（抽帧）', source_task_id=item['id']) for index, raw in enumerate(frames))
    decoding = await verify_decoding(final, duration)
    return {'assets': assets, 'evidence_frames': evidence_frames, 'decoding': decoding, 'duration_seconds': duration, 'source_sha256': hashlib.sha256(html).hexdigest(), 'fps': 24, 'network': 'blocked', 'renderer': 'canvas-film', 'has_narration': final != video}


async def verify_decoding(path, duration):
    raw = await command([shutil.which('ffmpeg') or 'ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'framemd5', '-'], timeout=90)
    records = [line for line in raw.decode().splitlines() if line and not line.startswith('#')]
    hashes = {line.rsplit(',', 1)[-1].strip() for line in records}
    if len(records) < max(1, round(duration * 24) - 1):
        raise ValueError('全片视频解码不完整')
    return {'full_decode': True, 'frames': len(records), 'distinct_frames': len(hashes), 'scope': '全片解码及画面变化检查；不等同于人工审片或语义核验'}
