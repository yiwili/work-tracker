"""Matching and confidence calculation between local work tasks and remote Gitee work items.

Supports title normalization, prefix extraction and conflict detection,
token and character bigram n-gram overlap, normalized Levenshtein distance,
configurable date proximity scoring, and task merging.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from uuid import uuid4


def normalize_text(text: str | None) -> str:
    """Normalize text by lowercasing, removing punctuation and whitespace."""
    if not text:
        return ""
    return re.sub(r"[\W_]+", "", text.casefold())


def extract_prefix_and_content(item: dict[str, Any]) -> tuple[str, str]:
    """Extract project prefix (like 【上海工程】 or [2026]) and remaining content.

    If no bracketed prefix is in the title, falls back to the project or program field.
    """
    raw_title = str(item.get("title") or "").strip()
    prefix = ""
    content = raw_title

    # Bracket prefix pattern at start of title: 【...】 or [...] or （...） or (...)
    match = re.match(r"^\s*[【\[（(]([^】\]）)]+)[】\]）)]\s*(.*)$", raw_title, re.DOTALL)
    if match:
        prefix = match.group(1).strip()
        content = match.group(2).strip()

    # Fall back to project / program field if title had no bracket prefix
    if not prefix:
        proj = (
            item.get("project")
            or item.get("program")
            or item.get("program_name")
            or item.get("module")
            or ""
        )
        if isinstance(proj, dict):
            proj = proj.get("name") or proj.get("title") or ""
        proj_str = str(proj).strip()
        if proj_str and proj_str not in ("未归属项目", "None", "none", "null"):
            prefix = proj_str

    if not content:
        content = raw_title

    return prefix, content


def parse_date(value: Any) -> date | None:
    """Safely parse various date formats into a datetime.date object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        val = value.strip()
        if not val:
            return None
        # ISO-like date parsing: YYYY-MM-DD
        try:
            return date.fromisoformat(val[:10])
        except (ValueError, TypeError):
            pass
        match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", val)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
    return None


def extract_item_date(item: dict[str, Any]) -> date | None:
    """Extract a representative date from a task or remote issue dict."""
    for field in (
        "date",
        "work_date",
        "plan_started_at",
        "created_at",
        "deadline",
        "updated_at",
        "started_at",
    ):
        d = parse_date(item.get(field))
        if d is not None:
            return d
    return None


def calculate_date_score(
    local_date: date | None, remote_date: date | None
) -> tuple[float, int | None]:
    """Calculate date proximity score and days difference.

    Rules:
    - Same date: 1.0
    - Within 3 days: 0.8
    - Within 7 days: 0.6
    - Within 30 days: 0.4
    - > 30 days: 0.2
    - If remote issue (or local task) has no date: 0.5 neutral score.
    """
    if local_date is None or remote_date is None:
        return 0.5, None

    diff_days = abs((local_date - remote_date).days)
    if diff_days == 0:
        score = 1.0
    elif diff_days <= 3:
        score = 0.8
    elif diff_days <= 7:
        score = 0.6
    elif diff_days <= 30:
        score = 0.4
    else:
        score = 0.2
    return score, diff_days


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute standard Levenshtein distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1] * (len(s2) + 1)
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row[j + 1] = min(
                curr_row[j] + 1,      # insertion
                prev_row[j + 1] + 1,  # deletion
                prev_row[j] + cost,   # substitution
            )
        prev_row = curr_row
    return prev_row[len(s2)]


def normalized_levenshtein(s1: str, s2: str) -> float:
    """Normalized Levenshtein similarity: 1.0 - (dist / max(len1, len2))."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    dist = levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))
    return max(0.0, 1.0 - dist / max_len)


def get_bigrams(text: str) -> set[str]:
    """Generate character bigrams (2-grams) from normalized text."""
    if not text:
        return set()
    if len(text) < 2:
        return {text}
    return {text[i : i + 2] for i in range(len(text) - 1)}


def get_tokens(text: str) -> set[str]:
    """Tokenize into English/numeric words and individual CJK characters."""
    if not text:
        return set()
    return set(re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]", text.casefold()))


def jaccard_similarity(s1: set[str], s2: set[str]) -> float:
    """Calculate Jaccard similarity index between two sets."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    intersection = len(s1 & s2)
    union = len(s1 | s2)
    return intersection / union if union > 0 else 0.0


def check_prefix_match(p1: str, p2: str) -> tuple[str, float]:
    """Check module/prefix match status and score adjustment.

    Returns:
        (status, adjustment)
        status: 'match', 'conflict', or 'neutral'
        adjustment: +0.15 (boost), -0.50 (heavy penalty), or 0.0 (neutral)
    """
    np1 = normalize_text(p1)
    np2 = normalize_text(p2)

    if np1 in ("未归属项目", ""):
        np1 = ""
    if np2 in ("未归属项目", ""):
        np2 = ""

    if not np1 or not np2:
        return "neutral", 0.0

    if np1 == np2:
        return "match", 0.15

    if (np1 in np2 or np2 in np1) and min(len(np1), len(np2)) >= 2:
        return "match", 0.15

    return "conflict", -0.50


