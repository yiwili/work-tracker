from datetime import date, datetime
import pytest

from awt.matching import (
    calculate_confidence,
    calculate_content_similarity,
    calculate_date_score,
    check_prefix_match,
    extract_prefix_and_content,
    format_task_title_with_prefix,
    get_prefix_metadata,
    has_bracket_prefix,
    match_tasks_with_remote,
    merge_local_into_remote,
    normalize_text,
    parse_date,
)


def test_normalize_text():
    assert normalize_text("  【上海工程】  ") == "上海工程"
    assert normalize_text("Fix: login Bug #123!") == "fixloginbug123"
    assert normalize_text("【2026】修复用户登录BUG！") == "2026修复用户登录bug"
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


def test_extract_prefix_and_content():
    # Chinese bracket prefix
    prefix, content = extract_prefix_and_content({"title": "【上海工程】修复登录BUG"})
    assert prefix == "上海工程"
    assert content == "修复登录BUG"

    # English bracket prefix
    prefix, content = extract_prefix_and_content({"title": "[2026] 优化首屏性能"})
    assert prefix == "2026"
    assert content == "优化首屏性能"

    # Fullwidth parentheses
    prefix, content = extract_prefix_and_content({"title": "（核心架构）重写存储层"})
    assert prefix == "核心架构"
    assert content == "重写存储层"

    # Fallback to project field
    prefix, content = extract_prefix_and_content({"title": "优化首屏性能", "project": "前端重构"})
    assert prefix == "前端重构"
    assert content == "优化首屏性能"

    # Fallback to program field
    prefix, content = extract_prefix_and_content({"title": "优化首屏性能", "program": "AI系统"})
    assert prefix == "AI系统"
    assert content == "优化首屏性能"

    # Fallback to program dict
    prefix, content = extract_prefix_and_content({"title": "优化首屏性能", "program": {"name": "AI系统"}})
    assert prefix == "AI系统"
    assert content == "优化首屏性能"

    # Default / unassigned
    prefix, content = extract_prefix_and_content({"title": "常规维护", "project": "未归属项目"})
    assert prefix == ""
    assert content == "常规维护"


def test_parse_date():
    assert parse_date(date(2026, 11, 2)) == date(2026, 11, 2)
    assert parse_date(datetime(2026, 11, 2, 14, 30)) == date(2026, 11, 2)
    assert parse_date("2026-11-02") == date(2026, 11, 2)
    assert parse_date("2026-11-02T10:00:00Z") == date(2026, 11, 2)
    assert parse_date("2026-11-02 18:30:00") == date(2026, 11, 2)
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("not-a-date") is None


def test_date_proximity_scoring():
    d = date(2026, 11, 10)

    # Same date -> 1.0
    score, diff = calculate_date_score(d, date(2026, 11, 10))
    assert score == 1.0
    assert diff == 0

    # Within 3 days -> 0.8
    score, diff = calculate_date_score(d, date(2026, 11, 12))
    assert score == 0.8
    assert diff == 2
    score, diff = calculate_date_score(d, date(2026, 11, 7))
    assert score == 0.8
    assert diff == 3

    # Within 7 days -> 0.6
    score, diff = calculate_date_score(d, date(2026, 11, 14))
    assert score == 0.6
    assert diff == 4
    score, diff = calculate_date_score(d, date(2026, 11, 17))
    assert score == 0.6
    assert diff == 7

    # Within 30 days -> 0.4
    score, diff = calculate_date_score(d, date(2026, 11, 18))
    assert score == 0.4
    assert diff == 8
    score, diff = calculate_date_score(d, date(2026, 11, 24))  # 14 days
    assert score == 0.4
    assert diff == 14
    score, diff = calculate_date_score(d, date(2026, 12, 10))  # 30 days
    assert score == 0.4
    assert diff == 30

    # > 30 days -> 0.2
    score, diff = calculate_date_score(d, date(2026, 12, 11))
    assert score == 0.2
    assert diff == 31
    score, diff = calculate_date_score(d, date(2027, 2, 1))
    assert score == 0.2
    assert diff > 30

    # Missing date -> 0.5 neutral
    score, diff = calculate_date_score(d, None)
    assert score == 0.5
    assert diff is None
    score, diff = calculate_date_score(None, d)
    assert score == 0.5
    assert diff is None
    score, diff = calculate_date_score(None, None)
    assert score == 0.5
    assert diff is None


