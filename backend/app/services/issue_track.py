"""问题追踪领域服务：数据治理问题的发现、指派、修复和闭环验证管理。

接收质量规则检查阻断、影响分析和合规扫描等过程的自动化问题创建。
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services import exemption_engine as engine
from app.services.base import BaseModuleService
from app.store import store


class IssueTrackService(BaseModuleService):
    MODULE = "issue_track"
    ENTITY = "治理问题"
    OPERATION_TERM = "关联"
    LIST_FIELDS = [
        "问题编号", "关联资产", "问题类型", "严重级别", "指派处理", "当前状态",
        "修复方案", "截止日期", "数据分区", "违规字段", "合规条款", "检查规则",
        "观测日期", "整改要求", "豁免编号", "豁免适用日期",
    ]
    REQUIRED_FIELDS = ["问题编号", "关联资产", "问题类型"]
    STATUS_ORDER = ["待确认", "处理中", "已修复", "已验证"]
    ACTION_RULES = {"确认问题": "处理中", "指派处理": "处理中", "验证修复": "已验证"}
    NEGATIVE_ACTIONS = []

    def create_issue(self, asset_code: str, issue_type: str, severity: str,
                     assignee: str, solution: str, deadline_days: int = 7) -> dict[str, Any]:
        """创建治理问题。"""
        issue_id = store.next_id(self.MODULE)
        issue = {
            "id": issue_id,
            "问题编号": f"ISSU-{issue_id:04d}",
            "关联资产": asset_code,
            "问题类型": issue_type,
            "严重级别": severity,
            "指派处理": assignee,
            "当前状态": "待确认",
            "修复方案": solution,
            "截止日期": _time.strftime("%Y-%m-%d", _time.localtime(_time.time() + 86400 * deadline_days)),
            "status": "待确认",
            "pending": True,
            "abnormal": severity == "严重",
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(self.MODULE).append(issue)

        # 发出事件通知
        self.emit_event("issue_created", {
            "issue_id": issue_id,
            "asset_code": asset_code,
            "issue_type": issue_type,
            "severity": severity,
            "assignee": assignee,
        })

        return issue

    def fix_issue(self, issue_id: int, fix_description: str) -> dict[str, Any] | None:
        """标记问题为已修复。"""
        issue = self.get_entry(issue_id)
        if not issue:
            return None

        old_status = issue.get("status", "")
        issue["status"] = "已修复"
        issue["pending"] = False
        issue["修复方案"] = f"{issue.get('修复方案', '')} | 实际修复: {fix_description}"
        issue["fixed_at"] = _time.strftime("%Y-%m-%d %H:%M:%S")

        store.log_change(
            self.MODULE, issue_id, "修复问题",
            before=old_status, after="已修复",
            source="fix", scope="issue_track"
        )

        self.emit_event("issue_fixed", {
            "issue_id": issue_id,
            "asset_code": issue.get("关联资产"),
        })

        return issue

    def verify_issue(self, issue_id: int, verified_by: str) -> dict[str, Any] | None:
        """验证修复完成。"""
        issue = self.get_entry(issue_id)
        if not issue:
            return None

        issue["status"] = "已验证"
        issue["pending"] = False
        issue["verified_by"] = verified_by
        issue["verified_at"] = _time.strftime("%Y-%m-%d %H:%M:%S")

        self.emit_event("issue_verified", {
            "issue_id": issue_id,
            "asset_code": issue.get("关联资产"),
        })

        return issue

    def get_issues_by_asset(self, asset_code: str) -> list[dict[str, Any]]:
        """获取资产的所有治理问题。"""
        return store.find_by_field(self.MODULE, "关联资产", asset_code)

    def get_open_issues(self) -> list[dict[str, Any]]:
        """获取所有未关闭问题。"""
        return [
            r for r in store.rows(self.MODULE)
            if r.get("status") in ("待确认", "处理中")
        ]

    def get_issues_by_severity(self, severity: str) -> list[dict[str, Any]]:
        """按严重级别获取问题。"""
        return store.find_by_field(self.MODULE, "严重级别", severity)

    def get_issues_by_observation_date(self, observed_on: str) -> list[dict[str, Any]]:
        """按观测日获取问题，含已被豁免但保留追溯的已派出条目。"""
        observed = engine.normalize_date(observed_on)
        return [
            row for row in store.rows(self.MODULE)
            if engine.normalize_date(row.get("观测日期")) == observed
        ]

    def get_issues_by_clause(self, clause: str, observed_on: str | None = None) -> list[dict[str, Any]]:
        """按仍适用的违规条款查找问题；豁免条目不会被当作待整改分派。"""
        rows = [
            row for row in store.rows(self.MODULE)
            if engine.clause_match(row.get("合规条款"), clause)
        ]
        if observed_on:
            observed = engine.normalize_date(observed_on)
            rows = [row for row in rows if engine.normalize_date(row.get("观测日期")) == observed]
        return rows

    def get_dispatchable_issues(self, observed_on: str | None = None) -> list[dict[str, Any]]:
        """问题追踪真正要分派的范围：豁免范围内的条目只保留追溯，不再派整改。"""
        rows = [
            row for row in store.rows(self.MODULE)
            if row.get("整改要求") != "无需整改" and row.get("status") != "已豁免"
        ]
        if observed_on:
            observed = engine.normalize_date(observed_on)
            rows = [row for row in rows if engine.normalize_date(row.get("观测日期")) == observed]
        return rows

    def get_exempted_issues(self, observed_on: str | None = None) -> list[dict[str, Any]]:
        """已经派出、后补豁免的条目：可追到豁免编号和适用日期。"""
        rows = [row for row in store.rows(self.MODULE) if row.get("status") == "已豁免"]
        if observed_on:
            observed = engine.normalize_date(observed_on)
            rows = [row for row in rows if engine.normalize_date(row.get("观测日期")) == observed]
        return rows

    def trace_exemption(self, issue_id: int) -> dict[str, Any] | None:
        """沿整改单追到豁免凭据与适用日期，供复查核对。"""
        issue = self.get_entry(issue_id)
        if issue is None:
            return None
        exemption = next(
            (row for row in store.rows("clause_exemption")
             if row.get("豁免编号") == issue.get("豁免编号")),
            None,
        )
        checks = [
            row for row in store.rows("exemption_check")
            if row.get("豁免编号") == issue.get("豁免编号")
            and row.get("违规字段") == issue.get("违规字段")
            and row.get("合规条款") == issue.get("合规条款")
        ]
        return {
            "issue": issue,
            "exemption": exemption,
            "checks": checks,
            "exempted_on": issue.get("豁免适用日期", ""),
            "observation_date": issue.get("观测日期", ""),
        }

    def remediation_scope(self, **filters: Any) -> dict[str, Any]:
        return engine.remediation_scope(**filters)

    def get_issues_by_assignee(self, assignee: str) -> list[dict[str, Any]]:
        """按指派人获取问题。"""
        return store.find_by_field(self.MODULE, "指派处理", assignee)

    def issue_statistics(self) -> dict[str, Any]:
        """问题统计概览。"""
        all_issues = store.rows(self.MODULE)
        by_status = store.count_by_status(self.MODULE)
        by_severity = store.count_by_field(self.MODULE, "严重级别")

        return {
            "total": len(all_issues),
            "open": sum(1 for r in all_issues if r.get("status") in ("待确认", "处理中")),
            "resolved": sum(1 for r in all_issues if r.get("status") in ("已修复", "已验证")),
            "by_status": by_status,
            "by_severity": by_severity,
            "pipeline": "quality_rule->issue_track",
        }