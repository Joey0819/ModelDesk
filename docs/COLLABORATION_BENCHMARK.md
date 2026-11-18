# ModelDesk: single model vs. automatic team delivery

This opt-in benchmark runs ten paired tasks: grounded Chinese research and product writing, table/report formatting, a small React code review, and a bounded Python fix. Both arms get the same synthetic source facts and output requirements in fresh conversations. The single arm uses Workbench with `gpt-6.1-sol`. The automatic arm uses a saved project and team, submits through the room’s automatic delivery endpoint, and scores the actual `group:delivery` export. That team uses `gpt-6.1-sol` for author and coordinator and `gemini-3-flash` for review, via the installed gateway. There are no attachments, external tools, connectors, or publishing in either arm. Both arms use the same Markdown rubric and reporting layout, and both read back the saved HTML export. The automatic score also requires a complete product delivery.

From the repository root, with the backend virtualenv and the installed ModelDesk service running:

```sh
run=$(uuidgen | tr '[:upper:]' '[:lower:]')
.venv/bin/python scripts/benchmark_automatic_collaboration.py --live --run-id "$run"
.venv/bin/python scripts/score_automatic_collaboration_benchmark.py --run-id "$run"
```

Omit `--live` to inspect the run plan without creating jobs. Reuse the run ID to resume an interrupted invocation; do not start another run to recover an uncertain submission. The runner sends jobs sequentially and waits for the relevant queue to be idle before admission. Raw prompts, responses, receipts, and downloaded artifacts live in ignored `data/collaboration-benchmark/` directories. To measure a separate recovery pass, rerun the first command with `--recover` and the second with `--with-recovery`. Recovery issues at most one continuation per eligible original job and keeps the first-pass evidence intact.

Observed on November 18, 2026 (Shanghai time), on the installed loopback service:

| Arm | Product delivery status | Independent content checks passed | Completed and verified | Median elapsed |
| --- | --- | ---: | ---: | ---: |
| Single, first pass | 10 responses | 10/10 | 10/10 | 8.7 s |
| Automatic, first pass | 1 complete, 5 partial, 4 blocked | 6/10 | 1/10 | 53.6 s |
| Automatic, after one continuation per incomplete case | 8 complete, 2 partial | 9/10 | 8/10 | 32.3 s per continuation (9 jobs) |

The first automatic pass downloaded and verified six nonempty HTML exports. After one operator-issued continuation for each of the nine incomplete jobs, all ten HTML exports downloaded and matched the saved body. This took place after an API update for transient HTTP failures and titleless final paragraphs. Eight first-pass cases surfaced HTTP 500 at a model step; none of the continued cases ended with HTTP 500 as a current step error. First-pass automatic delivery was slower in all ten pairs (median paired increase 45.4 s). First-pass timings are server job creation to last update; the continuation median measures request to terminal state and excludes time between the passes. No human edited or approved the contents after admission. First-pass receipts record 10 single and 17 team model turns; total provider requests, tokens, and billed cost were unavailable.

The last two partial deliveries illustrate different problems. `asset-inventory` passed the scripted checks and all its member steps finished on continuation. Its final assembly encountered HTTP 500 again. The same saved version and its earlier review remained available. ModelDesk now keeps that earlier verdict in history when a new assembly fails. `cohort-table` put the correct IDF1 review scope in the source cell. Manual inspection found the figures and scope correct; the product reviewer and the strict table-cell check rejected that placement even though the task did not specify a cell for the qualifier. The table above retains the strict score.

The deterministic checks look for concrete source details, forbidden filler and unsupported claims, valid table cells, and expected code behavior on synthetic cases. They are not an editorial assessment and can miss an unsupported claim or reject valid phrasing. The rubric was calibrated on an exploratory Workbench discussion pilot and is not held out. The baseline ran earlier that day, and the recovered team used an updated service; these ten cases demonstrate delivery reliability under the observed conditions, not a causal estimate of a team’s writing advantage. The benchmark does not exercise native media or visual layout. To rescore the captured automatic run locally, use run ID `51298a79-5028-43cb-8fc3-1878dd99bc7d`, with or without `--with-recovery`; it resolves its recorded single baseline automatically. The exploratory Workbench discussion is a separate path and is not included in these results.
