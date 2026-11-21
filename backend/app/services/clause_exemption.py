"""条款豁免领域服务：登记有期限的例外，并按条款、分区和观测日核验。

豁免是对某一条款在某个分区、字段和检查规则上的例外，不是对字段的终身免检。
补报的豁免照样按原观测日生效；已经派出的整改单保留，但会回写豁免编号和适用日期。
"""
from __future__ import annotations

from typing import Any

from app.services import exemption_engine as engine
from app.services.base import BaseModuleService
from app.store import store


class ClauseExemptionService(BaseModuleService):
    MODULE = "clause_exemption"
    ENTITY = "条款豁免"
    OPERATION_TERM = "核验"
    LIST_FIELDS = engine.EXEMPTION_LIST_FIELDS
    REQUIRED_FIELDS = engine.EXEMPTION_REQUIRED_FIELDS
    STATUS_ORDER = ["已生效", "已驳回", "已终止"]
    ACTION_RULES = {"驳回豁免": "已驳回", "终止豁免": "已终止", "恢复生效": "已生效"}
    NEGATIVE_ACTIONS = ["驳回豁免", "终止豁免"]

    def create_entry(self, values: dict[str, Any]):
        return engine.apply_exemption(values, source="clause_exemption")

    def run_action(self, entry_id: int, action: str):
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        old_status = entry.get("status")
        entry["status"] = target
        entry["pending"] = False
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS
        entry["当前有效性"] = self._validity(entry)
        entry["updated_at"] = engine._now()
        store.log_change(
            self.MODULE, entry_id, action,
            before=old_status, after=target,
            source="action", scope=f"{self.MODULE}->scan_violation",
        )
        # 豁免被驳回/终止时，恢复原本需要整改的字段和整改单；恢复生效时重新核销。
        engine.revoke_exemption(entry)
        return entry, f"{self.ENTITY}已{action}"

    @staticmethod
    def _validity(entry: dict[str, Any]) -> str:
        if entry.get("status") != "已生效":
            return "已失效"
        today = engine._today()
        if today < str(entry.get("生效日期", "")):
            return "未生效"
        if today > str(entry.get("失效日期", "")):
            return "已过期"
        return "有效期内"

    def check_field(
        self,
        *,
        clause: str,
        partition: str,
        field: str,
        rule: str = "",
        observed_on: str,
    ) -> dict[str, Any]:
        """按条款和分区核验某字段在观测日是否被豁免。"""
        match = engine.find_matching_exemption(
            clause=clause, partition=partition, field=field,
            rule=rule or "*", observed_on=observed_on,
        )
        scope_only = engine.find_scope_exemption(
            clause=clause, partition=partition, field=field, rule=rule or "*",
        )
        if match is not None:
            return {
                "ok": True,
                "exempted": True,
                "conclusion": "豁免成立",
                "exemption": match,
                "observed_on": engine.normalize_date(observed_on),
            }
        return {
            "ok": True,
            "exempted": False,
            "conclusion": "豁免不覆盖" if scope_only else "无适用豁免",
            "exemption": scope_only,
            "observed_on": engine.normalize_date(observed_on),
        }

    def get_active_exemptions(self) -> list[dict[str, Any]]:
        today = engine._today()
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "已生效"
            and str(row.get("生效日期", "")) <= today <= str(row.get("失效日期", ""))
        ]

    def get_exemptions_by_scope(self, clause: str, partition: str | None = None) -> list[dict[str, Any]]:
        return engine.list_rows(self.MODULE, 适用条款=clause, 数据分区=partition)

    def get_audit_trail(self, exemption_id: int) -> list[dict[str, Any]]:
        """豁免的核验明细：核销过的每一条扫描违规和整改单。"""
        exemption = self.get_entry(exemption_id)
        if exemption is None:
            return []
        checks = [
            row for row in store.rows("exemption_check")
            if row.get("豁免编号") == exemption.get("豁免编号")
        ]
        return checks