def calculate_content_similarity(c1: str, c2: str) -> tuple[float, float, float, float]:
    """Calculate content similarity using normalized Levenshtein, bigrams, and tokens.

    Returns:
        (combined_content_score, lev_sim, ngram_sim, token_sim)
    """
    nc1 = normalize_text(c1)
    nc2 = normalize_text(c2)

    if not nc1 and not nc2:
        return 1.0, 1.0, 1.0, 1.0
    if not nc1 or not nc2:
        return 0.0, 0.0, 0.0, 0.0
    if nc1 == nc2:
        return 1.0, 1.0, 1.0, 1.0

    lev_sim = normalized_levenshtein(nc1, nc2)
    ngram_sim = jaccard_similarity(get_bigrams(nc1), get_bigrams(nc2))
    token_sim = jaccard_similarity(get_tokens(c1), get_tokens(c2))

    combined = 0.40 * lev_sim + 0.30 * ngram_sim + 0.30 * token_sim
    return combined, lev_sim, ngram_sim, token_sim


def calculate_confidence(
    local_task: dict[str, Any],
    remote_issue: dict[str, Any],
    date_weight: float = 0.2,
) -> tuple[float, dict[str, Any]]:
    """Calculate confidence score between a local work task and a remote Gitee work item.

    Returns:
        (confidence_score, explanation_dict)
    """
    # 1. Normalize titles and extract prefixes
    local_prefix, local_content = extract_prefix_and_content(local_task)
    remote_prefix, remote_content = extract_prefix_and_content(remote_issue)

    raw_local_title = str(local_task.get("title") or "").strip()
    raw_remote_title = str(remote_issue.get("title") or "").strip()
    if not raw_local_title and not raw_remote_title:
        base_content_score, lev_sim, ngram_sim, token_sim = 0.0, 0.0, 0.0, 0.0
    else:
        base_content_score, lev_sim, ngram_sim, token_sim = calculate_content_similarity(
            local_content, remote_content
        )

    # 2. Module / Prefix matching boost or penalty
    prefix_status, prefix_delta = check_prefix_match(local_prefix, remote_prefix)
    if prefix_status == "match":
        if base_content_score >= 0.20 or (not local_content and not remote_content):
            text_score = min(1.0, base_content_score + prefix_delta)
        else:
            text_score = base_content_score
    elif prefix_status == "conflict":
        text_score = max(0.0, base_content_score + prefix_delta)
    else:
        text_score = base_content_score

    # 3. Date proximity score
    local_date = extract_item_date(local_task)
    remote_date = extract_item_date(remote_issue)
    date_score, diff_days = calculate_date_score(local_date, remote_date)

    # 4. Combined confidence: (1 - date_weight) * text_score + date_weight * date_score
    clamped_date_weight = max(0.0, min(1.0, date_weight))
    combined_score = (1.0 - clamped_date_weight) * text_score + clamped_date_weight * date_score
    confidence = max(0.0, min(1.0, combined_score))

    reason_parts = []
    if prefix_status == "match":
        reason_parts.append(f"前缀匹配[{local_prefix}]")
    elif prefix_status == "conflict":
        reason_parts.append(f"前缀冲突[{local_prefix} vs {remote_prefix}]")

    if base_content_score >= 0.95:
        reason_parts.append("标题内容完全一致")
    elif base_content_score >= 0.70:
        reason_parts.append(f"标题内容高度相似({base_content_score:.2f})")
    elif base_content_score >= 0.40:
        reason_parts.append(f"标题内容部分重合({base_content_score:.2f})")
    else:
        reason_parts.append(f"标题差异较大({base_content_score:.2f})")

    if diff_days is not None:
        if diff_days == 0:
            reason_parts.append("日期相同")
        else:
            reason_parts.append(f"相差{diff_days}天")
    else:
        reason_parts.append("缺少对比日期")

    explanation = {
        "text_score": round(text_score, 4),
        "date_score": round(date_score, 4),
        "date_weight": clamped_date_weight,
        "combined_score": round(confidence, 4),
        "prefix_local": local_prefix,
        "prefix_remote": remote_prefix,
        "prefix_status": prefix_status,
        "prefix_adjustment": prefix_delta,
        "content_similarity": round(base_content_score, 4),
        "levenshtein_similarity": round(lev_sim, 4),
        "ngram_similarity": round(ngram_sim, 4),
        "token_similarity": round(token_sim, 4),
        "days_difference": diff_days,
        "reason": "；".join(reason_parts),
    }

    return round(confidence, 4), explanation


