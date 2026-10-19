# ModelDesk 2.13 inspection and fixes

This inspection covers role routing, task context, native Muse handoff, actual media delivery, Skills configuration, frontend integration, and packaging. It does not certify every upstream model or third-party application capability.

## Findings and changes

| Finding | Change | Evidence boundary |
| --- | --- | --- |
| Saved image models entered the text discussion path; the media stage did not honor the role's model. | Resolve legacy image roles by capability; use the exact selected image model; exclude image models from the Ruler selector. | Existing local gateway alias `gpt-image-2.5` returned a decoded, stored PNG. Alias availability is connection-specific. |
| Image outputs were referenced only as local URLs that external Agents could not read. | Add image-to-video dependency edges; copy scoped, hash-verified image bytes into Codex inputs or upload them through Muse's native attachment control. | Muse actually described the attached blue paper boat and cream background. Codex received the generated image in its isolated workspace. |
| Muse could finish a text reply while the video attachment was unavailable. | Distinguish reply completion from media delivery; recognize `Video not found`; allow re-collection from the same confirmed conversation without sending again. | Four actual keyframe images recovered from the failing conversation. Its video remained unavailable; no successful video claim is made for that task. |
| Skills had no installation or role binding flow. | Add a Skills library and per-role selection. Install Lemo's pinned instructions and style references; import standalone SKILL.md files. | Skill hashes and references are frozen per run; installer does not execute upstream setup/hooks. |
| A coding Agent could create animation sources but no renderer collected an MP4. | Add a bounded offline Canvas renderer using Lemo's render(t) interface, Chromium, and fixed FFmpeg commands. | Actual 6-second, 1280×720 MP4 files generated. Full decoding inspected all 144 frames; one video also played through to its end in the UI. |
| Creative sources hit the code patch size limit or merge approval gate. | Freeze creative sources separately; keep supplied images and Skill references immutable. Creative output is not a mergeable repository patch. | Regression covers source files larger than 1 MB and rejects modifications to frozen inputs. The existing source was recovered and rendered without another model generation. |
| Verification frames appeared as unrequested deliverable images. | Store frame samples as evidence separately from requested outputs. | Media collection excludes skill-frame slots from delivery counts and the final gallery. |
| Runtime workers could re-read unrelated recent group context. | Automatic children use the parent's frozen task and explicit upstream handoffs; selected Skills are frozen before execution. | Separate fresh-task context and resume behavior remain intact; no automatic memory deletion. |

The artifact panel leads with the actual document, images, and video. Detailed validation and execution evidence are expandable below it; a partial-result banner remains visible.

## Configuring Skills

Open **Agent 与连接 → Skills 能力库**, install Lemo or import `SKILL.md`, then open **项目 Agent → 成员与分工**, check the desired Skill, save, and sync the group. Use **自动完成** in the project group. The Ruler remains a reasoning LLM; the image role and executable Agent have distinct capabilities.

Lemo is pinned at `508848acb2b4930c4041fef4d594120da1525bff` from https://github.com/lemomo-ai/lemo-opuscar. Its code and documentation use MIT; upstream demo media are not copied into this repository or shipped as generated output. Installation reads directorial and style guidance. Generated agents do not run the upstream installer.

Current adapter: Codex + self-contained Canvas/HTML, 1–120 seconds, 1280×720, 24fps. Optional Chinese narration uses the Mac system voice. It can animate supplied images; it is not a photorealistic generative-video engine. Text LLMs can use the instructions, while Claude Code does not currently use the automatic video-rendering adapter. Custom workflow templates explicitly reject Skill-bound teams and direct users to project groups.

## Validation and remaining boundaries

Run `.venv/bin/python scripts/verify_modeldesk.py` for backend, frontend, lint, and desktop routing checks. Build the real macOS app with `.venv/bin/python scripts/package_modeldesk.py`; it verifies the bundle signature. The user's existing local UI edits are separately merged and checked, and are not included in this release's commit.

Real execution covered exact-model image generation, native image attachment to Muse, pinned Skill installation, Codex animation source generation, file handoff, offline MP4 rendering, and actual player playback. Muse's unavailable old video cannot be reconstructed from a text response. Full decoding and sampled-frame review are not equivalent to human evaluation of storytelling, narration accuracy, or every visual transition. Missing requested media continues to produce a partial result instead of success.

The platform remains a local, single-backend process. Native Muse interaction requires an unlocked Mac and the relevant conversation. Skills do not grant new tools, credentials, network permissions, or access to other projects. Third-party cloud generation is not guaranteed to succeed by the local harness.
