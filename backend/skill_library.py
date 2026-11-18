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
PSTACK_URL = 'https://github.com/cursor/plugins'
PSTACK_COMMIT = 'e1cc03121a0e71129a4fb730d7c5528a0a3e43c8'
# Curated pstack methodology skills: (source_folder_name, chinese_display_name)
PSTACK_SKILLS = {
    'tdd': ('pstack-tdd', 'TDD 缺陷修复'),
    'blast-radius': ('pstack-blast-radius', '改动影响面分析'),
    'interrogate': ('pstack-interrogate', '需求追问'),
    'unslop': ('pstack-unslop', '去 AI 味写作'),
    'technical-writing': ('pstack-technical-writing', '技术写作'),
    'figure-it-out': ('pstack-figure-it-out', '先弄清再动手'),
    'principle-fix-root-causes': ('pstack-fix-root-causes', '修根因不是修症状'),
    'principle-prove-it-works': ('pstack-prove-it-works', '先证明能跑通'),
}
router = APIRouter(prefix='/api/skills', tags=['skills'])


class SkillInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=80)
    description: str = Field(default='', max_length=400)
    filename: str = Field(default='', max_length=255)
    instructions: str = Field(min_length=1, max_length=50000)


def parse_frontmatter(text):
    """Parse YAML frontmatter from SKILL.md text. Hand-written parser, no yaml dependency.

    Supports LF and CRLF delimiters and single-line 'key: value' pairs. Preserves body line endings.
    Values wrapped in single/double quotes are unquoted; literal true/false become booleans.
    On any parse error or missing end delimiter, degrades safely: entire text as body, empty meta.
    Returns (meta_dict, body_text, frontmatter_raw). Always a 3-tuple: the early
    returns hand back an empty ``frontmatter_raw`` instead of a shorter shape, so
    callers unpack once and never index conditionally.
    Raises HTTPException(422) if body is empty after stripping.
    """
    if not text.startswith(('---\n', '---\r\n')):
        if not text.strip():
            raise HTTPException(422, '技能指令为空')
        return {}, text, ''
    # Find closing delimiter: next line that is exactly '---' or '...'
    lines = text.split('\n')
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].removesuffix('\r') in {'---', '...'}:
            end_idx = i
            break
    if end_idx is None:
        # No closing delimiter found: degrade to treating entire text as body
        if not text.strip():
            raise HTTPException(422, '技能指令为空')
        return {}, text, ''
    # Parse frontmatter block (lines between opening and closing delimiters)
    meta = {}
    fm_block_lines = lines[1:end_idx]
    fm_raw = '\n'.join(fm_block_lines).removesuffix('\r')
    try:
        for line in fm_block_lines:
            if not line.strip() or ':' not in line:
                continue
            key, _, val = line.partition(':')
            key = key.strip()
            val = val.strip()
            if not key:
                continue
            # Unquote single/double quoted values
            if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                val = val[1:-1].replace('\\"', '"').replace('\\\\', '\\')
            elif len(val) >= 2 and val[0] == "'" and val[-1] == "'":
                val = val[1:-1]
            elif val == 'true':
                val = True
            elif val == 'false':
                val = False
            meta[key] = val
    except Exception:
        # Any parse error: degrade gracefully
        pass
    # Body is everything after the closing delimiter
    body = '\n'.join(lines[end_idx + 1:])
    if not body.strip():
        raise HTTPException(422, '技能指令为空')
    return meta, body, fm_raw


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