def match_tasks_with_remote(
    local_tasks: list[dict[str, Any]],
    remote_issues: list[dict[str, Any]],
    threshold_high: float = 0.8,
    threshold_low: float = 0.4,
    date_weight: float = 0.2,
) -> dict[str, list[Any]]:
    """Match local tasks with remote Gitee work items.

    Categorizes results into:
    - auto_matched: confidence >= threshold_high
    - ambiguous: threshold_low <= confidence < threshold_high
    - discarded: all match candidates < threshold_low
    - unmatched_local: local tasks that were not auto-matched
    """
    pairs = []
    for r_idx, remote in enumerate(remote_issues):
        for l_idx, local in enumerate(local_tasks):
            score, expl = calculate_confidence(local, remote, date_weight=date_weight)
            pairs.append((score, r_idx, l_idx, expl))

    # Highest confidence pairs first
    pairs.sort(key=lambda x: x[0], reverse=True)

    matched_remotes: set[int] = set()
    matched_locals: set[int] = set()
    auto_matched: list[dict[str, Any]] = []

    for score, r_idx, l_idx, expl in pairs:
        if score < threshold_high:
            break
        if r_idx not in matched_remotes and l_idx not in matched_locals:
            matched_remotes.add(r_idx)
            matched_locals.add(l_idx)
            local_task = local_tasks[l_idx]
            remote_issue = remote_issues[r_idx]
            auto_matched.append({
                "local_task": local_task,
                "remote_issue": remote_issue,
                "confidence": score,
                "explanation": expl,
                "local": local_task,
                "remote": remote_issue,
            })

    ambiguous: list[dict[str, Any]] = []
    discarded: list[dict[str, Any]] = []

    for r_idx, remote in enumerate(remote_issues):
        if r_idx in matched_remotes:
            continue

        candidates: list[dict[str, Any]] = []
        for l_idx, local in enumerate(local_tasks):
            if l_idx in matched_locals:
                continue
            score, expl = calculate_confidence(local, remote, date_weight=date_weight)
            candidates.append({
                "local_task": local,
                "local_task_id": local.get("id"),
                "confidence": score,
                "explanation": expl,
                "local": local,
            })

        candidates.sort(key=lambda x: x["confidence"], reverse=True)
        best_score = candidates[0]["confidence"] if candidates else 0.0

        if best_score >= threshold_low:
            valid_candidates = [c for c in candidates if c["confidence"] >= threshold_low]
            ambiguous_item = {
                **remote,
                "remote_issue": remote,
                "best_confidence": best_score,
                "confidence": best_score,
                "candidates": valid_candidates,
            }
            ambiguous.append(ambiguous_item)
        else:
            discarded_item = {
                **remote,
                "remote_issue": remote,
                "best_confidence": best_score,
                "confidence": best_score,
                "candidates": candidates,
            }
            discarded.append(discarded_item)

    unmatched_local = [
        local_tasks[l_idx]
        for l_idx in range(len(local_tasks))
        if l_idx not in matched_locals
    ]

    return {
        "auto_matched": auto_matched,
        "ambiguous": ambiguous,
        "discarded": discarded,
        "unmatched_local": unmatched_local,
    }


def merge_local_into_remote(
    local_task: dict[str, Any],
    remote_issue: dict[str, Any],
) -> dict[str, Any]:
    """Merge local task details into remote Gitee work item.

    "以云端为主，补上本地设置的工时以及相关内容":
    - title, issue_id, url, project/program from remote issue
    - confirmed_minutes, suggested_minutes, details, category, evidence, timing from local task
    - origin set to 'gitee'
    - metadata including original local task id and remote issue details.
    """
    merged_metadata: dict[str, Any] = {}
    if isinstance(local_task.get("metadata"), dict):
        merged_metadata.update(local_task["metadata"])

    issue_id = remote_issue.get("issue_id") or remote_issue.get("id")
    remote_url = (
        remote_issue.get("url")
        or remote_issue.get("html_url")
        or remote_issue.get("issue_url")
        or ""
    )
    remote_project = (
        remote_issue.get("project")
        or remote_issue.get("program")
        or remote_issue.get("program_name")
        or local_task.get("project")
        or "未归属项目"
    )
    if isinstance(remote_project, dict):
        remote_project = remote_project.get("name") or remote_project.get("title") or "未归属项目"

    merged_metadata.update({
        "local_task_id": local_task.get("id"),
        "local_task_title": local_task.get("title"),
        "merged_from_local": True,
        "remote_issue": {
            "id": remote_issue.get("id"),
            "issue_id": issue_id,
            "title": remote_issue.get("title"),
            "url": remote_url,
            "program": remote_issue.get("program") or remote_issue.get("project"),
        },
    })

    details = local_task.get("details")
    if not details:
        details = remote_issue.get("details") or remote_issue.get("description") or ""

    return {
        "id": local_task.get("id") or uuid4().hex,
        "date": str(local_task.get("date") or remote_issue.get("date") or ""),
        "title": remote_issue.get("title") or local_task.get("title") or "",
        "details": details,
        "project": remote_project,
        "program": remote_issue.get("program") or remote_issue.get("program_name") or remote_project,
        "issue_id": issue_id,
        "url": remote_url,
        "category": local_task.get("category") or "开发",
        "status": local_task.get("status") or "requested",
        "origin": "gitee",
        "confirmed_minutes": local_task.get("confirmed_minutes", 0),
        "suggested_minutes": local_task.get("suggested_minutes"),
        "evidence": local_task.get("evidence", []),
        "timing": local_task.get("timing"),
        "started_at": local_task.get("started_at"),
        "ended_at": local_task.get("ended_at"),
        "time_locked": local_task.get("time_locked", False),
        "metadata": merged_metadata,
    }
