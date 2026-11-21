"""条款豁免领域引擎：按条款、分区、字段和观测日判定例外是否成立。

设计要点：
- 豁免有明确的生效日期和失效日期，按“原观测日”判定，而不是按补交申请的日期；
- 豁免只对写入的条款生效，同一字段被另一条款命中时不会被一起带走；
- 同一条款、分区、字段、规则和观测日只保留一条有效整改；
- 补报的豁免保留原有整改单，并把豁免适用日期回写到已经派出的条目上。

引擎只操作内存仓库，供 compliance_scan / clause_exemption / scan_violation /
exemption_check / issue_track 几个领域服务共同复用。
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime
from typing import Any

from app.store import store

EXEMPTION_MODULE = "clause_exemption"
VIOLATION_MODULE = "scan_violation"
CHECK_MODULE = "exemption_check"
SCAN_MODULE = "compliance_scan"
ISSUE_MODULE = "issue_track"

WILDCARDS = {"", "-", "*", "全部", "所有", "any", "all", "不限"}

EXEMPTION_LIST_FIELDS = [
    "豁免编号", "适用条款", "数据分区", "违规字段", "检查规则",
    "生效日期", "失效日期", "申请日期", "申请方式", "申请依据", "核验结论",
]
EXEMPTION_REQUIRED_FIELDS = ["适用条款", "生效日期", "失效日期"]

VIOLATION_LIST_FIELDS = [
    "明细编号", "扫描编号", "目标资产", "数据分区", "违规字段", "合规条款",
    "检查规则", "观测日期", "违规描述", "整改要求", "豁免编号", "核验结论",
]
VIOLATION_REQUIRED_FIELDS = ["目标资产", "违规字段", "合规条款", "观测日期"]

CHECK_LIST_FIELDS = [
    "核验编号", "扫描编号", "明细编号", "目标资产", "数据分区", "违规字段",
    "合规条款", "检查规则", "观测日期", "豁免编号", "豁免适用起",
    "豁免适用止", "核验结论", "整改要求", "核验时间",
]


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _is_wild(value: Any) -> bool:
    return _text(value).casefold() in WILDCARDS


def _same(left: Any, right: Any) -> bool:
    return _text(left).casefold() == _text(right).casefold()


def normalize_date(value: Any) -> str | None:
    """把常见日期写法收敛成 YYYY-MM-DD；无法解析时返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = _text(value).replace("/", "-").replace(".", "-")
    if not raw:
        return None
    raw = raw.split(" ")[0]
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _today() -> str:
    return _time.strftime("%Y-%m-%d")


def _now() -> str:
    return _time.strftime("%Y-%m-%d %H:%M:%S")