def put_skill(value, name, instructions, *, description='', source='local', revision=None, renderer=None):
    identifier(value)
    if secret_text(instructions) or secret_text(name) or secret_text(description):
        raise HTTPException(422, 'Skill 不得包含密钥或凭据')
    folder = DIRECTORY / value
    if DIRECTORY.is_symlink() or folder.is_symlink():
        raise HTTPException(409, 'Skill 路径无效')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    item = dict(id=value, name=name, instructions=instructions, description=description, source=source, revision=revision,
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
    result = {key: value for key, value in item.items() if key != 'instructions'}
    # Older manifests may lack 'description'; .get ensures backward compatibility.
    if 'description' not in result:
        result['description'] = ''
    return result | {
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
    recommended = [
        {'id': 'lemo-opuscar', 'name': 'Lemo-Opuscar 动画导演', 'source': LEMO_URL, 'revision': LEMO_COMMIT},
        {'id': 'pstack', 'name': 'pstack 方法论精选', 'description': '从 cursor/plugins 精选的 8 个方法论技能，固定 commit 版本，只导入指令文本', 'source': PSTACK_URL, 'revision': PSTACK_COMMIT},
    ]
    return {'renderer_ready': render_ready, 'installed': installed, 'recommended': recommended,
            'policy': '安装只读取指令文本，不执行任何脚本、hooks 或安装程序。绑定到角色后生效；不会自动安装依赖、运行 hooks 或获取新的系统权限。'}


@router.post('/import', dependencies=[Depends(require_local_admin)])
async def import_skill(body: SkillInput):
    meta, instructions_body, fm_raw = parse_frontmatter(body.instructions)
    if secret_text(fm_raw) or secret_text(body.filename):
        raise HTTPException(422, 'Skill 不得包含密钥或凭据')
    filename = re.split(r'[/\\]', body.filename)[-1].strip()
    filename_stem = re.sub(r'\.md$', '', filename, flags=re.IGNORECASE).strip()
    final_name = body.name.strip() or str(meta.get('name', '')).strip() or filename_stem[:80]
    if not final_name:
        raise HTTPException(422, '技能名称不能为空')
    final_desc = (body.description.strip() or str(meta.get('description', '')).strip())[:400]
    value = 'custom-' + hashlib.sha256((final_name + instructions_body).encode()).hexdigest()[:16]
    return put_skill(value, final_name, instructions_body, description=final_desc)


def install_pstack_files(folder):
    """Install curated pstack skills from a local checkout folder.

    Only reads SKILL.md files; never executes scripts or copies references/.
    Symlink checks on every path component for safety.
    """
    results = []
    for source_name, (skill_id, display_name) in PSTACK_SKILLS.items():
        skill_dir = folder / 'pstack' / 'skills' / source_name
        # Symlink check on intermediate directories and target
        if (folder / 'pstack').is_symlink() or (folder / 'pstack' / 'skills').is_symlink() or skill_dir.is_symlink():
            raise HTTPException(409, 'Skill 路径无效')
        skill_md = skill_dir / 'SKILL.md'
        if skill_md.is_symlink():
            raise HTTPException(409, 'Skill 路径无效')
        raw_text = skill_md.read_text()
        # Parse frontmatter using commit 1's parser
        result = parse_frontmatter(raw_text)
        meta, body = result[0], result[1]
        fm_raw = result[2] if len(result) > 2 else ''
        # Secret check covers body, name, description, and raw frontmatter
        if secret_text(body) or secret_text(display_name) or secret_text(fm_raw):
            raise HTTPException(422, 'Skill 不得包含密钥或凭据')
        desc = str(meta.get('description', '')).strip()[:400]
        if secret_text(desc):
            raise HTTPException(422, 'Skill 不得包含密钥或凭据')
        source_url = f'{PSTACK_URL}/tree/main/pstack/skills/{source_name}'
        results.append(put_skill(skill_id, display_name, body, description=desc,
                                 source=source_url, revision=PSTACK_COMMIT))
    return results


def install_pstack():
    """Clone pstack repo at fixed commit and install curated skills. Mirrors install_lemo safety posture."""
    with tempfile.TemporaryDirectory(prefix='modeldesk-pstack-') as tmp:
        folder = Path(tmp) / 'source'
        folder.mkdir()
        subprocess.run(['git', '-C', str(folder), 'init', '--quiet'], check=True, timeout=15, capture_output=True)
        subprocess.run(['git', '-C', str(folder), 'remote', 'add', 'origin', PSTACK_URL + '.git'], check=True, timeout=15, capture_output=True)
        subprocess.run(['git', '-C', str(folder), 'fetch', '--quiet', '--depth', '1', 'origin', PSTACK_COMMIT], check=True, timeout=120, capture_output=True)
        subprocess.run(['git', '-C', str(folder), '-c', 'core.hooksPath=/dev/null', 'checkout', '--quiet', '--detach', PSTACK_COMMIT], check=True, timeout=45, capture_output=True)
        return install_pstack_files(folder)


@router.post('/pstack/install', dependencies=[Depends(require_local_admin)])
async def install_pstack_endpoint():
    try:
        installed = await asyncio.to_thread(install_pstack)
        return {'name': 'pstack 方法论精选', 'installed': installed}
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(502, '下载 pstack 技能失败，现有安装保持可用；请检查 GitHub 连接后重试') from None


@router.post('/lemo-opuscar/install', dependencies=[Depends(require_local_admin)])
async def install_recommended():
    try:
        return await asyncio.to_thread(install_lemo)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(502, '下载 Skill 失败，现有安装保持可用；请检查 GitHub 连接后重试') from None
