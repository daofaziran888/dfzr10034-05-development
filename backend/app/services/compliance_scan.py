"""合规扫描领域服务：数据资产的合规性自动扫描、违规发现和修复追踪。

接收数据契约的Schema验证和操作日志的异常检测作为扫描输入。
字段级扫描结论交给条款豁免引擎按条款、分区和观测日核销，再回写问题追踪。
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services import exemption_engine as engine
from app.services.base import BaseModuleService
from app.store import store


class ComplianceScanService(BaseModuleService):
    MODULE = "compliance_scan"
    ENTITY = "扫描任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["任务编号", "目标资产", "合规条款", "检查规则", "扫描范围", "违规发现", "修复意见", "扫描时间"]
    REQUIRED_FIELDS = ["任务编号", "目标资产", "合规条款"]
    STATUS_ORDER = ["待扫描", "扫描中", "已通过", "存在违规"]
    ACTION_RULES = {"启动扫描": "扫描中", "确认通过": "已通过", "标记违规": "存在违规"}
    NEGATIVE_ACTIONS = ["标记违规"]

    def scan_asset(self, asset_code: str, compliance_clause: str, check_rules: list[str]) -> dict[str, Any]:
        """对资产执规扫描（Pipeline 5/8 核心方法）。"""
        scan_id = store.next_id(self.MODULE)
        violations = []

        for rule in check_rules:
            # 根据规则类型检查资产
            if "分类" in rule or "classification" in rule.lower():
                classifications = [
                    c for c in store.rows("classification")
                    if str(c.get("目标字段", "")).startswith(f"{asset_code}.")
                ]
                pending = [c for c in classifications if c.get("status") == "待复核"]
                if pending:
                    violations.append(f"存在{len(pending)}个待复核的分级标签")

            if "脱敏" in rule or "anonym" in rule.lower():
                for c in store.rows("classification"):
                    if str(c.get("目标字段", "")).startswith(f"{asset_code}."):
                        if c.get("敏感等级") == "高" and c.get("status") == "已确认":
                            # 检查是否有对应脱敏规则
                            anonym_rules = store.find_by_field("data_anonym", "目标字段", c.get("目标字段", ""))
                            if not anonym_rules:
                                violations.append(f"高敏感字段{c.get('目标字段','')}缺少脱敏规则")

            if "契约" in rule or "contract" in rule.lower():
                contracts = store.find_by_field("data_contract", "数据资产", asset_code)
                violated = [c for c in contracts if c.get("status") == "已违约"]
                if violated:
                    violations.append(f"存在{len(violated)}个违约契约")

            if "操作" in rule or "log" in rule.lower():
                anomalies = [
                    a for a in store.rows("operation_log")
                    if a.get("目标资产") == asset_code and a.get("status") == "已异常"
                ]
                if anomalies:
                    violations.append(f"存在{len(anomalies)}条异常操作记录需要核实")

        passed = len(violations) == 0

        scan = {
            "id": scan_id,
            "任务编号": f"COMP-{scan_id:04d}",
            "目标资产": asset_code,
            "合规条款": compliance_clause,
            "检查规则": "; ".join(check_rules),
            "扫描范围": "全量",
            "违规发现": "; ".join(violations) if violations else "",
            "修复意见": self._generate_remediation(violations),
            "扫描时间": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "已通过" if passed else "存在违规",
            "pending": not passed,
            "abnormal": not passed,
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(self.MODULE).append(scan)

        # 如果有违规，创建治理问题
        if not passed:
            self._create_compliance_issue(asset_code, violations)

        store.log_change(
            self.MODULE, scan_id, "合规扫描",
            before=None, after=f"{'通过' if passed else '违规'}: {len(violations)}项违规",
            source="pipeline_5", scope="compliance_scan"
        )

        self.emit_event("compliance_scanned", {
            "scan_id": scan_id,
            "asset_code": asset_code,
            "passed": passed,
            "violations_count": len(violations),
        })

        return {
            "scan": scan,
            "passed": passed,
            "violations": violations,
            "pipeline": "compliance_scan->issue_track" if not passed else None,
        }

    def _generate_remediation(self, violations: list[str]) -> str:
        if not violations:
            return "无需修复"
        return f"发现{len(violations)}项违规: {'; '.join(violations)}。建议逐一确认并制定修复方案。"

    def scan_fields(
        self,
        asset_code: str,
        partition: str,
        observed_on: str,
        hits: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """字段级扫描：逐条款登记违规明细，核验豁免后把结论回写到待办。

        hits 每项形如 {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"}。
        同一字段命中多个条款会产生多条明细，各自核验豁免，不会互相串用凭据。
        """
        observed = engine.normalize_date(observed_on)
        if not observed:
            return {"ok": False, "error": "观测日期无法解析", "violations": []}

        details: list[dict[str, Any]] = []
        for hit in hits:
            record, missing = engine.register_violation({
                "目标资产": asset_code,
                "数据分区": partition,
                "违规字段": hit.get("违规字段", ""),
                "合规条款": hit.get("合规条款", ""),
                "检查规则": hit.get("检查规则", "通用检查"),
                "观测日期": observed,
                "违规描述": hit.get("违规描述", ""),
            }, source="compliance_scan")
            if record is not None:
                details.append(record)
            elif missing:
                details.append({"错误": f"字段{hit.get('违规字段', '')}缺少必填项：{'、'.join(missing)}"})

        scan_ids = {int(row["扫描id"]) for row in details if row.get("扫描id")}
        for scan_id in scan_ids:
            engine.refresh_scan(scan_id)
        scans = [store.find(self.MODULE, scan_id) for scan_id in sorted(scan_ids)]
        scans = [row for row in scans if row is not None]

        need = [row for row in details if row.get("整改要求") == "需要整改"]
        exempted = [row for row in details if row.get("整改要求") == "无需整改"]
        passed = not need

        for scan in scans:
            store.log_change(
                self.MODULE, scan["id"], "字段级合规扫描",
                before=None,
                after=f"{partition}分区观测日{observed}: {len(need)}项整改/{len(exempted)}项豁免",
                source="exemption_engine", scope="compliance_scan->clause_exemption->issue_track",
            )

        self.emit_event("compliance_fields_scanned", {
            "asset_code": asset_code,
            "partition": partition,
            "observed_on": observed,
            "passed": passed,
            "need_remediation": len(need),
            "exempted": len(exempted),
            "clauses": sorted({row.get("合规条款", "") for row in need}),
        })

        return {
            "ok": True,
            "scans": scans,
            "details": details,
            "passed": passed,
            "fields_to_remediate": [
                {"违规字段": row.get("违规字段"), "合规条款": row.get("合规条款"),
                 "检查规则": row.get("检查规则"), "核验结论": row.get("核验结论")}
                for row in need
            ],
            "exempted_fields": [
                {"违规字段": row.get("违规字段"), "合规条款": row.get("合规条款"),
                 "豁免编号": row.get("豁免编号"), "豁免适用日期": row.get("豁免适用日期")}
                for row in exempted
            ],
            "pipeline": "compliance_scan->clause_exemption->issue_track",
        }

    def reevaluate_exemptions(self, observed_on: str | None = None) -> dict[str, Any]:
        """补登记或驳回豁免后，重算观测日的扫描明细和整改范围。"""
        return engine.reevaluate_all(observed_on)

    def remediation_scope(self, **filters: Any) -> dict[str, Any]:
        return engine.remediation_scope(**filters)

    def _create_compliance_issue(self, asset_code: str, violations: list[str]) -> None:
        """合规扫描发现违规时创建治理问题。"""
        issue_id = store.next_id("issue_track")
        issue = {
            "id": issue_id,
            "问题编号": f"ISSU-COMP-{issue_id:04d}",
            "关联资产": asset_code,
            "问题类型": "合规违规",
            "严重级别": "需关注" if len(violations) <= 2 else "严重",
            "指派处理": "合规管理员",
            "当前状态": "待处理",
            "修复方案": "逐项处理合规违规问题: " + "; ".join(violations),
            "截止日期": _time.strftime("%Y-%m-%d", _time.localtime(_time.time() + 86400 * 14)),
            "status": "待确认",
            "pending": True,
            "abnormal": True,
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows("issue_track").append(issue)

    def get_scans_by_asset(self, asset_code: str) -> list[dict[str, Any]]:
        return store.find_by_field(self.MODULE, "目标资产", asset_code)

    def get_violations(self) -> list[dict[str, Any]]:
        return [r for r in store.rows(self.MODULE) if r.get("status") == "存在违规"]