def _first(values: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if _text(values.get(key)):
            return values.get(key)
    return ""


def _mapped(values: dict[str, Any], aliases: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    """输入字段别名归一：中文全称、简写和英文键都接受。"""
    mapped = dict(values)
    for canonical, names in aliases.items():
        if _text(mapped.get(canonical)):
            continue
        found = _first(values, *names)
        if _text(found):
            mapped[canonical] = found
    return mapped


_EXEMPTION_ALIASES = {
    "适用条款": ("条款", "clause", "compliance_clause"),
    "数据分区": ("分区", "partition", "数据域"),
    "违规字段": ("字段", "field", "字段名称"),
    "检查规则": ("规则", "rule", "检查项"),
    "生效日期": ("起始日期", "开始日期", "适用起", "有效期起", "valid_from", "start_date"),
    "失效日期": ("结束日期", "截止日期", "适用止", "有效期止", "valid_to", "end_date"),
    "申请日期": ("申请日", "登记日期", "applied_on", "applied_at"),
    "申请依据": ("依据", "凭据", "reason", "basis"),
    "申请人": ("申报人", "applicant"),
    "核验结论": ("结论", "verification"),
    "豁免编号": ("编号", "exemption_code"),
}

_VIOLATION_ALIASES = {
    "目标资产": ("资产", "asset", "asset_code"),
    "数据分区": ("分区", "partition", "数据域"),
    "违规字段": ("字段", "field", "字段名称", "列名"),
    "合规条款": ("条款", "clause", "compliance_clause"),
    "检查规则": ("规则", "rule", "检查项", "检查类型"),
    "观测日期": ("日期", "报告日期", "扫描日期", "observed_on", "observation_date"),
    "违规描述": ("描述", "问题描述", "detail", "description"),
    "明细编号": ("编号", "violation_code"),
    "扫描编号": ("任务编号", "scan_code"),
}


def _scope_match(scope_value: Any, actual: Any) -> bool:
    return _is_wild(scope_value) or _same(scope_value, actual)


def clause_match(exemption_clause: Any, violation_clause: Any) -> bool:
    """条款必须逐条核对：C7 的豁免不能覆盖 C9。"""
    return _same(exemption_clause, violation_clause)


def date_covers(exemption: dict[str, Any], observed_on: Any) -> bool:
    observed = normalize_date(observed_on)
    start = normalize_date(exemption.get("生效日期"))
    end = normalize_date(exemption.get("失效日期"))
    if not (observed and start and end):
        return False
    return start <= observed <= end


def exemption_scope_matches(
    exemption: dict[str, Any],
    *,
    clause: Any,
    partition: Any,
    field: Any,
    rule: Any,
) -> bool:
    """判断豁免的适用范围是否覆盖一条违规（不含日期判断）。"""
    if not clause_match(exemption.get("适用条款"), clause):
        return False
    if not _scope_match(exemption.get("数据分区"), partition):
        return False
    if not _scope_match(exemption.get("违规字段"), field):
        return False
    if not _scope_match(exemption.get("检查规则"), rule):
        return False
    return True


def find_scope_exemption(**scope: Any) -> dict[str, Any] | None:
    """找到范围匹配的已生效豁免，忽略日期；用于区分“无豁免”和“豁免不覆盖”。"""
    matches = [
        row for row in store.rows(EXEMPTION_MODULE)
        if row.get("status") == "已生效"
        and exemption_scope_matches(row, **scope)
    ]
    return matches[-1] if matches else None


def find_matching_exemption(
    *,
    clause: Any,
    partition: Any,
    field: Any,
    rule: Any,
    observed_on: Any,
) -> dict[str, Any] | None:
    """找到真正适用于观测日的豁免；优先更具体的字段/规则范围。"""
    candidates = [
        row for row in store.rows(EXEMPTION_MODULE)
        if row.get("status") == "已生效"
        and exemption_scope_matches(row, clause=clause, partition=partition, field=field, rule=rule)
        and date_covers(row, observed_on)
    ]
    if not candidates:
        return None

    def specificity(row: dict[str, Any]) -> tuple[int, int, int, str]:
        return (
            2 if not _is_wild(row.get("违规字段")) else 0,
            2 if not _is_wild(row.get("检查规则")) else 0,
            1 if not _is_wild(row.get("数据分区")) else 0,
            _text(row.get("created_at")),
        )

    return max(candidates, key=specificity)


# ── 豁免申请 ──────────────────────────────────────────────────────


def apply_exemption(values: dict[str, Any], *, source: str = "manual") -> tuple[dict[str, Any] | None, list[str]]:
    """登记一条有期限的条款豁免，并立即回算受影响的违规明细和整改单。"""
    data = _mapped(values, _EXEMPTION_ALIASES)
    clause = _text(data.get("适用条款"))
    start = normalize_date(data.get("生效日期"))
    end = normalize_date(data.get("失效日期"))
    missing: list[str] = []
    if not clause:
        missing.append("适用条款")
    if not start:
        missing.append("生效日期")
    if not end:
        missing.append("失效日期")
    if missing:
        return None, missing
    if end < start:
        return None, ["失效日期不得早于生效日期"]

    partition = _text(data.get("数据分区")) or "*"
    field = _text(data.get("违规字段")) or "*"
    rule = _text(data.get("检查规则")) or "*"

    # 重复提交同一范围、同一有效期的豁免：沿用已有记录，不再生成第二条凭据。
    for row in store.rows(EXEMPTION_MODULE):
        if row.get("status") != "已生效":
            continue
        if (
            clause_match(row.get("适用条款"), clause)
            and _text(row.get("数据分区")) == partition
            and _text(row.get("违规字段")) == field
            and _text(row.get("检查规则")) == rule
            and normalize_date(row.get("生效日期")) == start
            and normalize_date(row.get("失效日期")) == end
        ):
            row["重复提交"] = "是"
            row["重复提交时间"] = _now()
            return row, []

    applied_on = normalize_date(data.get("申请日期")) or _today()

    related = [
        row for row in store.rows(VIOLATION_MODULE)
        if exemption_scope_matches(
            {"适用条款": clause, "数据分区": partition, "违规字段": field, "检查规则": rule},
            clause=row.get("合规条款"), partition=row.get("数据分区"),
            field=row.get("违规字段"), rule=row.get("检查规则"),
        )
    ]
    # 申请日晚于所覆盖的观测日即为补报：整改单可能已经派出，但豁免仍按原观测日生效。
    late = any(
        observed and observed < applied_on
        for observed in (normalize_date(row.get("观测日期")) for row in related)
    )
    mode = _text(data.get("申请方式")) or ("补报" if late else "事前申请")
    conclusion = "豁免成立"
    if mode == "补报":
        conclusion = "豁免成立（补报追溯）"

    exemption_id = store.next_id(EXEMPTION_MODULE)
    entry = {
        "id": exemption_id,
        "豁免编号": _text(data.get("豁免编号")) or f"EXEM-{exemption_id:04d}",
        "适用条款": clause,
        "数据分区": partition,
        "违规字段": field,
        "检查规则": rule,
        "生效日期": start,
        "失效日期": end,
        "申请日期": applied_on,
        "申请方式": mode,
        "申请依据": _text(data.get("申请依据")),
        "申请人": _text(data.get("申请人")) or "合规申请",
        "核验人": _text(data.get("核验人")) or "合规管理员",
        "核验结论": _text(data.get("核验结论")) or conclusion,
        "当前有效性": "有效期内" if _today() <= end else "已过期",
        "status": "已生效",
        "pending": False,
        "abnormal": False,
        "created_at": _now(),
        "updated_at": _now(),
    }
    store.rows(EXEMPTION_MODULE).append(entry)
    store.log_change(
        EXEMPTION_MODULE, exemption_id, "登记条款豁免",
        before=None,
        after=f"{entry['适用条款']} {entry['数据分区']} {start}~{end} ({mode})",
        source=source, scope="clause_exemption->scan_violation",
    )

    reevaluate_exemption(entry)
    return entry, []


def revoke_exemption(exemption: dict[str, Any]) -> None:
    """豁免被驳回或终止后，恢复原本需要整改的字段和整改单。"""
    reevaluate_exemption(exemption)


def reevaluate_exemption(exemption: dict[str, Any]) -> None:
    """按豁免当前状态重算所有范围相关的违规明细和已派出整改单。"""
    affected_scans: set[int] = set()
    for violation in list(store.rows(VIOLATION_MODULE)):
        if not exemption_scope_matches(
            exemption,
            clause=violation.get("合规条款"), partition=violation.get("数据分区"),
            field=violation.get("违规字段"), rule=violation.get("检查规则"),
        ):
            continue
        evaluate_violation(violation, force_check=True)
        scan_id = violation.get("扫描id")
        if scan_id:
            affected_scans.add(int(scan_id))

    for scan_id in affected_scans:
        refresh_scan(scan_id, scan_code=next(
            (v.get("扫描编号") for v in store.rows(VIOLATION_MODULE) if v.get("扫描id") == scan_id), ""
        ))

    # 已派出的问题：补报豁免后保留原条目，但回写豁免编号和适用日期；
    # 豁免被驳回/终止时则恢复为待处理。
    for issue in store.rows(ISSUE_MODULE):
        if issue.get("问题类型") != "合规违规":
            continue
        if not exemption_scope_matches(
            exemption,
            clause=issue.get("合规条款"), partition=issue.get("数据分区"),
            field=issue.get("违规字段"), rule=issue.get("检查规则"),
        ):
            continue
        if exemption.get("status") == "已生效" and date_covers(exemption, issue.get("观测日期")):
            _mark_issue_exempt(issue, exemption)
        elif issue.get("豁免编号") == exemption.get("豁免编号"):
            _reopen_issue(issue)


# ── 违规明细与核验 ────────────────────────────────────────────────


def _violation_key(record: dict[str, Any]) -> str:
    return "|".join([
        _text(record.get("目标资产")), _text(record.get("数据分区")),
        _text(record.get("违规字段")), _text(record.get("合规条款")),
        _text(record.get("检查规则")), _text(record.get("观测日期")),
    ])


def register_violation(values: dict[str, Any], *, source: str = "manual") -> tuple[dict[str, Any] | None, list[str]]:
    """登记一条字段级违规观测，按条款和分区核验豁免并回写待办。"""
    data = _mapped(values, _VIOLATION_ALIASES)
    asset = _text(data.get("目标资产"))
    field = _text(data.get("违规字段"))
    clause = _text(data.get("合规条款"))
    observed_on = normalize_date(data.get("观测日期"))
    missing: list[str] = []
    if not asset:
        missing.append("目标资产")
    if not field:
        missing.append("违规字段")
    if not clause:
        missing.append("合规条款")
    if not observed_on:
        missing.append("观测日期")
    if missing:
        return None, missing

    partition = _text(data.get("数据分区")) or "*"
    rule = _text(data.get("检查规则")) or "通用检查"

    # 同一条款、分区、字段、规则、观测日的重复提交只保留一条有效整改。
    for row in store.rows(VIOLATION_MODULE):
        if row.get("整改键") == _violation_key({
            "目标资产": asset, "数据分区": partition, "违规字段": field,
            "合规条款": clause, "检查规则": rule, "观测日期": observed_on,
        }):
            row["重复提交"] = "是"
            row["重复提交时间"] = _now()
            return row, []

    scan = ensure_scan(asset, observed_on, partition, clause, rule)
    violation_id = store.next_id(VIOLATION_MODULE)
    record = {
        "id": violation_id,
        "明细编号": _text(data.get("明细编号")) or f"SCAN-{violation_id:04d}",
        "扫描id": scan.get("id"),
        "扫描编号": scan.get("任务编号", ""),
        "目标资产": asset,
        "数据分区": partition,
        "违规字段": field,
        "合规条款": clause,
        "检查规则": rule,
        "观测日期": observed_on,
        "违规描述": _text(data.get("违规描述")) or f"{partition}分区字段{field}命中{clause}",
        "整改键": _violation_key({
            "目标资产": asset, "数据分区": partition, "违规字段": field,
            "合规条款": clause, "检查规则": rule, "观测日期": observed_on,
        }),
        "status": "待核验",
        "pending": True,
        "abnormal": False,
        "created_at": _now(),
    }
    store.rows(VIOLATION_MODULE).append(record)
    evaluate_violation(record, force_check=True)
    refresh_scan(scan["id"], scan_code=scan.get("任务编号", ""))

    store.log_change(
        VIOLATION_MODULE, violation_id, "登记扫描违规",
        before=None,
        after=f"{asset}/{partition}/{field} {clause} 观测日{observed_on}",
        source=source, scope="scan_violation->issue_track",
    )
    return record, []


def ensure_scan(asset: str, observed_on: str, partition: str, clause: str, rule: str) -> dict[str, Any]:
    """同一资产、分区、观测日复用一个扫描任务，避免逐字段生成大量扫描单。"""
    for row in store.rows(SCAN_MODULE):
        if (
            row.get("观测日期") == observed_on
            and row.get("目标资产") == asset
            and _text(row.get("数据分区")) == partition
        ):
            return row

    scan_id = store.next_id(SCAN_MODULE)
    scan = {
        "id": scan_id,
        "任务编号": f"COMP-{scan_id:04d}",
        "目标资产": asset,
        "合规条款": clause,
        "检查规则": rule,
        "扫描范围": f"{partition}分区 / 字段级",
        "数据分区": partition,
        "观测日期": observed_on,
        "违规发现": "",
        "修复意见": "",
        "扫描时间": _now(),
        "status": "扫描中",
        "pending": True,
        "abnormal": False,
        "created_at": _now(),
    }
    store.rows(SCAN_MODULE).append(scan)
    return scan


def refresh_scan(scan_id: int, *, scan_code: str = "") -> None:
    scan = store.find(SCAN_MODULE, scan_id)
    if scan is None:
        return
    related = [
        row for row in store.rows(VIOLATION_MODULE)
        if row.get("扫描id") == scan_id or (scan_code and row.get("扫描编号") == scan_code)
    ]
    clauses = list(dict.fromkeys(_text(row.get("合规条款")) for row in related if _text(row.get("合规条款"))))
    rules = list(dict.fromkeys(_text(row.get("检查规则")) for row in related if _text(row.get("检查规则"))))
    need = [row for row in related if row.get("整改要求") == "需要整改"]
    exempted = [row for row in related if row.get("整改要求") == "无需整改"]

    if related:
        scan["合规条款"] = "; ".join(clauses)
        scan["检查规则"] = "; ".join(rules)
        scan["违规发现"] = (
            f"共{len(related)}项字段违规，{len(need)}项需整改，{len(exempted)}项豁免成立"
        )
        scan["修复意见"] = (
            "按仍适用的违规条款逐项整改: " + "; ".join(sorted({
                f"{row.get('数据分区')}/{row.get('违规字段')}({row.get('合规条款')})"
                for row in need
            }))
        ) if need else "豁免范围内无需整改"
        scan["status"] = "存在违规" if need else "已通过"
        scan["pending"] = bool(need)
        scan["abnormal"] = bool(need)
    scan["updated_at"] = _now()


def evaluate_violation(violation: dict[str, Any], *, force_check: bool = False) -> dict[str, Any]:
    """按观测日核验豁免，更新违规明细并维护整改单。"""
    match = find_matching_exemption(
        clause=violation.get("合规条款"), partition=violation.get("数据分区"),
        field=violation.get("违规字段"), rule=violation.get("检查规则"),
        observed_on=violation.get("观测日期"),
    )
    scope_only = find_scope_exemption(
        clause=violation.get("合规条款"), partition=violation.get("数据分区"),
        field=violation.get("违规字段"), rule=violation.get("检查规则"),
    )
    previous = violation.get("核验结论")

    if match is not None:
        conclusion = "豁免成立"
        requirement = "无需整改"
        status = "已豁免"
        note = (
            f"豁免{match.get('豁免编号')}（{match.get('适用条款')}/{match.get('数据分区')}"
            f"/{match.get('违规字段')}）有效期{match.get('生效日期')}至{match.get('失效日期')}，"
            f"覆盖观测日{violation.get('观测日期')}"
        )
        if match.get("申请方式") == "补报":
            note += "；该豁免为补报，追溯至原观测日有效"
        violation.update({
            "豁免编号": match.get("豁免编号", ""),
            "豁免适用起": match.get("生效日期", ""),
            "豁免适用止": match.get("失效日期", ""),
            "豁免适用日期": f"{match.get('生效日期')} 至 {match.get('失效日期')}",
            "核验结论": conclusion,
            "整改要求": requirement,
            "核验说明": note,
            "status": status,
            "pending": False,
            "abnormal": False,
        })
    else:
        if scope_only is not None:
            conclusion = "豁免不覆盖"
            note = (
                f"存在条款{scope_only.get('适用条款')}的豁免{scope_only.get('豁免编号')}，"
                f"但有效期{scope_only.get('生效日期')}至{scope_only.get('失效日期')}"
                f"不包含观测日{violation.get('观测日期')}"
            )
        else:
            conclusion = "无适用豁免"
            note = f"未找到同时匹配条款{violation.get('合规条款')}、分区{violation.get('数据分区')}和观测日{violation.get('观测日期')}的豁免"
        requirement = "需要整改"
        status = "需整改"
        violation.update({
            "豁免编号": "",
            "豁免适用起": "",
            "豁免适用止": "",
            "豁免适用日期": "",
            "核验结论": conclusion,
            "整改要求": requirement,
            "核验说明": note,
            "status": status,
            "pending": True,
            "abnormal": True,
        })

    violation["核验时间"] = _now()

    if force_check or previous != conclusion:
        _append_check(violation, conclusion, requirement, note)
    _sync_issue(violation)
    return violation


def _append_check(violation: dict[str, Any], conclusion: str, requirement: str, note: str) -> dict[str, Any]:
    check_id = store.next_id(CHECK_MODULE)
    round_no = 1 + sum(
        1 for row in store.rows(CHECK_MODULE)
        if row.get("明细编号") == violation.get("明细编号")
    )
    check = {
        "id": check_id,
        "核验编号": f"CHK-{check_id:04d}",
        "核验轮次": str(round_no),
        "扫描编号": violation.get("扫描编号", ""),
        "明细编号": violation.get("明细编号", ""),
        "目标资产": violation.get("目标资产", ""),
        "数据分区": violation.get("数据分区", ""),
        "违规字段": violation.get("违规字段", ""),
        "合规条款": violation.get("合规条款", ""),
        "检查规则": violation.get("检查规则", ""),
        "观测日期": violation.get("观测日期", ""),
        "豁免编号": violation.get("豁免编号", ""),
        "豁免适用起": violation.get("豁免适用起", ""),
        "豁免适用止": violation.get("豁免适用止", ""),
        "核验结论": conclusion,
        "整改要求": requirement,
        "核验说明": note,
        "核验时间": _now(),
        "status": conclusion,
        "pending": False,
        "abnormal": conclusion != "豁免成立",
        "created_at": _now(),
    }
    store.rows(CHECK_MODULE).append(check)
    return check


def _find_issue(violation: dict[str, Any]) -> dict[str, Any] | None:
    key = violation.get("整改键", "")
    if key:
        for issue in store.rows(ISSUE_MODULE):
            if issue.get("问题类型") == "合规违规" and issue.get("整改键") == key:
                return issue
    return None


def _sync_issue(violation: dict[str, Any]) -> dict[str, Any] | None:
    existing = _find_issue(violation)
    if violation.get("整改要求") == "需要整改":
        if existing is not None:
            if existing.get("status") == "已豁免":
                _reopen_issue(existing)
            existing["整改要求"] = "需要整改"
            existing["核验结论"] = violation.get("核验结论", "")
            existing["核验说明"] = violation.get("核验说明", "")
            existing["updated_at"] = _now()
            return existing
        return _create_issue(violation)

    if existing is not None:
        _mark_issue_exempt(existing, {
            "豁免编号": violation.get("豁免编号"),
            "生效日期": violation.get("豁免适用起"),
            "失效日期": violation.get("豁免适用止"),
            "申请方式": "补报" if "补报" in _text(violation.get("核验说明")) else "",
            "适用条款": violation.get("合规条款"),
            "数据分区": violation.get("数据分区"),
            "违规字段": violation.get("违规字段"),
            "检查规则": violation.get("检查规则"),
        })
    return existing


def _create_issue(violation: dict[str, Any]) -> dict[str, Any]:
    issue_id = store.next_id(ISSUE_MODULE)
    issue = {
        "id": issue_id,
        "问题编号": f"ISSU-COMP-{issue_id:04d}",
        "关联资产": violation.get("目标资产", ""),
        "问题类型": "合规违规",
        "严重级别": "需关注",
        "指派处理": "合规管理员",
        "当前状态": "待处理",
        "修复方案": (
            f"整改{violation.get('数据分区')}分区字段{violation.get('违规字段')}"
            f"（条款{violation.get('合规条款')}，规则{violation.get('检查规则')}，"
            f"观测日{violation.get('观测日期')}）"
        ),
        "截止日期": _time.strftime("%Y-%m-%d", _time.localtime(_time.time() + 86400 * 14)),
        "数据分区": violation.get("数据分区", ""),
        "违规字段": violation.get("违规字段", ""),
        "合规条款": violation.get("合规条款", ""),
        "检查规则": violation.get("检查规则", ""),
        "观测日期": violation.get("观测日期", ""),
        "整改键": violation.get("整改键", ""),
        "整改要求": "需要整改",
        "豁免编号": "",
        "豁免适用起": "",
        "豁免适用止": "",
        "豁免适用日期": "",
        "核验结论": violation.get("核验结论", ""),
        "status": "待确认",
        "pending": True,
        "abnormal": True,
        "created_at": _now(),
    }
    store.rows(ISSUE_MODULE).append(issue)
    store.log_change(
        ISSUE_MODULE, issue_id, "分派合规整改",
        before=None, after=f"{issue['合规条款']} {issue['数据分区']}/{issue['违规字段']}",
        source="exemption_engine", scope="compliance_scan->issue_track",
    )
    return issue


def _mark_issue_exempt(issue: dict[str, Any], exemption: dict[str, Any]) -> None:
    """保留已派出的整改单，回写豁免编号和适用日期，供复查追溯。"""
    previous_status = issue.get("status", "")
    issue["豁免编号"] = exemption.get("豁免编号", "")
    issue["豁免适用起"] = exemption.get("生效日期", "")
    issue["豁免适用止"] = exemption.get("失效日期", "")
    issue["豁免适用日期"] = f"{exemption.get('生效日期')} 至 {exemption.get('失效日期')}"
    issue["核验结论"] = "豁免成立（补报追溯）" if exemption.get("申请方式") == "补报" else "豁免成立"
    issue["整改要求"] = "无需整改"
    issue["当前状态"] = "豁免成立"
    issue["status"] = "已豁免"
    issue["pending"] = False
    issue["abnormal"] = False
    issue["updated_at"] = _now()
    store.log_change(
        ISSUE_MODULE, issue.get("id", 0), "豁免回写整改单",
        before=previous_status, after=f"已豁免 {issue['豁免适用日期']}",
        source="exemption_engine", scope="clause_exemption->issue_track",
    )


def _reopen_issue(issue: dict[str, Any]) -> None:
    issue["豁免编号"] = ""
    issue["豁免适用起"] = ""
    issue["豁免适用止"] = ""
    issue["豁免适用日期"] = ""
    issue["整改要求"] = "需要整改"
    issue["当前状态"] = "待处理"
    issue["status"] = "待确认"
    issue["pending"] = True
    issue["abnormal"] = True
    issue["updated_at"] = _now()


# ── 查询与整改范围 ────────────────────────────────────────────────


def list_rows(module: str, **filters: Any) -> list[dict[str, Any]]:
    rows = list(store.rows(module))
    for key, value in filters.items():
        if value in (None, ""):
            continue
        rows = [row for row in rows if _same(row.get(key), value)]
    return rows


def remediation_scope(
    *,
    observation_date: Any = None,
    assignee: Any = None,
    asset: Any = None,
    partition: Any = None,
) -> dict[str, Any]:
    """给出某观测日仍需整改的字段清单与已豁免字段清单。"""
    observed = normalize_date(observation_date) if observation_date else None

    def matches(row: dict[str, Any]) -> bool:
        if observed and normalize_date(row.get("观测日期")) != observed:
            return False
        if asset and not _same(row.get("目标资产"), asset):
            return False
        if partition and not _same(row.get("数据分区"), partition):
            return False
        return True

    violations = [row for row in store.rows(VIOLATION_MODULE) if matches(row)]
    issues = [row for row in store.rows(ISSUE_MODULE)
              if row.get("问题类型") == "合规违规" and matches(row)]
    if assignee:
        issues = [row for row in issues if _same(row.get("指派处理"), assignee)]

    active_issues = [row for row in issues if row.get("整改要求") != "无需整改" and row.get("status") != "已豁免"]
    exempted_issues = [row for row in issues if row.get("status") == "已豁免"]
    active_keys = {row.get("整改键") for row in active_issues if row.get("整改键")}

    need = [row for row in violations if row.get("整改要求") == "需要整改"]
    exempted = [row for row in violations if row.get("整改要求") == "无需整改"]

    def field_item(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "目标资产": row.get("目标资产", ""),
            "数据分区": row.get("数据分区", ""),
            "违规字段": row.get("违规字段", ""),
            "合规条款": row.get("合规条款", ""),
            "检查规则": row.get("检查规则", ""),
            "观测日期": row.get("观测日期", ""),
            "核验结论": row.get("核验结论", ""),
            "豁免编号": row.get("豁免编号", ""),
            "豁免适用日期": row.get("豁免适用日期", ""),
            "关联问题编号": next(
                (issue.get("问题编号") for issue in issues
                 if issue.get("整改键") == row.get("整改键")), "",
            ),
        }

    fields_to_remediate = [field_item(row) for row in need]
    exempted_fields = [field_item(row) for row in exempted]
    return {
        "observation_date": observed or "",
        "assignee": _text(assignee),
        "asset": _text(asset),
        "partition": _text(partition),
        "fields_to_remediate": fields_to_remediate,
        "exempted_fields": exempted_fields,
        "active_issues": active_issues,
        "exempted_issues": exempted_issues,
        "summary": {
            "need_remediation": len(need),
            "exempted": len(exempted),
            "active_issues": len(active_issues),
            "exempted_issues": len(exempted_issues),
            "clauses_to_remediate": sorted({row.get("合规条款", "") for row in need}),
            "clauses_exempted": sorted({row.get("合规条款", "") for row in exempted}),
        },
        "pipeline": "compliance_scan->clause_exemption->issue_track",
    }


def reevaluate_all(observation_date: Any = None) -> dict[str, Any]:
    observed = normalize_date(observation_date) if observation_date else None
    count = 0
    scans: set[int] = set()
    for row in store.rows(VIOLATION_MODULE):
        if observed and normalize_date(row.get("观测日期")) != observed:
            continue
        evaluate_violation(row, force_check=False)
        count += 1
        if row.get("扫描id"):
            scans.add(int(row["扫描id"]))
    for scan_id in scans:
        refresh_scan(scan_id)
    return {"ok": True, "reevaluated": count, "observation_date": observed or ""}
