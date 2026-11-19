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
    runs = []
    for room, prompt, status in [(rooms[0], "复核产品手册的引用来源", "running"), (rooms[2], "整理官网更新说明", "complete")]:
        stamp = workbench.now()
        job = {"id": str(uuid4()), "project_id": room["project_id"], "group_room_id": room["id"], "conversation_id": room["id"],
               "mode": "group", "status": status, "prompt": prompt, "created_at": stamp, "updated_at": stamp, "events": [], "models": []}
        workbench.manager.jobs[job["id"]] = job
        workbench.manager.save(job)
        runs.append(job)

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
        page.locator("nav").get_by_role("button", name="任务看板 待办 / 进行中 / 已完成").click()
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
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shot(page, "04-phone-board")
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
    print("Task board passed: scoped group navigation, role agreements, native drag, source sync, reload, phone status and creation.")


if __name__ == "__main__":
    main()