def test_prefix_match_and_mismatch():
    # Exactly matching prefixes
    status, delta = check_prefix_match("上海工程", "上海工程")
    assert status == "match"
    assert delta > 0

    # Substring matching prefix (>= 2 chars)
    status, delta = check_prefix_match("上海工程", "上海")
    assert status == "match"
    assert delta > 0

    # Conflicting prefixes
    status, delta = check_prefix_match("上海工程", "北京工程")
    assert status == "conflict"
    assert delta < -0.3

    # One or both missing
    status, delta = check_prefix_match("", "上海工程")
    assert status == "neutral"
    assert delta == 0.0

    status, delta = check_prefix_match("上海工程", "")
    assert status == "neutral"
    assert delta == 0.0

    # Unassigned
    status, delta = check_prefix_match("未归属项目", "上海工程")
    assert status == "neutral"
    assert delta == 0.0


def test_prefix_effect_on_confidence():
    local_matching = {
        "title": "【上海工程】修复登录页面崩溃",
        "date": "2026-11-02",
    }
    local_conflicting = {
        "title": "【北京工程】修复登录页面崩溃",
        "date": "2026-11-02",
    }
    remote = {
        "title": "【上海工程】修复登录页面崩溃",
        "date": "2026-11-02",
    }

    score_match, expl_match = calculate_confidence(local_matching, remote)
    score_conflict, expl_conflict = calculate_confidence(local_conflicting, remote)

    assert expl_match["prefix_status"] == "match"
    assert expl_conflict["prefix_status"] == "conflict"
    assert score_match >= 0.95
    # Conflicting prefix heavily penalizes confidence
    assert score_conflict <= 0.65
    assert score_match > score_conflict + 0.30


def test_identical_and_near_identical_titles():
    # Identical titles, same date
    local = {"title": "优化前端首屏渲染性能", "date": "2026-11-02"}
    remote = {"title": "优化前端首屏渲染性能", "date": "2026-11-02"}
    score, expl = calculate_confidence(local, remote)
    assert score == 1.0
    assert expl["text_score"] == 1.0

    # Near-identical titles
    local_near = {"title": "【2026】优化前端首屏渲染性能", "date": "2026-11-02"}
    remote_near = {"title": "【2026】优化前端首屏渲染性能缺陷", "date": "2026-11-02"}
    score_near, expl_near = calculate_confidence(local_near, remote_near)
    assert score_near >= 0.80
    assert expl_near["prefix_status"] == "match"


def test_date_weight_effect():
    # Strong title match, but 2 weeks (14 days) apart
    local = {"title": "重构权限校验中间件", "date": "2026-11-02"}
    remote = {"title": "重构权限校验中间件", "date": "2026-11-16"}  # 14 days diff (date_score=0.4)

    # Low date weight (default 0.20): text match is strong enough to pass >= 0.8
    score_low_weight, expl_low = calculate_confidence(local, remote, date_weight=0.20)
    # (1 - 0.2) * 1.0 + 0.2 * 0.4 = 0.88 >= 0.80
    assert score_low_weight >= 0.80
    assert expl_low["date_score"] == 0.4

    # High date weight (0.50): falls below high threshold into ambiguous
    score_high_weight, expl_high = calculate_confidence(local, remote, date_weight=0.50)
    # (1 - 0.5) * 1.0 + 0.5 * 0.4 = 0.70 < 0.80
    assert score_high_weight < 0.80
    assert score_high_weight >= 0.60


def test_categorization_auto_matched_ambiguous_discarded():
    local_tasks = [
        {"id": "t1", "title": "【上海工程】修复订单结算金额计算BUG", "date": "2026-11-02", "confirmed_minutes": 60},
        {"id": "t2", "title": "完善用户设置页面UI样式", "date": "2026-11-02", "confirmed_minutes": 45},
        {"id": "t3", "title": "搭建CI/CD自动化部署流程", "date": "2026-11-02", "confirmed_minutes": 120},
    ]

    remote_issues = [
        # Exactly matches t1 -> auto_matched
        {"id": 101, "issue_id": 101, "title": "【上海工程】修复订单结算金额计算BUG", "date": "2026-11-02", "url": "https://gitee.com/issue/101"},
        # Partial match with t2 -> ambiguous (around ~0.65-0.70 confidence)
        {"id": 102, "issue_id": 102, "title": "优化用户设置界面UI样式", "date": "2026-11-02", "url": "https://gitee.com/issue/102"},
        # Completely unrelated -> discarded
        {"id": 103, "issue_id": 103, "title": "编写市场营销宣传软文", "date": "2026-11-02", "url": "https://gitee.com/issue/103"},
    ]

    result = match_tasks_with_remote(
        local_tasks,
        remote_issues,
        threshold_high=0.8,
        threshold_low=0.4,
        date_weight=0.2,
    )

    # 1. auto_matched: issue 101 with task t1
    auto_matched = result["auto_matched"]
    assert len(auto_matched) == 1
    assert auto_matched[0]["local_task"]["id"] == "t1"
    assert auto_matched[0]["remote_issue"]["id"] == 101
    assert auto_matched[0]["confidence"] >= 0.80

    # 2. ambiguous: issue 102
    ambiguous = result["ambiguous"]
    assert len(ambiguous) == 1
    assert ambiguous[0]["id"] == 102
    assert 0.40 <= ambiguous[0]["best_confidence"] < 0.80
    assert len(ambiguous[0]["candidates"]) >= 1

    # 3. discarded: issue 103
    discarded = result["discarded"]
    assert len(discarded) == 1
    assert discarded[0]["id"] == 103
    assert discarded[0]["best_confidence"] < 0.40

    # 4. unmatched_local: t3 was never matched
    unmatched_local = result["unmatched_local"]
    unmatched_ids = [t["id"] for t in unmatched_local]
    assert "t3" in unmatched_ids
    assert "t1" not in unmatched_ids


