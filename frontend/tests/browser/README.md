# Browser navigation checks

The checks serve the real ModelDesk frontend on an ephemeral loopback port with fixture data. The server rejects API writes, and the page's content security policy keeps its requests on the fixture origin. The tests use a fresh browser profile.

## Bombadil

From the repository root, use [Bombadil 0.7.10](https://github.com/antithesishq/bombadil/releases/tag/v0.7.10).

```sh
node frontend/tests/browser/run-bombadil.mjs
node frontend/tests/browser/run-bombadil.mjs --mobile
```

Set `BOMBADIL_BIN` if the binary is outside `PATH`. Each run explores navigation for 30 seconds, stops on a violation, and prints the directory containing its trace and screenshots.

## Repeatable browser regression

With Chrome installed, use the Playwright runtime from the project's locked Python environment.

```sh
export PLAYWRIGHT_MODULE
PLAYWRIGHT_MODULE="$(.venv/bin/python -c 'from pathlib import Path; import playwright; print((Path(playwright.__file__).parent / "driver/package/index.mjs").as_uri())')"
node frontend/tests/browser/check-navigation.mjs
node frontend/tests/browser/check-platform-catalogs.mjs
node frontend/tests/browser/check-model-picker-refresh.mjs
node frontend/tests/browser/check-conversation-actions.mjs
node frontend/tests/browser/check-group-dispatch.mjs
node frontend/tests/browser/check-group-routines.mjs
node frontend/tests/browser/check-group-routine-loops.mjs
node frontend/tests/browser/check-muse-panel-dispatch.mjs
npm --prefix frontend run build
.venv/bin/python frontend/tests/browser/check-todo-board.py
```

Set `CHROMIUM_EXECUTABLE` to use a specific Chromium executable. Set
`SCREENSHOT_DIR` to save desktop and mobile screenshots. The check covers 96
seeded navigation transitions, keyboard search, dialog focus, opening results
in an empty session and returning from the phone task board. Linux product
smoke runs this check on every push and pull request.

The catalog check delays two MCP servers and verifies that retries preserve a draft and that results return to the correct server. The conversation check opens account controls and downloads Markdown and JSON at laptop and phone widths.

The model picker check refreshes and selects a new Ruler model while an older poll is pending. It checks failure feedback for both source types and confirms paired phones only read the host catalog.

The dispatch checks simulate a lost reply after an Agent accepts work. They cover the group chat, code review form and Muse panel. Reloading or returning from a different project or room must recover the same task and preserve any edits. All submissions in these browser checks go to an isolated test adapter.

The group routines check opens the production React dialog on desktop and phone. It exercises draft import, saving a routine, enabling it, and recovering the original run when the server accepts a request but the response is lost.

The task-board check runs the production build against disposable, disk-backed project, room and board stores. It drags a synced card, advances its source, reloads the page, and checks scoped creation, role agreements and phone controls. Pass `--screenshots DIR` to capture these views.
