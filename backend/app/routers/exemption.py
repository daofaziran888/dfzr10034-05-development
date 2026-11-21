"""条款豁免与复查接口：字段级扫描、原件核验、整改范围和追溯链。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult
from app.services import service_for

router = APIRouter(prefix="/api", tags=["条款豁免"])


def _service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.post("/compliance_scan/field-scan", response_model=dict)
def field_scan(payload: dict[str, Any]) -> dict[str, Any]:
    """按分区、观测日逐字段逐条款扫描；每命中一条就独立核验豁免。"""
    service = _service("compliance_scan")
    asset = str(payload.get("目标资产") or "").strip()
    partition = str(payload.get("数据分区") or "").strip()
    observed_on = str(payload.get("观测日期") or "").strip()
    hits = payload.get("违规项") or []
    if not (asset and partition and observed_on):
        raise HTTPException(status_code=400, detail="目标资产、数据分区、观测日期不能为空")
    if not isinstance(hits, list) or not hits:
        raise HTTPException(status_code=400, detail="违规项必须是非空数组")
    return service.scan_fields(asset, partition, observed_on, hits)


@router.post("/clause_exemption/check", response_model=dict)
def check_exemption(payload: dict[str, Any]) -> dict[str, Any]:
    """核验某条款、分区、字段在指定观测日是否被豁免。"""
    service = _service("clause_exemption")
    clause = str(payload.get("合规条款") or payload.get("条款") or "").strip()
    partition = str(payload.get("数据分区") or payload.get("分区") or "").strip()
    field = str(payload.get("违规字段") or payload.get("字段") or "").strip()
    observed_on = str(payload.get("观测日期") or "").strip()
    if not (clause and partition and field and observed_on):
        raise HTTPException(status_code=400, detail="合规条款、数据分区、违规字段、观测日期不能为空")
    return service.check_field(
        clause=clause, partition=partition, field=field,
        rule=str(payload.get("检查规则") or ""), observed_on=observed_on,
    )


@router.get("/exemption-review", response_model=dict)
def exemption_review(
    observation_date: str = Query(default=""),
    assignee: str = Query(default=""),
    asset: str = Query(default=""),
    partition: str = Query(default=""),
) -> dict[str, Any]:
    """复查视图：某观测日仍需整改的字段、已豁免字段、经办人的整改范围。"""
    service = _service("compliance_scan")
    return service.remediation_scope(
        observation_date=observation_date or None,
        assignee=assignee or None,
        asset=asset or None,
        partition=partition or None,
    )


@router.get("/exemption_check/audit", response_model=dict)
def exemption_audit(observation_date: str = Query(default="")) -> dict[str, Any]:
    """条款豁免核验明细汇总。"""
    service = _service("exemption_check")
    return service.get_audit_summary(observation_date or None)


@router.get("/issue_track/{issue_id}/exemption-trace", response_model=dict)
def exemption_trace(issue_id: int) -> dict[str, Any]:
    """从已派出的整改单追到豁免凭据、适用日期和核验明细。"""
    service = _service("issue_track")
    trace = service.trace_exemption(issue_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"治理问题 {issue_id} 不存在或已归档")
    return trace


@router.post("/scan_violation/{entry_id}/recheck", response_model=ActionResult)
def recheck_violation(entry_id: int) -> ActionResult:
    """补登记豁免后，按原观测日重新核验单条违规明细。"""
    service = _service("scan_violation")
    entry, message = service.run_action(entry_id, "核验豁免")
    return ActionResult(ok=entry is not None, message=message, entry=entry)
