# ModelDesk 2.13.3 · Role Skills and native Muse

## What changed

The team editor previously hid Skills for Muse, and the backend rejected those bindings. Muse now accepts saved per-role Skills in both automatic group dispatch and direct member dispatch. Switching among LLM, Codex, Claude Code and Muse preserves compatible bindings; selecting an image model clears them and explains where to configure directing instructions instead. Skill-list errors are visible and missing selections can be removed.

Each Muse task freezes only its member’s selected Skill versions before queuing. The parent’s frozen snapshot is used for automatic work and its permitted revision. Short instructions are passed verbatim inside a role guidance boundary. Long sources and code-rendering packages are adapted by the saved group coordinator into a bounded native creative brief. The original task stays intact. The full source, hashes, adapted brief, preparing model and confirmed dispatch state remain inspectable. No upstream installer, hook, native plugin installation or added permissions are implied.

Preparation happens before native dispatch. A missing/corrupt snapshot, failed or truncated adaptation, or over-budget task stops before sending. Observing a previous request does not compile or send again; failed/uncertain child tasks retain the existing duplicate-send protection. Native reply boundaries and actual media collection remain separate evidence.

## Capability boundaries

| Role | Lemo Skill behavior |
| --- | --- |
| LLM | Director/writing/review instructions in that role’s context. |
| Codex | Generates animation source; ModelDesk’s bounded Chromium/FFmpeg adapter renders and collects MP4. |
| Claude Code | Uses the role instructions and its existing CLI tools; the platform’s automatic Lemo renderer is not enabled for this executor. CLI authentication is still required. |
| Muse | Receives adapted directing guidance and uses its own creative tools. It does not receive an installed copy of the upstream renderer. |
| Image model | Produces images from the directing role’s brief; does not execute Skills. |

This is a pinned integration of Lemo (`508848acb2b4930c4041fef4d594120da1525bff`), not a claim that every upstream style, dependency, music or voice backend is available in every Agent. Platform rendering supports self-contained Canvas/HTML, 1–120 seconds, 1280×720 at 24 fps; user-requested content, sound and motion still require acceptance checks.

## Verification

- Regression cases cover direct and automatic Muse bindings, parent snapshot forwarding, revision preservation, role switching, exact short instructions, bounded adaptation, failed preparation without native dispatch, hash checks, and prepared-versus-sent UI states.
- Browser inspection confirmed the checked Lemo Skill under a Muse member and retained it through a Codex → Muse switch.
- Existing real Lemo job `529d564a-7974-4322-89b0-0e136d830ed9` produced a 6-second MP4 (`cee440b6-23f7-4584-8197-3a59fd95c697`). Full file decoding was rechecked successfully. The job remains partial for content acceptance; rendering success is not a blanket quality pass.
- New group job `92f0e5c2-0318-4b9b-9b84-297303983599` ran GPT writer → Muse director with Lemo → Gemini reviewer. Native task `fdf02c5b-cef4-4e19-8a80-80a63bf28e84` froze the pinned Skill, adapted 39119 source characters into 4602 characters with `gpt-6.1-sol`, and obtained a matching native send/reply receipt. Muse returned actual MP4 asset `e146881c-60c7-4588-af71-35d5ebd6f08c`: 6.0 seconds, 1280×720, H.264, 30 fps/180 frames, no audio stream, 320435 bytes. Full FFmpeg decoding passed; browser playback reached `ended=true` at 6.0 seconds. Downloaded bytes match SHA256 `4cfe54d5bf1689ae4ea17a5a8bb4cdd2c7045c9b1e2d270a4cc6c69cce963d94`.
- This confirms Skill guidance delivery, real native generation and media return. The automatic job still reports partial acceptance because its designated review role did not close the actual-file review loop. Continuous visual quality is not established by three extracted frames; the limitation is retained, not relabeled as full success.
- Automated verification: 393 backend tests, 147 frontend tests, 19 desktop tests, and frontend lint passed. The actual signed macOS bundle is rebuilt by the Git completion workflow.

## Operation

Install/import in **Agent 与连接 → Skills**. In a project group choose **管理成员 → 成员与分工**, select the member type and Skill, then **保存变更 → 应用到当前群聊**. Use automatic completion or dispatch that saved member. Muse’s task card shows **本次 Skills**, its exact prepared guidance and whether native delivery was confirmed. The standalone Muse panel without a selected member does not inherit a role’s Skills.
