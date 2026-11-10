"""Similarity detection and AI merge recommendation between human and AI work items."""
import hashlib
import json
import logging
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db, model_provider
from .matching import calculate_content_similarity, extract_prefix_and_content
from .service import get_task, get_settings, invalidate, list_tasks, now, upsert

logger = logging.getLogger("awt.similarity")


def compute_tasks_signature(tasks: list[dict[str, Any]]) -> str:
    """Compute a deterministic title-only hash of active work items for change detection."""
    active = [t for t in tasks if not t.get("deleted")]
    items = sorted([
        (
            str(t.get("id") or ""),
            str(t.get("title") or "").strip(),
        )
        for t in active
    ])
    payload = json.dumps(items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_baseline_titles(tasks: list[dict[str, Any]]) -> dict[str, str]:
    """Return mapping of active task IDs to their stripped titles."""
    return {
        str(t.get("id") or ""): str(t.get("title") or "").strip()
        for t in tasks
        if not t.get("deleted") and t.get("id")
    }


def mark_titles_modified(conn: Any, day: date | str) -> None:
    """Mark similarity state for day as having modified titles."""
    state = get_similarity_state(conn, day)
    if state:
        state = {**state, "titles_modified": True}
        save_similarity_state(conn, day, state)


def calculate_task_pair_similarity(manual_task: dict[str, Any], ai_task: dict[str, Any]) -> tuple[float, str]:
    """Calculate title-only similarity score between a manual task and an AI task.

    Returns (similarity_score, reason).
    """
    m_title = str(manual_task.get("title") or "").strip()
    a_title = str(ai_task.get("title") or "").strip()
    if not m_title or not a_title:
        return 0.0, ""

    # 1. Content similarity on raw titles
    sim_raw, _, _, _ = calculate_content_similarity(m_title, a_title)

    # 2. Content similarity stripped of bracket prefixes
    p1, c1 = extract_prefix_and_content(manual_task)
    p2, c2 = extract_prefix_and_content(ai_task)
    sim_content, _, _, _ = calculate_content_similarity(c1, c2)

    # 3. SequenceMatcher ratios
    ratio_raw = SequenceMatcher(None, m_title, a_title).ratio()
    ratio_content = SequenceMatcher(None, c1, c2).ratio()

    score = max(sim_raw, sim_content, ratio_raw, ratio_content)

    # 4. Substring containment check for sufficiently long titles
    shorter, longer = (c1, c2) if len(c1) <= len(c2) else (c2, c1)
    if len(shorter) >= 4 and shorter.lower() in longer.lower():
        score = max(score, 0.75)

    reason = f"工作项标题高度重复（标题相似度 {int(round(score * 100))}%）"
    return round(score, 3), reason


def detect_duplicates(
    tasks: list[dict[str, Any]],
    threshold: float = 0.70,
    dismissed_pairs: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Find all pairs of (manual_task, ai_task) with similarity score >= threshold."""
    dismissed = dismissed_pairs or set()
    active = [t for t in tasks if not t.get("deleted")]
    manual_tasks = [t for t in active if t.get("origin") == "manual"]
    ai_tasks = [t for t in active if t.get("origin") != "manual"]

    if not manual_tasks or not ai_tasks:
        return []

    duplicates = []
    for m in manual_tasks:
        for a in ai_tasks:
            pair_id = f"{m['id']}_{a['id']}"
            if pair_id in dismissed:
                continue
            score, reason = calculate_task_pair_similarity(m, a)
            if score >= threshold:
                duplicates.append({
                    "pair_id": pair_id,
                    "manual_task": m,
                    "ai_task": a,
                    "similarity_score": score,
                    "reason": reason,
                })
    return duplicates


def synthesize_merge_recommendation(
    settings: Any,
    manual_task: dict[str, Any],
    ai_task: dict[str, Any],
    similarity_score: float,
) -> dict[str, Any]:
    """Generate merge recommendation for a duplicate pair, using LLM if available or rules."""
    m_title = str(manual_task.get("title") or "").strip()
    a_title = str(ai_task.get("title") or "").strip()
    m_details = str(manual_task.get("details") or "").strip()
    a_details = str(ai_task.get("details") or "").strip()
    m_proj = str(manual_task.get("project") or "").strip()
    a_proj = str(ai_task.get("project") or "").strip()
    m_cat = str(manual_task.get("category") or "").strip()
    a_cat = str(ai_task.get("category") or "").strip()
    m_mins = int(manual_task.get("confirmed_minutes") or 0)
    a_mins = int(ai_task.get("confirmed_minutes") or 0)

    # Project selection
    project = m_proj if m_proj and m_proj != "未归属项目" else (a_proj or "未归属项目")
    category = m_cat or a_cat or "开发"

    # Default rule-based title: prefer manual title as it's human curated
    title = m_title or a_title

    # Combined details
    details_parts = []
    if m_details:
        details_parts.append(f"【人工录入内容】\n{m_details}")
    if a_details:
        details_parts.append(f"【AI提取内容】\n{a_details}")
    details = "\n\n".join(details_parts)

    # Minutes: Preserve manual confirmed minutes atomically
    mins = m_mins if m_mins > 0 else a_mins
    status = manual_task.get("status") or ai_task.get("status") or "requested"

    # AI synthesis via LLM if available and enabled
    if settings and getattr(settings, "llm_enabled", False):
        try:
            system_prompt = (
                "你是一个工作项合并与整理专家。系统检测到用户的人工工作项与AI自动提取的工作项存在高度重复。\n"
                "请将人工工作项和AI工作项整合成一个精炼、准确的业务级工作项。\n"
                "要求：\n"
                "1. title: 精炼明确的业务标题，包含关键模块或功能，不超过100字。\n"
                "2. details: 整合双方的背景、工作内容及成果，保留重要技术细节和人工重点，条理清晰。\n"
                "3. 返回且仅返回标准JSON，格式为：{\"title\": \"...\", \"details\": \"...\"}\n"
            )
            user_input = json.dumps({
                "manual_task": {"title": m_title, "details": m_details, "project": m_proj},
                "ai_task": {"title": a_title, "details": a_details, "project": a_proj},
            }, ensure_ascii=False)
            response = model_provider.complete(
                settings, system_prompt, user_input, max_tokens=1000, purpose="task_merger"
            )
            clean_resp = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip())
            parsed = json.loads(clean_resp)
            if isinstance(parsed, dict) and parsed.get("title"):
                title = str(parsed["title"]).strip()
                if parsed.get("details"):
                    details = str(parsed["details"]).strip()
        except Exception as exc:
            logger.warning("AI merge recommendation generation failed, using rule-based fallback: %s", exc)

    return {
        "pair_id": f"{manual_task['id']}_{ai_task['id']}",
        "manual_task": manual_task,
        "ai_task": ai_task,
        "similarity_score": round(similarity_score, 3),
        "reason": f"工作项标题高度重复（标题相似度 {int(round(similarity_score * 100))}%）",
        "recommended_title": title,
        "recommended_details": details,
        "recommended_project": project,
        "recommended_category": category,
        "recommended_minutes": mins,
        "recommended_status": status,
    }


def get_similarity_state(conn: Any, day: date | str) -> dict[str, Any] | None:
    """Retrieve similarity check state from database."""
    day_str = str(day)
    return conn.execute(
        select(db.integrations.c.value).where(db.integrations.c.id == f"similarity-check:{day_str}")
    ).scalar_one_or_none()


def save_similarity_state(conn: Any, day: date | str, state: dict[str, Any]) -> None:
    """Save similarity check state to database."""
    day_str = str(day)
    upsert(conn, db.integrations, {"id": f"similarity-check:{day_str}", "value": state}, "id")


def get_similarity_status(conn: Any, day: date | str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Check whether similarity check is clean and whether tasks have changed."""
    day_str = str(day)
    current_sig = compute_tasks_signature(tasks)
    state = get_similarity_state(conn, day_str)

    if not state:
        return {
            "checked": False,
            "tasks_changed": True,
            "titles_modified": False,
            "handled": False,
            "has_duplicates": False,
            "pairs_count": 0,
            "tasks_signature": current_sig,
        }

    sig_changed = state.get("tasks_signature") != current_sig
    titles_modified = bool(state.get("titles_modified", False))
    tasks_changed = sig_changed or titles_modified
    handled = state.get("handled", False)
    has_duplicates = state.get("has_duplicates", False)
    pairs_count = state.get("pairs_count", 0)

    return {
        "checked": True,
        "tasks_changed": tasks_changed,
        "titles_modified": titles_modified,
        "handled": handled,
        "has_duplicates": has_duplicates,
        "pairs_count": pairs_count,
        "tasks_signature": current_sig,
    }


def check_day_similarity(
    conn: Any,
    day: date | str,
    settings: Any = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run similarity check for day.

    If unchanged and already handled, returns clean without re-checking.
    If force=True or tasks changed, executes check and returns recommendations.
    """
    day_str = str(day)
    settings = settings or get_settings(conn)
    tasks = list_tasks(conn, day_str)
    current_sig = compute_tasks_signature(tasks)
    state = get_similarity_state(conn, day_str)

    # If already checked, tasks unchanged, and user handled alerts, skip
    if (
        not force
        and state
        and state.get("tasks_signature") == current_sig
        and not state.get("titles_modified", False)
        and state.get("handled", False)
    ):
        return {
            "status": "clean",
            "tasks_changed": False,
            "has_duplicates": False,
            "pairs": [],
            "tasks_signature": current_sig,
        }

    dismissed = set(state.get("dismissed_pairs", [])) if state else set()
    pairs = detect_duplicates(tasks, threshold=0.70, dismissed_pairs=dismissed)

    if not pairs:
        new_state = {
            "day": day_str,
            "tasks_signature": current_sig,
            "baseline_titles": compute_baseline_titles(tasks),
            "titles_modified": False,
            "checked_at": now(),
            "handled": True,
            "has_duplicates": False,
            "pairs_count": 0,
            "dismissed_pairs": list(dismissed),
        }
        save_similarity_state(conn, day_str, new_state)
        return {
            "status": "clean",
            "tasks_changed": False,
            "has_duplicates": False,
            "pairs": [],
            "tasks_signature": current_sig,
        }

    # Duplicates found! Synthesize recommendations
    recommendations = [
        synthesize_merge_recommendation(settings, p["manual_task"], p["ai_task"], p["similarity_score"])
        for p in pairs
    ]
    new_state = {
        "day": day_str,
        "tasks_signature": current_sig,
        "baseline_titles": compute_baseline_titles(tasks),
        "titles_modified": False,
        "checked_at": now(),
        "handled": False,
        "has_duplicates": True,
        "pairs_count": len(recommendations),
        "dismissed_pairs": list(dismissed),
    }
    save_similarity_state(conn, day_str, new_state)
    return {
        "status": "duplicates_found",
        "tasks_changed": False,
        "has_duplicates": True,
        "pairs": recommendations,
        "tasks_signature": current_sig,
    }


def resolve_similarity(
    conn: Any,
    day: date | str,
    resolutions: list[dict[str, Any]],
    settings: Any = None,
) -> dict[str, Any]:
    """Apply user decisions for duplicate pairs.

    Actions:
    - 'ai_merge': Merges user-edited fields into target task, migrates evidence, deletes other task.
    - 'keep_manual': Preserves manual task, migrates AI task's evidence, deletes AI task.
    - 'keep_ai': Preserves AI task, migrates manual task's minutes and evidence, deletes manual task.
    - 'ignore': Dismisses pair without changes.
    """
    day_str = str(day)
    state = get_similarity_state(conn, day_str) or {}
    dismissed = set(state.get("dismissed_pairs", []))

    for item in resolutions:
        pair_id = item.get("pair_id") or ""
        action = item.get("action", "ai_merge")

        if action == "ignore":
            if pair_id:
                dismissed.add(pair_id)
            continue

        parts = pair_id.split("_")
        if len(parts) < 2:
            continue
        manual_id, ai_id = parts[0], parts[1]

        # Verify tasks still exist and not deleted
        m_task = None
        a_task = None
        try:
            m_task = get_task(conn, manual_id)
        except Exception:
            pass
        try:
            a_task = get_task(conn, ai_id)
        except Exception:
            pass

        if not m_task or not a_task:
            continue

        if action == "ai_merge":
            target_id = item.get("target_task_id") or manual_id
            other_id = ai_id if target_id == manual_id else manual_id

            new_title = str(item.get("title") or m_task["title"]).strip()
            new_details = str(item.get("details") or "").strip()
            new_project = str(item.get("project") or m_task["project"]).strip()
            new_category = str(item.get("category") or m_task["category"]).strip()
            new_status = str(item.get("status") or m_task["status"]).strip()
            target_mins = conn.execute(
                select(db.worklogs.c.confirmed_minutes).where(db.worklogs.c.task_id == target_id)
            ).scalar_one_or_none() or 0
            new_minutes = int(item.get("confirmed_minutes") if item.get("confirmed_minutes") is not None else target_mins)

            conn.execute(
                update(db.tasks)
                .where(db.tasks.c.id == target_id)
                .values(
                    title=new_title,
                    details=new_details,
                    project=new_project,
                    category=new_category,
                    status=new_status,
                    origin="manual",
                    edited=True,
                )
            )
            conn.execute(
                update(db.worklogs)
                .where(db.worklogs.c.task_id == target_id)
                .values(
                    confirmed_minutes=new_minutes,
                    time_locked=bool(new_minutes),
                    time_source="manual",
                )
            )

            # Migrate evidence from other task
            other_events = conn.execute(
                select(db.evidence.c.event_id).where(db.evidence.c.task_id == other_id)
            ).scalars().all()
            for eid in other_events:
                conn.execute(
                    sqlite_insert(db.evidence)
                    .values(task_id=target_id, event_id=eid)
                    .on_conflict_do_nothing()
                )

            # Soft delete other task
            conn.execute(
                update(db.tasks)
                .where(db.tasks.c.id == other_id)
                .values(deleted=True, edited=True)
            )

        elif action == "keep_manual":
            # Migrate evidence from AI task to manual task
            ai_events = conn.execute(
                select(db.evidence.c.event_id).where(db.evidence.c.task_id == ai_id)
            ).scalars().all()
            for eid in ai_events:
                conn.execute(
                    sqlite_insert(db.evidence)
                    .values(task_id=manual_id, event_id=eid)
                    .on_conflict_do_nothing()
                )
            conn.execute(
                update(db.tasks)
                .where(db.tasks.c.id == ai_id)
                .values(deleted=True, edited=True)
            )

        elif action == "keep_ai":
            # Migrate confirmed minutes and evidence to AI task
            m_mins = conn.execute(
                select(db.worklogs.c.confirmed_minutes).where(db.worklogs.c.task_id == manual_id)
            ).scalar_one_or_none() or 0
            if m_mins > 0:
                conn.execute(
                    update(db.worklogs)
                    .where(db.worklogs.c.task_id == ai_id)
                    .values(
                        confirmed_minutes=m_mins,
                        time_locked=True,
                        time_source="manual",
                    )
                )
            m_events = conn.execute(
                select(db.evidence.c.event_id).where(db.evidence.c.task_id == manual_id)
            ).scalars().all()
            for eid in m_events:
                conn.execute(
                    sqlite_insert(db.evidence)
                    .values(task_id=ai_id, event_id=eid)
                    .on_conflict_do_nothing()
                )
            conn.execute(
                update(db.tasks)
                .where(db.tasks.c.id == manual_id)
                .values(deleted=True, edited=True)
            )

    invalidate(conn, day_str)
    updated_tasks = list_tasks(conn, day_str)
    new_sig = compute_tasks_signature(updated_tasks)

    # Save state: handled = True, no duplicates remaining
    new_state = {
        "day": day_str,
        "tasks_signature": new_sig,
        "baseline_titles": compute_baseline_titles(updated_tasks),
        "titles_modified": False,
        "checked_at": now(),
        "handled": True,
        "has_duplicates": False,
        "pairs_count": 0,
        "dismissed_pairs": list(dismissed),
    }
    save_similarity_state(conn, day_str, new_state)

    return {"success": True, "tasks_signature": new_sig}
