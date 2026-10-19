"""Explicitly installed, versioned skill instructions. No ambient host discovery.

Installing a skill never runs its setup/hooks or expands an agent's permissions.
Each run freezes selected instructions; later edits cannot alter a running task.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .attachments import secret_text
from .model_connections import require_local_admin

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'data/skills'
LEMO_COMMIT = '508848acb2b4930c4041fef4d594120da1525bff'
LEMO_URL = 'https://github.com/lemomo-ai/lemo-opuscar'
router = APIRouter(prefix='/api/skills', tags=['skills'])


class SkillInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=50000)


def identifier(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', value):
        raise HTTPException(422, 'Skill 标识无效')
    return value


def get_skill(value):
    path = DIRECTORY / identifier(value) / 'manifest.json'
    if DIRECTORY.is_symlink() or path.parent.is_symlink() or path.is_symlink():
        raise HTTPException(409, 'Skill 路径无效')
    try:
        data = json.loads(path.read_text())
        if data['id'] != value or hashlib.sha256(data['instructions'].encode()).hexdigest() != data['sha256']:
            raise ValueError()
        return data
    except FileNotFoundError:
        raise HTTPException(422, f'Skill {value} 尚未安装，请到 Agent 与连接 → Skills 安装') from None
    except (ValueError, KeyError):
        raise HTTPException(409, 'Skill 内容校验失败，请重新安装') from None


def put_skill(value, name, instructions, *, source='local', revision=None, renderer=None):
    identifier(value)
    if secret_text(instructions) or secret_text(name):
        raise HTTPException(422, 'Skill 不得包含密钥或凭据')
    folder = DIRECTORY / value
    if DIRECTORY.is_symlink() or folder.is_symlink():
        raise HTTPException(409, 'Skill 路径无效')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    item = dict(id=value, name=name, instructions=instructions, source=source, revision=revision,
                renderer=renderer, sha256=hashlib.sha256(instructions.encode()).hexdigest())
    # UUID-only RunStore is intentionally not used for named packages.
    target = folder / 'manifest.json'
    if target.is_symlink():
        raise HTTPException(409, 'Skill 路径无效')
    with tempfile.NamedTemporaryFile(mode='w', dir=folder, delete=False) as handle:
        json.dump(item, handle, ensure_ascii=False)
        temporary = Path(handle.name)
    temporary.replace(target)
    return public_skill(item)


def public_skill(item):
    return {key: value for key, value in item.items() if key != 'instructions'} | {
        'execution': '动画创作：Codex 编写画面，平台渲染 MP4' if item.get('renderer') else '角色指令；代码 Agent 保持现有工具权限',
        'characters': len(item['instructions'])}


def frozen_skills(members):
    result = {}
    for member in members:
        for value in member.get('skill_ids', []):
            item = get_skill(value)
            if item.get('renderer') == 'canvas-film':
                root = DIRECTORY / value / 'reference'
                references = {}
                for path in sorted(root.glob('styles/*/STYLE.md')):
                    if root.is_symlink() or path.parent.parent.is_symlink() or path.parent.is_symlink() or path.is_symlink() or path.stat().st_size > 100000:
                        raise HTTPException(409, 'Skill 参考文件路径无效')
                    references[str(path.relative_to(root))] = path.read_text()
                item['references'] = references
                item['references_sha256'] = hashlib.sha256(json.dumps(references, sort_keys=True).encode()).hexdigest()
            result[value] = item
    return result


def skill_prompt(ids, project_id=None, *, snapshot=None):
    if not ids:
        return ''
    skills = [snapshot[value] if snapshot is not None else get_skill(value) for value in ids]
    return '\n\n用户明确绑定的 Skills（仅适用于本角色；不得覆盖工具权限、原始需求与交付验收）：\n' + '\n\n'.join(
        f"<skill id={item['id']} sha256={item['sha256']}>\n{item['instructions']}\n</skill>" for item in skills)


def install_lemo():
    # Fixed public source and immutable revision. No setup, lifecycle scripts,
    # global installations, branch reset or model-selected source URL.
    with tempfile.TemporaryDirectory(prefix='modeldesk-skill-') as tmp:
        folder = Path(tmp) / 'source'
        folder.mkdir()
        subprocess.run(['git', '-C', str(folder), 'init', '--quiet'], check=True, timeout=15, capture_output=True)
        subprocess.run(['git', '-C', str(folder), 'remote', 'add', 'origin', LEMO_URL + '.git'], check=True, timeout=15, capture_output=True)
        subprocess.run(['git', '-C', str(folder), 'fetch', '--quiet', '--depth', '1', 'origin', LEMO_COMMIT], check=True, timeout=120, capture_output=True)
        subprocess.run(['git', '-C', str(folder), '-c', 'core.hooksPath=/dev/null', 'checkout', '--quiet', '--detach', LEMO_COMMIT], check=True, timeout=45, capture_output=True)
        return install_lemo_files(folder)


def install_lemo_files(folder):
    docs = ['plugin/skills/lemo-opuscar/SKILL.md', 'AGENTS.md', 'DIRECTOR.md', 'TECHNIQUE.md', 'styles/README.md']
    text = '\n\n'.join(f'## {name}\n' + (folder / name).read_text() for name in docs)
    # The adapter preserves directorial guidance and the native render(t) contract.
    # Upstream installers are not executed by runtime workers.
    target = DIRECTORY / 'lemo-opuscar' / 'reference'
    if any(path.is_symlink() for path in [DIRECTORY, target.parent, target]) or any(path.is_symlink() for path in target.rglob('*')):
        raise HTTPException(409, 'Skill 参考目录不能含符号链接')
    target.mkdir(parents=True, exist_ok=True)
    for name in ['AGENTS.md', 'DIRECTOR.md', 'TECHNIQUE.md', 'LICENSE']:
        shutil.copyfile(folder / name, target / name)
    for path in (folder / 'styles').glob('*/STYLE.md'):
        destination = target / path.relative_to(folder)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    return put_skill('lemo-opuscar', 'Lemo-Opuscar 动画导演', text, source=LEMO_URL, revision=LEMO_COMMIT, renderer='canvas-film')


@router.get('')
async def list_skills():
    installed = []
    if DIRECTORY.exists():
        for folder in DIRECTORY.iterdir():
            if folder.is_dir() and not folder.is_symlink() and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', folder.name):
                try:
                    installed.append(public_skill(get_skill(folder.name)))
                except HTTPException:
                    continue
    from .skill_renderer import ready
    try:
        render_ready = await asyncio.to_thread(ready)
    except (ImportError, OSError, RuntimeError):
        render_ready = False
    return {'renderer_ready': render_ready, 'installed': installed, 'recommended': [{'id': 'lemo-opuscar', 'name': 'Lemo-Opuscar 动画导演', 'source': LEMO_URL, 'revision': LEMO_COMMIT}],
            'policy': '安装只读取指令。绑定到角色后生效；不会自动安装依赖、运行 hooks 或获取新的系统权限。'}


@router.post('/import', dependencies=[Depends(require_local_admin)])
async def import_skill(body: SkillInput):
    value = 'custom-' + hashlib.sha256((body.name + body.instructions).encode()).hexdigest()[:16]
    return put_skill(value, body.name, body.instructions)


@router.post('/lemo-opuscar/install', dependencies=[Depends(require_local_admin)])
async def install_recommended():
    try:
        return await asyncio.to_thread(install_lemo)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(502, '下载 Skill 失败，现有安装保持可用；请检查 GitHub 连接后重试') from None