def test_merge_local_into_remote():
    local_task = {
        "id": "loc-uuid-12345",
        "date": "2026-11-02",
        "title": "本地草稿：修复登录失败",
        "details": "修复了auth.py并发竞争导致的500错误，补充了单元测试用例",
        "category": "Bug 修复",
        "confirmed_minutes": 90,
        "suggested_minutes": 80,
        "status": "verified",
        "evidence": [{"id": "ev1", "kind": "git_commit", "summary": "fix(auth): fix lock"}],
        "timing": {"active_minutes": 88},
        "started_at": "2026-11-02T09:00:00+08:00",
        "ended_at": "2026-11-02T10:30:00+08:00",
        "time_locked": True,
        "metadata": {"custom_tag": "urgent"},
    }

    remote_issue = {
        "id": 8888,
        "issue_id": 8888,
        "title": "【工程A】修复用户登录授权时偶发的500异常",
        "url": "https://gitee.com/my-org/my-repo/issues/8888",
        "program": "工程A",
        "project": "工程A",
        "date": "2026-11-01",
    }

    merged = merge_local_into_remote(local_task, remote_issue)

    # Remote priority fields
    assert merged["title"] == "【工程A】修复用户登录授权时偶发的500异常"
    assert merged["issue_id"] == 8888
    assert merged["url"] == "https://gitee.com/my-org/my-repo/issues/8888"
    assert merged["project"] == "工程A"
    assert merged["program"] == "工程A"

    # Local inherited fields
    assert merged["confirmed_minutes"] == 90
    assert merged["suggested_minutes"] == 80
    assert merged["details"] == "修复了auth.py并发竞争导致的500错误，补充了单元测试用例"
    assert merged["category"] == "Bug 修复"
    assert merged["status"] == "verified"
    assert merged["evidence"] == [{"id": "ev1", "kind": "git_commit", "summary": "fix(auth): fix lock"}]
    assert merged["timing"] == {"active_minutes": 88}
    assert merged["started_at"] == "2026-11-02T09:00:00+08:00"
    assert merged["ended_at"] == "2026-11-02T10:30:00+08:00"
    assert merged["time_locked"] is True

    # Origin and Metadata
    assert merged["origin"] == "gitee"
    assert merged["metadata"]["local_task_id"] == "loc-uuid-12345"
    assert merged["metadata"]["local_task_title"] == "本地草稿：修复登录失败"
    assert merged["metadata"]["merged_from_local"] is True
    assert merged["metadata"]["remote_issue"]["issue_id"] == 8888
    assert merged["metadata"]["custom_tag"] == "urgent"


