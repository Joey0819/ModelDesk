"""Image roles produce durable files within the dependency graph."""
import asyncio
import hashlib
import json

from .delivery_media import store
from .media_providers import generate_image, _safe_error
from .image_brief import prepare_image_prompt


async def execute_images(job, member, step, prompt, delivery, changed):
    count = step.get('image_count', 1)
    if not 1 <= count <= 4:
        raise ValueError('单个绘图步骤支持 1–4 张图片，请拆分任务')
    assets = []
    prepared = None
    for index in range(count):
        slot = f"role-{step['id']}-{index + 1}"
        previous = [item for item in delivery['media_tasks'] if item.get('id') == slot]
        task = previous[-1] if previous else {'id': slot, 'kind': 'image', 'member_id': member['id'], 'model': member['model']}
        # A slot is one deliverable. Keep retry history on it, rather than
        # displaying several failed pictures for the same requested image.
        if len(previous) > 1:
            delivery['media_tasks'][:] = [item for item in delivery['media_tasks'] if item.get('id') != slot]
        if not previous or len(previous) > 1:
            delivery['media_tasks'].append(task)
        saved = next((a for a in delivery['media'] if a.get('slot_id') == slot), None)
        if saved:
            checked, _ = store.read(job, saved['id'])
            if checked['sha256'] != saved['sha256']:
                raise ValueError('已保存的绘图产物发生变化')
            asset = checked
        else:
            task.update(status='running')
            task.pop('error', None)
            attempt = {'number': len(task.setdefault('attempts', [])) + 1, 'status': 'running'}
            task['attempts'].append(attempt)
            changed()
            try:
                if prepared is None:
                    prepared = await prepare_image_prompt(job, member, step, prompt, delivery, changed)
                asset = await generate_image(job, f'创作第 {index + 1}/{count} 张图片，保持角色与风格一致，每张独立构图。\n' + prepared,
                    model=member['model'], slot_id=slot, caption=f"{member['name']} · 图片 {index + 1}")
                delivery['media'].append(asset)
                attempt.update(status='complete', asset_id=asset['id'])
            except asyncio.CancelledError:
                task.update(status='cancelled')
                attempt.update(status='cancelled')
                raise
            except Exception as error:
                task.update(status='error', error=_safe_error(error))
                attempt.update(status='error', error=task['error'])
                raise
            finally:
                changed()
        task.update(status='complete', asset_id=asset['id'], provider=asset['provider'])
        task.pop('error', None)
        assets.append(asset)
    output = json.dumps({'images': assets, 'note': '已保存真实图片；本地 URL 只在 ModelDesk 内可用，外部 Agent 必须通过附件交接。'}, ensure_ascii=False)
    step.update(status='complete', output=output, media=assets, output_sha256=hashlib.sha256(output.encode()).hexdigest())
    step.pop('error', None)
