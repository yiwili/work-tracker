---
name: awt-daily-worklog
description: Operate AI Work Tracker daily conversation collection, relevance filtering and business-level work-item analysis through its local API. Use for this project's daily worklog requests, not unrelated software work.
---

Use the service at `http://127.0.0.1:8765` and the date in the user's configured timezone. Ask for a date only when it cannot be inferred. Start the existing service with the repository's `start.cmd` if needed; it reuses an already-running instance.

Read `GET /api/conversations?date=YYYY-MM-DD` first. Report conversation/session counts separately from work-item counts. A prompt, retry, technical cause, file, tool invocation or Git commit is not automatically a separate work item.

For an authorized collection request, use `POST /api/collect?date=...` with JSON `{}`. For an authorized AI analysis request, verify `/api/settings` has `llm_enabled=true`, then use `POST /api/analyze?date=...` with JSON `{}`. If model configuration is absent, point to the app's settings; do not print credentials, read secret stores, enable a provider or change external accounts on the user's behalf merely to bypass the missing configuration.

The application runs work-relevance filtering before business-goal consolidation. Private, unrelated and uncertain turns cannot provide automatic work timing or task evidence. Titles should name the business module and issue/feature; keep database types, exception classes and implementation mechanics in details. Preserve true outcomes and verification limits. Do not add achievements to make a day appear busier.

Read the report after analysis. Surface model warnings and excluded/uncertain counts. Existing human edits and pinned minutes are preserved. Do not repeatedly issue paid model calls after a failure; report the concrete cause, use local statistics, and retry only when the user requests it or a corrected configuration makes a retry warranted.

CLI alternatives from the repository: `.venv/Scripts/python.exe -m awt.cli conversations --date ...`, `collect --date ...`, `analyze --date ...`, `report --date ... --format markdown`. The CLI writes only through the same service logic. Do not confirm an 8h report without an explicit user review/confirmation request.