def test_edge_cases():
    # 1. Empty strings and None values
    empty_local = {"title": "", "date": ""}
    empty_remote = {"title": None, "date": None}
    score, expl = calculate_confidence(empty_local, empty_remote)
    assert score == 0.1  # 0.8 * 0.0 + 0.2 * 0.5 = 0.1
    assert score < 0.40

    # 2. Missing dates on both sides
    t_no_date_1 = {"title": "更新系统依赖组件版本"}
    t_no_date_2 = {"title": "更新系统依赖组件版本"}
    score_no_date, expl_no_date = calculate_confidence(t_no_date_1, t_no_date_2)
    assert expl_no_date["date_score"] == 0.5
    # (1 - 0.2) * 1.0 + 0.2 * 0.5 = 0.90
    assert score_no_date == 0.90
    assert score_no_date >= 0.80

    # 3. English + Chinese mix
    local_mix = {
        "title": "【AWT】Fix auth token 校验错误",
        "date": "2026-11-02",
    }
    remote_mix = {
        "title": "【AWT】Fix auth token 校验异常",
        "date": "2026-11-02",
    }
    score_mix, expl_mix = calculate_confidence(local_mix, remote_mix)
    assert score_mix >= 0.80
    assert expl_mix["prefix_status"] == "match"

    # 4. Empty local tasks list
    res_empty_local = match_tasks_with_remote(
        [],
        [{"id": 1, "title": "开发任务", "date": "2026-11-02"}],
    )
    assert len(res_empty_local["auto_matched"]) == 0
    assert len(res_empty_local["ambiguous"]) == 0
    assert len(res_empty_local["discarded"]) == 1
    assert len(res_empty_local["unmatched_local"]) == 0

    # 5. Empty remote issues list
    res_empty_remote = match_tasks_with_remote(
        [{"id": "t1", "title": "本地任务", "date": "2026-11-02"}],
        [],
    )
    assert len(res_empty_remote["auto_matched"]) == 0
    assert len(res_empty_remote["ambiguous"]) == 0
    assert len(res_empty_remote["discarded"]) == 0
    assert len(res_empty_remote["unmatched_local"]) == 1

    # 6. Both lists empty
    res_both_empty = match_tasks_with_remote([], [])
    assert res_both_empty["auto_matched"] == []
    assert res_both_empty["ambiguous"] == []
    assert res_both_empty["discarded"] == []
    assert res_both_empty["unmatched_local"] == []


def test_multiple_remote_issues_competing_for_one_local_task():
    # One local task, two remote issues with high confidence
    local_task = {"id": "t1", "title": "【上海工程】修复登录BUG", "date": "2026-11-02"}
    remote1 = {"id": 1, "title": "【上海工程】修复登录BUG", "date": "2026-11-02"}  # exact 1.0
    remote2 = {"id": 2, "title": "【上海工程】修复登录BUG问题", "date": "2026-11-02"}  # near match ~0.9

    result = match_tasks_with_remote([local_task], [remote1, remote2])

    # remote1 should win the auto-match
    assert len(result["auto_matched"]) == 1
    assert result["auto_matched"][0]["remote_issue"]["id"] == 1
    assert result["auto_matched"][0]["local_task"]["id"] == "t1"

    # t1 is now matched, so remote2 cannot take it.
    assert len(result["unmatched_local"]) == 0


def test_confidence_threshold_high_auto_pass():
    local = {"title": "【项目A】实现用户注册与邮箱激活", "date": "2026-11-02"}
    remote = {"title": "【项目A】实现用户注册与邮箱激活", "date": "2026-11-02"}
    score, expl = calculate_confidence(local, remote)
    assert score >= 0.80
    assert score == 1.0


def test_confidence_threshold_ambiguous_range():
    local = {"title": "优化订单支付链路响应时间", "date": "2026-11-02"}
    remote = {"title": "优化订单结算与支付体验", "date": "2026-11-02"}
    score, expl = calculate_confidence(local, remote)
    assert 0.40 <= score < 0.80


def test_confidence_threshold_low_discarding():
    local = {"title": "迁移Redis主从哨兵集群架构", "date": "2026-11-02"}
    remote = {"title": "编写年终述职PPT与汇报总结", "date": "2026-11-02"}
    score, expl = calculate_confidence(local, remote)
    assert score < 0.40


def test_date_proximity_boundaries():
    t_local = {"title": "【AWT】核心任务", "date": "2026-11-01"}

    # 0 days -> date_score 1.0 -> combined 1.0
    s0, e0 = calculate_confidence(t_local, {"title": "【AWT】核心任务", "date": "2026-11-01"})
    assert e0["date_score"] == 1.0
    assert s0 == 1.0

    # 3 days -> date_score 0.8 -> combined (0.8 * 1.0 + 0.2 * 0.8 = 0.96)
    s3, e3 = calculate_confidence(t_local, {"title": "【AWT】核心任务", "date": "2026-11-04"})
    assert e3["date_score"] == 0.8
    assert s3 == 0.96

    # 7 days -> date_score 0.6 -> combined (0.8 * 1.0 + 0.2 * 0.6 = 0.92)
    s7, e7 = calculate_confidence(t_local, {"title": "【AWT】核心任务", "date": "2026-11-08"})
    assert e7["date_score"] == 0.6
    assert s7 == 0.92

    # 30 days -> date_score 0.4 -> combined (0.8 * 1.0 + 0.2 * 0.4 = 0.88)
    s30, e30 = calculate_confidence(t_local, {"title": "【AWT】核心任务", "date": "2026-12-01"})
    assert e30["date_score"] == 0.4
    assert s30 == 0.88

    # 31 days -> date_score 0.2 -> combined (0.8 * 1.0 + 0.2 * 0.2 = 0.84)
    s31, e31 = calculate_confidence(t_local, {"title": "【AWT】核心任务", "date": "2026-12-02"})
    assert e31["date_score"] == 0.2
    assert s31 == 0.84


