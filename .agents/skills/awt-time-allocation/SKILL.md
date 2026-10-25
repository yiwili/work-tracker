---
name: awt-time-allocation
description: Estimate and allocate AI Work Tracker daily work-item minutes to the configured target while preserving pinned human time. Use when the user asks this project to estimate hours or distribute a day, not for unrelated time estimates.
---

Read `GET http://127.0.0.1:8765/api/reports/YYYY-MM-DD` and the configured target first. Identify pinned minutes (`time_locked=true`), unlocked work items, missing work evidence and an already-confirmed report.

For a requested estimate/allocation, call `POST /api/reports/YYYY-MM-DD/estimate-time` with JSON `{}`. The UI opens a modal first. Optional explicitly supplied manual activities use `{"request_id":"unique-batch-id","manual_items":[{"category":"沟通","name":"附件挂接需求","minutes":60}]}`; category is 沟通 or 运维. The model names those entries using historical style, preserves their minutes, then assesses existing eligible work using difficulty, scope and timing references. New manual entries and allocations save atomically as a draft. Reuse the same request ID and body when retrying a failed response to prevent duplicates. Never create manual activities the user did not supply. The endpoint does not confirm the report.

Manual meetings or customer communication can be entered with `POST /api/tasks`. Preserve user-specified minutes by setting `time_locked=true`. Editing minutes in the UI pins them by default. Do not unlock, reduce or move manual time to make the arithmetic fit without user authorization. If fixed time exceeds the target, there are no eligible items, or all time is fixed below the target, explain the actual constraint; do not fabricate work.

Log timing is a reference signal: session span includes idle/waiting; active intervals omit long gaps and union parallel activity; tool runtime is machine execution. Personal/unrelated turns cannot contribute automatic work timing. Never label a target allocation as measured human effort.

AI allocations and independent suggestions use 30-minute (0.5h) units only, irrespective of the manual editor's increment setting. Preserve human fixed minutes; if the remaining target is not divisible by 30, explain the conflict before any model request and ask the user to adjust their values. Never add residual minutes, silently round human time, or fabricate work to satisfy the rule. Independent estimates are rounded up to the next half-hour for display.

After allocation, show pinned time, distributed time, total, independent estimates and warnings. Mark AI-filled minutes as suggestions awaiting human review. If the target differs substantially from the independent estimate, preserve that warning. A total of 480 only satisfies the arithmetic boundary; `POST /api/reports/date/confirm` requires the user's explicit confirmation intent.

If work items or minutes changed while the model was running, the application rejects stale output. Re-read and explain the preserved edits before any warranted retry. CLI alternative: `.venv/Scripts/python.exe -m awt.cli estimate --date YYYY-MM-DD`.
