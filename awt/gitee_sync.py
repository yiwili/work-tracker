"""Gitee unfinished work items sync, deduplication and enrichment.

Fetches remote open/unfinished issues from Gitee Enterprise and V5,
matches them against local daily tasks using awt.matching,
prioritizes cloud titles and links while preserving local time and content,
and allows user decisions for ambiguous matches.
"""
from __future__ import annotations

import logging
from typing import Any
from sqlalchemy import select, update, insert, and_
from . import db, gitee, gitee_enterprise, matching
from .collectors import stable

logger = logging.getLogger("awt.gitee_sync")


def fetch_all_unfinished(engine) -> list[dict[str, Any]]:
    """Fetch remote unfinished issues from configured Gitee services.

    Performs network requests outside of any database transaction.
    """
    from .service import get_settings

    with engine.connect() as conn:
        settings = get_settings(conn)

    remote_issues: list[dict[str, Any]] = []

    # 1. Gitee Enterprise
    ent_config = settings.gitee_enterprise
    if ent_config.enterprise_id and gitee_enterprise.token():
        try:
            ent_items = gitee_enterprise.unfinished_issues(ent_config.enterprise_id)
            remote_issues.extend(ent_items)
        except Exception as exc:
            logger.warning("Failed to fetch Gitee Enterprise unfinished issues: %s", exc)

    # 2. Gitee V5 fallback (only if enterprise is not configured)
    v5_config = settings.gitee
    if not ent_config.enterprise_id and gitee.token():
        try:
            v5_items = gitee.fetch_unfinished(v5_config)
            remote_issues.extend(v5_items)
        except Exception as exc:
            logger.warning("Failed to fetch Gitee V5 unfinished issues: %s", exc)

    return remote_issues


def sync_gitee_tasks(engine, day: str) -> dict[str, Any]:
    """Sync remote unfinished Gitee work items with local daily tasks.

    - Auto-passes matches with confidence >= threshold_high (cloud title + local hours/content)
    - Returns ambiguous matches (threshold_low <= confidence < threshold_high) for user review
    - Discards matches with confidence < threshold_low
    """
    from .service import get_settings, list_tasks, invalidate, now

    # 1. Read settings
    with engine.connect() as conn:
        settings = get_settings(conn)
        dedup_cfg = settings.gitee_dedup

    # 2. Fetch remote unfinished issues (NO database lock held during network calls)
    remote_issues = fetch_all_unfinished(engine)

    # 3. Process matches in transaction
    with engine.begin() as conn:
        # Gitee 中的未完成项不影响本地 AI 生成的项（origin == 'auto'）。
        local_tasks = [t for t in list_tasks(conn, day) if t.get("origin") != "auto"]
        match_result = matching.match_tasks_with_remote(
            local_tasks,
            remote_issues,
            threshold_high=dedup_cfg.threshold_high,
            threshold_low=dedup_cfg.threshold_low,
            date_weight=dedup_cfg.date_weight,
        )

        auto_applied = []
        for match in match_result["auto_matched"]:
            local_t = match["local_task"]
            remote_i = match["remote_issue"]
            conf = match["confidence"]

            merged = matching.merge_local_into_remote(local_t, remote_i)
            task_id = local_t["id"]

            # Update task with cloud title, project, origin, etc.
            conn.execute(
                update(db.tasks)
                .where(db.tasks.c.id == task_id)
                .values(
                    title=merged["title"],
                    project=merged["project"],
                    origin="gitee",
                    edited=True,
                )
            )

            # Record link if Gitee Enterprise
            if remote_i.get("source") == "gitee_enterprise" and settings.gitee_enterprise.enterprise_id:
                ent_id = settings.gitee_enterprise.enterprise_id
                ident = stable(ent_id, task_id)
                link_record = {
                    "id": ident,
                    "enterprise_id": ent_id,
                    "task_id": task_id,
                    "program_id": remote_i.get("program_id") or 0,
                    "status": "created",
                    "issue_id": str(remote_i.get("issue_id") or ""),
                    "url": remote_i.get("url") or "",
                    "error": "",
                    "updated_at": now(),
                }
                from .service import upsert
                upsert(conn, db.gitee_links, link_record, "id")

            auto_applied.append({
                "task_id": task_id,
                "remote_issue_id": remote_i.get("id"),
                "title": merged["title"],
                "previous_title": local_t["title"],
                "confirmed_minutes": local_t.get("confirmed_minutes", 0),
                "confidence": conf,
                "explanation": match.get("explanation"),
            })

        invalidate(conn, day)

    return {
        "date": str(day),
        "total_remote": len(remote_issues),
        "auto_matched": auto_applied,
        "ambiguous": match_result["ambiguous"],
        "discarded_count": len(match_result["discarded"]),
        "unmatched_local_count": len(match_result["unmatched_local"]),
    }