def test_has_bracket_prefix():
    assert has_bracket_prefix("【2026】修复卷内目录打印问题") is True
    assert has_bracket_prefix("[2026] 优化首屏性能") is True
    assert has_bracket_prefix("（核心架构）重写存储层") is True
    assert has_bracket_prefix("(核心架构) 重写存储层") is True
    assert has_bracket_prefix("   【上海工程】测试   ") is True
    assert has_bracket_prefix("修复卷内目录打印问题") is False
    assert has_bracket_prefix("2026数据迁移") is False
    assert has_bracket_prefix("【】空括号测试") is False
    assert has_bracket_prefix("") is False
    assert has_bracket_prefix(None) is False


def test_format_task_title_with_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("AWT_DATA_DIR", str(tmp_path / "data"))
    from awt import db
    from awt.models import Settings, GiteeEnterpriseConfig, GiteeProjectRule
    from awt.matching import clear_prefix_cache

    clear_prefix_cache()
    engine = db.make_engine(tmp_path / "test_pfx.db")
    db.migrate(engine)

    with engine.begin() as conn:
        # Insert historical items to teach the user's style
        conn.execute(
            db.history.insert().values(
                [
                    {"id": "h1", "title": "【2026】在线编研模块", "project": "档案系统2026"},
                    {"id": "h2", "title": "【2026】优化相册预览", "project": "archive-system-2026"},
                    {"id": "h3", "title": "【农林】元数据显示不完整", "project": "浙江农林大学"},
                    {"id": "h4", "title": "【其他】录制帮助视频", "project": "未归属项目"},
                ]
            )
        )

        settings = Settings(
            gitee_enterprise=GiteeEnterpriseConfig(
                enterprise_id=1,
                rules=[
                    GiteeProjectRule(pattern="上海工程", mode="prefix", program_id=10),
                ],
            )
        )

        meta = get_prefix_metadata(conn, settings)
        assert meta["bracket_style"] == "【】"
        assert meta["project_prefixes"].get("archive-system-2026") == "2026"
        assert meta["project_prefixes"].get("浙江农林大学") == "农林"
        assert meta["default_prefix"] == "其他"

        # 1. Title already has prefix: untouched (saves tokens)
        res1 = format_task_title_with_prefix(conn, "【2026】修复卷内目录", "archive-system-2026", settings)
        assert res1 == "【2026】修复卷内目录"

        res2 = format_task_title_with_prefix(conn, "[上海工程] 制作案卷封面", "上海工程技术大学", settings)
        assert res2 == "[上海工程] 制作案卷封面"

        # 2. Title without prefix + mapped project
        res3 = format_task_title_with_prefix(conn, "修复卷内目录打印问题", "archive-system-2026", settings)
        assert res3 == "【2026】修复卷内目录打印问题"

        # 3. Title without prefix + Gitee rule project
        res4 = format_task_title_with_prefix(conn, "制作案卷封面模板", "上海工程技术大学", settings)
        assert res4 == "【上海工程】制作案卷封面模板"

        # 4. Title starting with mapped prefix name without brackets: bracketed without repetition
        res5 = format_task_title_with_prefix(conn, "2026数据迁移方案", "archive-system-2026", settings)
        assert res5 == "【2026】数据迁移方案"

        # 5. Empty project falls back to general prefix (e.g. 【其他】)
        res6 = format_task_title_with_prefix(conn, "整理部门月度汇报", "", settings)
        assert res6 == "【其他】整理部门月度汇报"

        res7 = format_task_title_with_prefix(conn, "整理部门月度汇报", "未归属项目", settings)
        assert res7 == "【其他】整理部门月度汇报"

        res8 = format_task_title_with_prefix(conn, "调试未知仓库脚本", "completely-unknown-repo", settings)
        assert res8 == "【其他】调试未知仓库脚本"

        res9 = format_task_title_with_prefix(conn, "协助处理临时事务", "空", settings)
        assert res9 == "【其他】协助处理临时事务"

    engine.dispose()


