"""Exercise the production board in Chromium with isolated, disk-backed project and room stores."""
import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend import groups, projects, todo_board, workbench
from backend.harness.models import TeamRequest
from backend.harness.teams import TeamStore


def checkpoint(room, prompt, status, *, feishu=False):
    stamp = workbench.now()
    job = {"id": str(uuid4()), "project_id": room["project_id"], "group_room_id": room["id"], "conversation_id": room["id"],
           "mode": "group", "status": status, "prompt": prompt, "created_at": stamp, "updated_at": stamp, "events": [], "models": []}
    if feishu:
        job["external_source"] = {"platform": "feishu", "app_id": "fixture-app", "chat_id": "fixture-chat", "message_id": "fixture-message"}
    workbench.manager.jobs[job["id"]] = job
    workbench.manager.save(job)
    return job


def fixture(directory):
    projects.store = projects.ProjectStore(directory / "projects")
    groups.store = groups.RoomStore(directory / "rooms")
    workbench.manager = workbench.JobManager(directory / "jobs")
    todo_board.BOARD_FILE = directory / "board.json"
    todo_board._cache = None
    workbench.manager._todo_sync_enabled = True
    teams = TeamStore(directory / "teams")
    rooms = []
    for name in ("产品手册", "营销网站"):
        project = projects.store.register(name)
        team = teams.put(TeamRequest(name="编辑团队", project_id=project["id"], members=[
            {"id": "writer", "name": "作者", "model": "fixture-writer", "required": True, "expected_output": "一份可发布的初稿"},
            {"id": "editor", "name": "审稿人", "model": "fixture-editor", "after_member_ids": ["writer"], "expected_output": "逐条核对引用来源"},
        ]))
        for title in ("编辑组", "排版组"):
            rooms.append(groups.store.create(groups.RoomRequest(project_id=project["id"], team_id=team["id"], title=title), teams))
    runs = [
        checkpoint(rooms[0], "复核产品手册的引用来源", "running"),
        checkpoint(rooms[2], "整理官网更新说明", "complete"),
    ]

    app = FastAPI()
    # Allow the production board API to write only the isolated board and expose read-only group views.
    app.include_router(todo_board.router)
    for path, handler in [
        ("/api/groups/catalog", groups.room_catalog), ("/api/groups", groups.list_rooms),
        ("/api/groups/{identifier}", groups.get_room), ("/api/groups/{identifier}/messages", groups.list_messages),
        ("/api/workbench/projects", projects.list_projects), ("/api/workbench/jobs", workbench.list_jobs),
        ("/api/workbench/jobs/{job_id}", workbench.get_job),
    ]:
        app.add_api_route(path, handler)

    @app.get("/remote/session")
    def session():
        return {"authenticated": True}

    @app.get("/remote/info")
    def remote_info():
        return {"platform": {"system": "linux"}}

    @app.get("/api/skills")
    def skills():
        return {"installed": [], "available": []}

    @app.get("/api/workbench/status")
    def status():
        return {"gateway": {"online": True}, "models": [], "providers": [], "tools": {}}

    @app.get("/api/{path:path}")
    def other_catalogs(path: str):
        return {"engines": []} if path == "group-tasks/engines" else []

    app.mount("/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="workbench")
    return app, rooms, runs


def exercise(origin, rooms, runs, screenshots):
    def shot(page, name):
        if screenshots:
            page.screenshot(path=screenshots / (name + ".png"), animations="disabled")

    with sync_playwright() as playwright:
        executable = os.getenv("CHROMIUM_EXECUTABLE")
        browser = playwright.chromium.launch(channel=None if executable else "chrome", executable_path=executable, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(origin)
        navigation = page.get_by_role("navigation", name="核心工作台切换")
        board_button = navigation.get_by_role("button", name="任务看板", exact=True)
        board_button.click()
        project = page.get_by_label("筛选项目")
        room = page.get_by_label("筛选群聊")
        first, empty, second = rooms[:3]
        title = runs[0]["prompt"]
        card = page.locator("article.todo-card").filter(has=page.get_by_role("button", name=title, exact=True))
        column = lambda status: page.locator(f'section[aria-labelledby="todo-column-{status}"]')
        quick = lambda label: page.get_by_role("textbox", name=f"快速添加到{label}")
        selector = lambda item: json.dumps([item["project_id"], item["id"]], separators=(",", ":"))
        expect(page.locator("article.todo-card")).to_have_count(2)
        project.select_option(first["project_id"])
        room.select_option(selector(first))
        expect(page.locator("article.todo-card")).to_have_count(1)
        page.get_by_role("button", name="群协作约定").click()
        dialog = page.get_by_role("dialog", name="群协作约定")
        expect(dialog).to_contain_text("逐条核对引用来源")
        expect(dialog).to_contain_text("接收：作者")
        shot(page, "02-room-standards")
        dialog.get_by_role("button", name="返回看板").click()

        quick("待办").fill("交付前复核")
        quick("待办").press("Enter")
        expect(column("todo")).to_contain_text("交付前复核")
        card.drag_to(column("review").locator(".todo-column-body"), target_position={"x": 35, "y": 30})
        expect(column("review")).to_contain_text(title)
        expect(card).to_contain_text("手动放置")
        assert runs[0]["status"] == "running"
        card.get_by_role("button", name=title, exact=True).click()
        editor = page.get_by_role("dialog", name="编辑任务")
        expect(editor).to_contain_text("来源任务尚未结束")
        expect(editor.get_by_role("button", name="归档卡片")).to_have_count(0)
        expect(editor.get_by_role("button", name="打开来源群聊")).to_be_visible()
        editor.get_by_role("button", name="取消", exact=True).click()
        shot(page, "03-desktop-board")
        # A real source checkpoint races with the human's board decision, then the user reloads.
        runs[0]["status"] = "complete"
        workbench.manager.save(runs[0])
        page.reload()
        expect(room).to_have_value(selector(first))
        expect(column("review")).to_contain_text(title)
        expect(card).to_contain_text("执行 · 已完成")
        card.get_by_role("button", name="恢复跟随").click()
        expect(column("done")).to_contain_text(title)
        expect(card).to_contain_text("跟随来源")
        card.get_by_role("button", name=title, exact=True).click()
        expect(editor.get_by_role("button", name="归档卡片")).to_be_visible()
        editor.get_by_role("button", name="取消", exact=True).click()

        project.select_option(second["project_id"])
        room.select_option(selector(second))
        expect(page.locator("article.todo-card")).to_have_count(1)
        expect(column("done")).to_contain_text(runs[1]["prompt"])
        page.get_by_role("button", name="打开群聊", exact=True).click()
        expect(page.get_by_label("打开项目群聊", exact=True)).to_have_value(second["id"])
        page.locator(".group-header-actions").get_by_role("button", name="任务看板").click()
        expect(room).to_have_value(selector(second))
        project.select_option(first["project_id"])
        room.select_option(selector(empty))
        expect(page.locator("article.todo-card")).to_have_count(0)
        expect(page.get_by_text("没有符合当前筛选条件的任务。")).to_be_visible()
        shot(page, "01-filtered-empty")
        page.get_by_role("button", name="+ 新建任务").click()
        form = page.get_by_role("dialog", name="新建任务")
        expect(form.get_by_label("群聊", exact=True)).to_have_value(empty["id"])
        form.get_by_label("标题").fill("检查印刷版式")
        form.get_by_role("button", name="保存", exact=True).click()
        expect(column("todo")).to_contain_text("检查印刷版式")
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_label("看板状态：检查印刷版式").select_option("blocked")
        expect(column("blocked")).to_be_visible()
        expect(column("blocked")).to_contain_text("检查印刷版式")
        page.reload()
        expect(page.get_by_label("看板状态：检查印刷版式")).to_have_value("blocked")
        expect(project.locator("option:checked")).to_have_text("产品手册")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shot(page, "04-phone-board")

        page.set_viewport_size({"width": 1440, "height": 900})
        for number in range(22):
            todo_board.create_task(todo_board.TaskCreate(
                title=f"历史交付 {number + 1:02d}", status="done",
                project_id=second["project_id"], room_id=second["id"], tags=["飞书"],
            ))
        navigation.get_by_role("button", name="项目群聊", exact=True).click()
        board_button.click()
        expect(project).to_have_value("")
        expect(room).to_have_value("")
        expect(column("done").get_by_role("button", name="展开其余 4 项已完成")).to_be_visible()

        # A bot job can start and finish between any two board reads.
        feishu_title = "飞书派发：汇总上线验收结果"
        bot_card = page.locator("article.todo-card").filter(has=page.get_by_role("button", name=feishu_title, exact=True))
        bot = checkpoint(second, feishu_title, "running", feishu=True)
        bot["status"] = "complete"
        workbench.manager.save(bot)
        expect(bot_card).to_have_count(1, timeout=12000)
        expect(bot_card).to_contain_text("执行 · 已完成")
        expect(bot_card.locator(".todo-source-badge")).to_have_text("飞书")
        expect(bot_card.get_by_role("button", name="查看成果")).to_have_count(1)
        expect(column("done").locator("article.todo-card")).to_have_count(20)
        expect(column("done").get_by_role("button", name="历史交付 01", exact=True)).to_have_count(0)
        page.get_by_role("button", name="展开其余 5 项已完成").click()
        expect(column("done").get_by_role("button", name="历史交付 01", exact=True)).to_have_count(1)
        page.get_by_role("button", name="收起多余已完成项").click()

        # Dropping above the shortened lane inserts beside the actual visible card.
        first_visible = column("done").locator("article.todo-card").first
        anchor_id = first_visible.get_attribute("data-task-id")
        manual_card = page.locator("article.todo-card").filter(has=page.get_by_role("button", name="检查印刷版式", exact=True))
        manual_id = manual_card.get_attribute("data-task-id")
        manual_card.drag_to(first_visible, target_position={"x": 30, "y": 5})
        expect(column("done")).to_contain_text("检查印刷版式")
        expect(page.get_by_role("button", name="收起多余已完成项")).to_be_visible()
        positions = {task["id"]: task["position"] for task in todo_board.list_tasks()}
        assert positions[manual_id] + 1 == positions[anchor_id]

        source = page.get_by_label("筛选来源")
        source.select_option("feishu")
        expect(page.locator("article.todo-card")).to_have_count(1)
        expect(bot_card).to_be_visible()
        shot(page, "05-feishu-desktop")
        page.reload()
        expect(source).to_have_value("feishu")
        expect(bot_card).to_be_visible()
        project.select_option(first["project_id"])
        expect(bot_card).to_have_count(0)
        expect(page.get_by_text("没有符合当前筛选条件的任务。")).to_be_visible()
        page.get_by_role("button", name="清除筛选").click()
        expect(project).to_have_value("")
        expect(source).to_have_value("")
        expect(bot_card).to_be_visible()
        source.select_option("feishu")
        project.select_option(first["project_id"])
        board_button.click()
        expect(project).to_have_value("")
        expect(source).to_have_value("")

        page.set_viewport_size({"width": 390, "height": 844})
        filters = page.get_by_role("button", name="筛选任务")
        filters.click()
        source.select_option("feishu")
        filters.click()
        expect(column("done")).to_be_visible()
        expect(bot_card).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shot(page, "06-feishu-phone")
        page.route(origin + "/api/todo", lambda route: route.fulfill(status=503, json={"detail": "测试：任务读取暂时失败"}), times=1)
        expect(page.get_by_text("测试：任务读取暂时失败")).to_be_visible(timeout=12000)
        expect(bot_card).to_be_visible()
        retry = page.get_by_role("button", name="重试读取任务")
        retry.click()
        expect(retry).to_have_count(0)

        filters.click()
        page.get_by_label("显示已完成").uncheck()
        expect(bot_card).to_have_count(0)
        page.get_by_role("button", name="清除筛选").click()
        expect(page.get_by_label("显示已完成")).to_be_checked()
        with page.expect_response(lambda response: response.url == origin + "/api/todo" and response.ok):
            page.get_by_role("button", name="刷新任务").click()
        page.set_viewport_size({"width": 1440, "height": 900})
        source.select_option("feishu")
        bot_card.get_by_role("button", name=feishu_title, exact=True).click()
        editor.get_by_role("button", name="归档卡片").click()
        expect(bot_card).to_have_count(0)
        assert bot["status"] == "complete"
        assert not errors, errors
        context.close()
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="modeldesk-todo-") as folder, socket.socket() as sock:
        app, rooms, runs = fixture(Path(folder))
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started, "Fixture server did not start"
            exercise(origin, rooms, runs, args.screenshots)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
    print("Task board passed: scoped navigation, role agreements, native drag, source sync, Feishu polling and filters, reload, and phone controls.")


if __name__ == "__main__":
    main()
