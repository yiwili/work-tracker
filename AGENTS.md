# AI Work Tracker project agents

This repository provides three reusable skills under `.agents/skills/`:

- `awt-daily-worklog`: collect/read daily conversations and generate business-level work items with relevance filtering.
- `awt-time-allocation`: estimate and distribute target minutes while preserving pinned human entries.
- `awt-style-prompts`: import historical records and maintain naming templates and the five role prompts.

Read the matching `SKILL.md` when asked to operate that workflow. Use the local API or the installed `awt` CLI, rather than manipulating SQLite directly. The API listens on `127.0.0.1:8765`; model credentials stay on the service side.

Implementation boundaries:

- `collectors.py`: read-only, version-tolerant adapters for Codex / Claude Code / WorkBuddy (CN + international) / DeepSeek Harness (append-only zstd frames) / Qoder (CN + international) / Trae. Trae stores full transcripts in encrypted SQLite, so only its plaintext session-memory summaries are ingested; never present them as raw conversation or invent user text.
- `awt/prompts/*.md`: default role instructions; `Settings.agent_prompts`: user supplements; evidence/JSON/minute constraints stay enforced in code.
- `work_scope.py`: classify work vs personal/unrelated/uncertain turns before task generation or work timing.
- `daily_analysis.py`: consolidate actual business goals; historical examples are style references, never today's evidence.
- `activity_time.py`: log-based spans and active-interval proxies; never equate them with exact human effort.
- `git_tools.py`: collect only the selected commit author (repository override > custom default > local/inherited Git identity). Match email exactly when present, otherwise the full name; unknown identity never falls back to every author. Apply the same filter to legacy events, AI input and Git naming references while preserving reviewed tasks and human time.
- `time_allocation.py`: independent difficulty estimates and exact integer target apportionment; may add only user-supplied quick manual activities, preserving their minutes atomically. No automatic report confirmation.
- `style_reference.py`: JSON/CSV/Excel history in SQLite, deduplication and reusable naming template generation. History never becomes today's evidence.
- `gitee.py`: read-only V5 Issues imports with optional periodic synchronization while the service runs. Use only explicitly supplied Gitee credentials.
- `gitee_enterprise.py`: enterprise project discovery and user-configured title/prefix mapping; creates work items only after a saved preview and explicit confirmation. Do not create real company work items during automated verification. Persist links to skip duplicates; uncertain writes must not be automatically retried. Enterprise credentials are separate from V5 history credentials.

Preserve manual entries, pinned minutes, reviewed titles, delete decisions, original logs and repositories. Network model waits must not hold a SQLite writer lock. Re-read current state before applying AI output so concurrent human edits are not overwritten. Run focused pytest cases and the affected browser workflow when changing these boundaries.

Commit each feature separately using standard Conventional Commit messages (for example `feat: ...`, `fix: ...`); do not copy the user's Gitee double-tag commit naming. Before a user-authorized push, verify the destination host is GitHub. GitHub push authorization does not authorize pushes to any Gitee repository or automatic company work-item creation.
