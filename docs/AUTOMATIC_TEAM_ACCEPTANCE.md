# Automatic team acceptance · 2.11.0

Verified locally on 2026-10-18 using connected model accounts and the native Muse application.

## Real delivery paths

| Scenario | Actual result | Evidence |
| --- | --- | --- |
| Illustrated story | A complete Chinese story plus a generated 1200 × 896 image | Image bytes and SHA-256 verified; an independent model received the actual thumbnail |
| Writer → Muse → reviewer → editor | A new 10-second, 1280 × 720 MP4 and its shot description | Original 4,250,606-byte video stored, checksum verified, and playback observed in the artifact panel |
| Review and final copy | Two artifact versions and two final reviews | The second version removed resolved pending-check language; all four task requirements passed |
| Portable delivery | Article and video ZIP bundles | Manifest, Markdown checksum, and every included media checksum verified |
| Continue an existing delivery | Illustrated-story continuation completed | Completed steps and the existing image were reused rather than regenerated |

The video review inspected frames at 1, 5 and 9 seconds. This is **not** audio analysis, continuous-motion validation, or full-film review. The UI and final output state that scope.

## Controlled regression coverage

Tests cover dependency ordering, saved runtime roles, actual image inputs, media ownership and checksums, bounded video sampling, and one linked Muse correction. The correction tests check reattachment, the one-correction budget, remote restrictions, and refusal to resend uncertain original tasks. A live correction was not forced during the successful video run.

Existing local UI edits were overlaid for compatibility checks and preserved separately from this release's commit. Automated suites and packaging are run by `scripts/verify_modeldesk.py` and `scripts/package_modeldesk.py` before the release commit.

## Operational requirements

- Automatic work uses the group's saved members and the selected coordinating model.
- Muse needs an unlocked Mac, a usable chat window, and the existing native bridge permissions. Muse 8's ordinary Chats button is supported alongside the older popup button.
- A provider/model must support the requested operation and be connected. A text-only model does not gain image or video generation merely by joining a group.
- Code changes retain their existing review and merge gates. Failed or uncertain native sends do not automatically retry.
