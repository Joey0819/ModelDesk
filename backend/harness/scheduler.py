"""Bounded dependency scheduling; state is owned and persisted by the caller."""
import asyncio


def validate_graph(steps):
    ids = [step["id"] for step in steps]
    if len(ids) != len(set(ids)):
        raise ValueError("步骤标识不能重复")
    remaining = {step["id"]: set(step.get("depends_on", [])) for step in steps}
    if any(not deps <= set(ids) or key in deps for key, deps in remaining.items()):
        raise ValueError("步骤依赖不存在或引用自身")
    done = set()
    while remaining:
        ready = {key for key, deps in remaining.items() if deps <= done}
        if not ready:
            raise ValueError("执行计划含循环依赖")
        done.update(ready)
        remaining = {key: deps for key, deps in remaining.items() if key not in ready}


async def run_graph(steps, execute, changed, *, concurrency=2):
    """Independent branches proceed; failed prerequisites block descendants.

    Complete steps are reused. Cancellation drains every launched coroutine
    before returning, allowing adapters to stop their tracked child processes.
    """
    validate_graph(steps)
    by_id = {step["id"]: step for step in steps}
    running = {}
    try:
        while True:
            for step in steps:
                if step["status"] != "pending":
                    continue
                parents = [by_id[key] for key in step.get("depends_on", [])]
                if any(parent["status"] in {"error", "blocked", "cancelled", "interrupted"} for parent in parents):
                    step.update(status="blocked", error="前置步骤尚未完成；已保留其他分支的成果")
                    changed()
                elif all(parent["status"] == "complete" for parent in parents) and len(running) < concurrency:
                    step["status"] = "running"
                    changed()
                    running[asyncio.create_task(execute(step, parents))] = step
            if not running:
                for step in steps:
                    if step["status"] == "pending":
                        step.update(status="blocked", error="前置步骤尚未完成；已保留其他分支的成果")
                        changed()
                return
            finished, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                step = running.pop(task)
                try:
                    await task
                except asyncio.CancelledError:
                    raise
                except Exception:
                    step.update(status="error", error="执行步骤异常；实际输出已保留")
                changed()
    finally:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        for step in steps:
            if step["status"] in {"running", "pending"}:
                step["status"] = "cancelled"
        if running:
            changed()
