"""条款豁免场景演示：直接运行即可看到 SEA / 六月订单分区的复查结论。

用法（在 backend 目录下）：
    .venv/bin/python -m app.demo_clause_exemption
"""
from __future__ import annotations

import json

from app.services import SERVICES
from app.store import store


def _print(title: str, payload: object) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def sea_scenario() -> None:
    scan, exemption, issue = (
        SERVICES["compliance_scan"], SERVICES["clause_exemption"], SERVICES["issue_track"],
    )
    store.reset()
    store.rows("clause_exemption").clear()

    # 九日：同一列“运费”分别命中 C7、C9；此时还没有任何豁免。
    scan.scan_fields("SEA-SALES", "SEA", "2026-10-09", [
        {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
        {"违规字段": "运费", "合规条款": "C9", "检查规则": "完整性"},
    ])
    # 十一日：C7 又一次命中。
    scan.scan_fields("SEA-SALES", "SEA", "2026-10-11", [
        {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
    ])
    # 十日之后才补交 C7 的豁免，有效期只写十月一日至十日。
    exemption.create_entry({
        "适用条款": "C7", "数据分区": "SEA", "违规字段": "运费",
        "检查规则": "*", "生效日期": "2026-10-01", "失效日期": "2026-10-10",
        "申请日期": "2026-10-12", "申请依据": "区域运费口径差异已备案",
    })

    _print("九日复查：仍需整改的字段", [
        {k: row[k] for k in ("违规字段", "合规条款", "核验结论")}
        for row in scan.remediation_scope(observation_date="2026-10-09")["fields_to_remediate"]
        if row["目标资产"] == "SEA-SALES"
    ])
    _print("九日复查：整改员还能凭 C9 找到运费", [
        {"问题编号": row["问题编号"], "合规条款": row["合规条款"], "违规字段": row["违规字段"],
         "状态": row["status"], "豁免编号": row.get("豁免编号", "")}
        for row in issue.get_dispatchable_issues("2026-10-09") if row["关联资产"] == "SEA-SALES"
    ])
    _print("九日已派单、后补豁免的追溯", [
        {"问题编号": row["问题编号"], "合规条款": row["合规条款"],
         "状态": row["status"], "豁免编号": row["豁免编号"], "豁免适用日期": row["豁免适用日期"]}
        for row in issue.get_exempted_issues("2026-10-09") if row["关联资产"] == "SEA-SALES"
    ])
    _print("十一日的 C7 不被九日的凭据带走", [
        {"合规条款": row["合规条款"], "核验结论": row["核验结论"], "整改要求": row["整改要求"]}
        for row in store.rows("scan_violation")
        if row["目标资产"] == "SEA-SALES" and row["观测日期"] == "2026-10-11"
    ])


def june_scenario() -> None:
    scan, exemption, issue = (
        SERVICES["compliance_scan"], SERVICES["clause_exemption"], SERVICES["issue_track"],
    )
    store.reset()
    store.rows("clause_exemption").clear()

    # 六月订单分区先豁免一次完整性检查。
    exemption.create_entry({
        "适用条款": "C3", "数据分区": "ORDER_202606", "违规字段": "订单金额",
        "检查规则": "完整性", "生效日期": "2026-06-01", "失效日期": "2026-06-30",
    })
    # 同一列随后命中脱敏条款，条款不同、规则不同，必须单独核对。
    result = scan.scan_fields("ORDER", "ORDER_202606", "2026-06-15", [
        {"违规字段": "订单金额", "合规条款": "C3", "检查规则": "完整性"},
        {"违规字段": "订单金额", "合规条款": "C11", "检查规则": "脱敏完备性"},
    ])
    _print("六月分区扫描明细", [
        {"违规字段": row["违规字段"], "合规条款": row["合规条款"], "检查规则": row["检查规则"],
         "核验结论": row["核验结论"], "整改要求": row["整改要求"], "豁免编号": row["豁免编号"]}
        for row in result["details"]
    ])
    scope = scan.remediation_scope(observation_date="2026-06-15", assignee="合规管理员")
    _print("经办人的整改范围", scope["summary"])
    _print("经办人仍需整改的字段", [
        {"违规字段": row["违规字段"], "合规条款": row["合规条款"], "核验结论": row["核验结论"]}
        for row in scope["fields_to_remediate"]
    ])


if __name__ == "__main__":
    sea_scenario()
    june_scenario()