def apply_user_decisions(engine, day: str, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply user decisions for ambiguous matches.

    Each decision:
    - remote_issue_id: str
    - action: 'match' | 'create' | 'ignore'
    - target_task_id: str | None (required if action == 'match')
    """
    from .service import get_settings, list_tasks, invalidate, now, upsert
    from uuid import uuid4

    # Fetch remote unfinished issues outside lock
    remote_issues = {str(r.get("id")): r for r in fetch_all_unfinished(engine)}

    applied = []
    with engine.begin() as conn:
        settings = get_settings(conn)
        local_tasks = {t["id"]: t for t in list_tasks(conn, day)}

        for dec in decisions:
            r_id = str(dec.get("remote_issue_id"))
            action = dec.get("action")
            target_id = dec.get("target_task_id")

            remote_i = remote_issues.get(r_id)
            if not remote_i:
                continue

            if action == "match" and target_id and target_id in local_tasks:
                local_t = local_tasks[target_id]
                if local_t.get("origin") == "auto":
                    continue
                merged = matching.merge_local_into_remote(local_t, remote_i)

                conn.execute(
                    update(db.tasks)
                    .where(db.tasks.c.id == target_id)
                    .values(
                        title=merged["title"],
                        project=merged["project"],
                        origin="gitee",
                        edited=True,
                    )
                )

                if remote_i.get("source") == "gitee_enterprise" and settings.gitee_enterprise.enterprise_id:
                    ent_id = settings.gitee_enterprise.enterprise_id
                    ident = stable(ent_id, target_id)
                    link_record = {
                        "id": ident,
                        "enterprise_id": ent_id,
                        "task_id": target_id,
                        "program_id": remote_i.get("program_id") or 0,
                        "status": "created",
                        "issue_id": str(remote_i.get("issue_id") or ""),
                        "url": remote_i.get("url") or "",
                        "error": "",
                        "updated_at": now(),
                    }
                    upsert(conn, db.gitee_links, link_record, "id")

                applied.append({
                    "action": "matched",
                    "task_id": target_id,
                    "title": merged["title"],
                    "confirmed_minutes": local_t.get("confirmed_minutes", 0),
                })

            elif action == "create":
                new_task_id = uuid4().hex
                proj = remote_i.get("project") or remote_i.get("program") or "未归属项目"
                if isinstance(proj, dict):
                    proj = proj.get("name") or "未归属项目"

                conn.execute(
                    insert(db.tasks).values(
                        id=new_task_id,
                        date=str(day),
                        title=remote_i.get("title") or "未命名工作项",
                        details=remote_i.get("description") or "",
                        project=proj,
                        category="开发",
                        status="requested",
                        origin="gitee",
                        confidence="云端同步",
                        evidence_level="云端工作项",
                        edited=True,
                        deleted=False,
                    )
                )
                conn.execute(
                    insert(db.worklogs).values(
                        task_id=new_task_id,
                        confirmed_minutes=0,
                        suggested_minutes=None,
                        time_locked=False,
                        time_source="manual",
                        is_confirmed=False,
                    )
                )

                if remote_i.get("source") == "gitee_enterprise" and settings.gitee_enterprise.enterprise_id:
                    ent_id = settings.gitee_enterprise.enterprise_id
                    ident = stable(ent_id, new_task_id)
                    link_record = {
                        "id": ident,
                        "enterprise_id": ent_id,
                        "task_id": new_task_id,
                        "program_id": remote_i.get("program_id") or 0,
                        "status": "created",
                        "issue_id": str(remote_i.get("issue_id") or ""),
                        "url": remote_i.get("url") or "",
                        "error": "",
                        "updated_at": now(),
                    }
                    upsert(conn, db.gitee_links, link_record, "id")

                applied.append({
                    "action": "created",
                    "task_id": new_task_id,
                    "title": remote_i.get("title"),
                    "confirmed_minutes": 0,
                })

        invalidate(conn, day)

    return {"date": str(day), "applied": applied}
