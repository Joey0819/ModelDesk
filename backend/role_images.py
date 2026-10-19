"""Image roles produce durable files within the dependency graph."""
import hashlib
import json

from .delivery_media import store
from .media_providers import generate_image


async def execute_images(job, member, step, prompt, delivery, changed):
    count = step.get('image_count', 1)
    if not 1 <= count <= 4:
        raise ValueError('单个绘图步骤支持 1–4 张图片，请拆分任务')
    assets = []
    for index in range(count):
        slot = f"role-{step['id']}-{index + 1}"
        saved = next((a for a in delivery['media'] if a.get('slot_id') == slot), None)
        if saved:
            checked, _ = store.read(job, saved['id'])
            if checked['sha256'] != saved['sha256']:
                raise ValueError('已保存的绘图产物发生变化')
            asset = checked
        else:
            task = {'id': slot, 'kind': 'image', 'member_id': member['id'], 'model': member['model'], 'status': 'running'}
            delivery['media_tasks'].append(task)
            changed()
            try:
                asset = await generate_image(job, f'创作第 {index + 1}/{count} 张图片，保持角色与风格一致，每张独立构图。\n' + prompt,
                    model=member['model'], slot_id=slot, caption=f"{member['name']} · 图片 {index + 1}")
                delivery['media'].append(asset)
                task.update(status='complete', asset_id=asset['id'], provider=asset['provider'])
            except Exception:
                task.update(status='error', error='所选绘图模型调用失败，未切换模型；请查看步骤错误')
                raise
            finally:
                changed()
        assets.append(asset)
    output = json.dumps({'images': assets, 'note': '已保存真实图片；本地 URL 只在 ModelDesk 内可用，外部 Agent 必须通过附件交接。'}, ensure_ascii=False)
    step.update(status='complete', output=output, media=assets, output_sha256=hashlib.sha256(output.encode()).hexdigest())
