"""扫描违规明细领域服务：字段级的违规观测，按条款、分区和观测日留存。

一条违规明细只对应一个条款；同一字段同时命中 C7 和 C9 时会产生两条明细，
各自独立核验豁免范围，不会被同一条凭据一起带走。
"""
from __future__ import annotations

from typing import Any

from app.services import exemption_engine as engine
from app.services.base import BaseModuleService
from app.store import store


class ScanViolationService(BaseModuleService):
    MODULE = "scan_violation"
    ENTITY = "扫描违规明细"
    OPERATION_TERM = "核验"
    LIST_FIELDS = engine.VIOLATION_LIST_FIELDS
    REQUIRED_FIELDS = engine.VIOLATION_REQUIRED_FIELDS
    STATUS_ORDER = ["待核验", "需整改", "已豁免"]
    ACTION_RULES = {"核验豁免": "待核验", "确认整改": "需整改", "确认豁免": "已豁免"}
    NEGATIVE_ACTIONS = ["确认整改"]

    def create_entry(self, values: dict[str, Any]):
        return engine.register_violation(values, source="scan_violation")

    def run_action(self, entry_id: int, action: str):
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        if action == "核验豁免":
            engine.evaluate_violation(entry, force_check=True)
            if entry.get("扫描id"):
                engine.refresh_scan(int(entry["扫描id"]), scan_code=entry.get("扫描编号", ""))
            return entry, (
                f"{self.ENTITY}已核验：{entry.get('核验结论')}"
            )
        target = self.ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != "已豁免"
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS
        entry["updated_at"] = engine._now()
        store.log_change(
            self.MODULE, entry_id, action,
            before=None, after=target,
            source="action", scope=self.MODULE,
        )
        return entry, f"{self.ENTITY}已{action}"

    def get_by_asset(self, asset_code: str, observed_on: str | None = None) -> list[dict[str, Any]]:
        rows = engine.list_rows(self.MODULE, 目标资产=asset_code)
        if observed_on:
            observed = engine.normalize_date(observed_on)
            rows = [row for row in rows if engine.normalize_date(row.get("观测日期")) == observed]
        return rows

    def get_pending_fields(self, observed_on: str | None = None) -> list[dict[str, Any]]:
        """复查人员视角：某观测日仍需整改的字段。"""
        return engine.remediation_scope(observation_date=observed_on)["fields_to_remediate"]

    def get_exempted_fields(self, observed_on: str | None = None) -> list[dict[str, Any]]:
        return engine.remediation_scope(observation_date=observed_on)["exempted_fields"]

    def remediation_scope(self, **filters: Any) -> dict[str, Any]:
        return engine.remediation_scope(**filters)
