"""豁免核验明细领域服务：每条违规、每轮核验留一条可追溯的判定记录。

核验明细回答的是“那一天、那个字段、那个条款，为什么豁免成立/不成立”，
是复查人员判断整改范围的依据，不是审批流本身。
"""
from __future__ import annotations

from typing import Any

from app.services import exemption_engine as engine
from app.services.base import BaseModuleService
from app.store import store


class ExemptionCheckService(BaseModuleService):
    MODULE = "exemption_check"
    ENTITY = "豁免核验明细"
    OPERATION_TERM = "核验"
    LIST_FIELDS = engine.CHECK_LIST_FIELDS
    REQUIRED_FIELDS = ["目标资产", "违规字段", "合规条款", "观测日期"]
    STATUS_ORDER = ["豁免成立", "无适用豁免", "豁免不覆盖"]
    ACTION_RULES = {}
    NEGATIVE_ACTIONS = []

    def get_by_observation_date(self, observed_on: str) -> list[dict[str, Any]]:
        observed = engine.normalize_date(observed_on)
        return [
            row for row in store.rows(self.MODULE)
            if engine.normalize_date(row.get("观测日期")) == observed
        ]

    def get_by_exemption(self, exemption_code: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(self.MODULE)
            if row.get("豁免编号") == exemption_code
        ]

    def get_audit_summary(self, observed_on: str | None = None) -> dict[str, Any]:
        rows = (
            self.get_by_observation_date(observed_on)
            if observed_on else list(store.rows(self.MODULE))
        )
        by_conclusion: dict[str, int] = {}
        for row in rows:
            key = str(row.get("核验结论", ""))
            by_conclusion[key] = by_conclusion.get(key, 0) + 1
        return {
            "observation_date": engine.normalize_date(observed_on) if observed_on else "",
            "total": len(rows),
            "by_conclusion": by_conclusion,
            "exempted": [row for row in rows if row.get("整改要求") == "无需整改"],
            "needs_remediation": [row for row in rows if row.get("整改要求") == "需要整改"],
        }

    def remediation_scope(self, **filters: Any) -> dict[str, Any]:
        return engine.remediation_scope(**filters)
