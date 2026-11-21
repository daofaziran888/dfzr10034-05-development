"""条款豁免测试：按条款/分区/观测日核验，补报追溯、重复提交与整改范围。"""
import app.store as store_mod
from app.services import SERVICES


def _s():
    store_mod.store.reset()
    return SERVICES


def _clear_exemptions():
    """清掉种子里的示例豁免，测试“先无豁免、后补报”的时间线。"""
    store_mod.store.rows("clause_exemption").clear()


def _rows(module):
    return store_mod.store.rows(module)


def _issue(asset, clause):
    return next(
        row for row in _rows("issue_track")
        if row.get("关联资产") == asset and row.get("合规条款") == clause
    )


class TestClauseScope:
    def test_c7_exemption_does_not_cover_c9(self):
        s = _s()
        result = s["compliance_scan"].scan_fields("T-SEA", "SEA", "2026-10-09", [
            {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
            {"违规字段": "运费", "合规条款": "C9", "检查规则": "完整性"},
        ])
        details = {row["合规条款"]: row for row in result["details"]}
        assert details["C7"]["核验结论"] == "豁免成立"
        assert details["C7"]["整改要求"] == "无需整改"
        assert details["C7"]["豁免编号"] == "EXEM-0001"
        # C9 没有被 C7 的凭据带走
        assert details["C9"]["核验结论"] == "无适用豁免"
        assert details["C9"]["整改要求"] == "需要整改"
        assert result["passed"] is False

    def test_issue_track_only_dispatches_clause_still_applicable(self):
        s = _s()
        s["compliance_scan"].scan_fields("T-SEA2", "SEA", "2026-10-09", [
            {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
            {"违规字段": "运费", "合规条款": "C9", "检查规则": "完整性"},
        ])
        issues = [row for row in _rows("issue_track") if row.get("关联资产") == "T-SEA2"]
        assert [row["合规条款"] for row in issues] == ["C9"]
        dispatchable = s["issue_track"].get_dispatchable_issues("2026-10-09")
        assert [row["合规条款"] for row in dispatchable if row["关联资产"] == "T-SEA2"] == ["C9"]

    def test_exemption_only_for_one_rule_does_not_cover_another_rule(self):
        s = _s()
        s["clause_exemption"].create_entry({
            "适用条款": "C3", "数据分区": "JUN", "违规字段": "订单金额",
            "检查规则": "完整性", "生效日期": "2026-06-01", "失效日期": "2026-06-30",
        })
        result = s["compliance_scan"].scan_fields("T-JUN", "JUN", "2026-06-15", [
            {"违规字段": "订单金额", "合规条款": "C3", "检查规则": "完整性"},
            {"违规字段": "订单金额", "合规条款": "C3", "检查规则": "脱敏完备性"},
        ])
        by_rule = {row["检查规则"]: row for row in result["details"]}
        assert by_rule["完整性"]["整改要求"] == "无需整改"
        assert by_rule["脱敏完备性"]["核验结论"] == "无适用豁免"
        assert by_rule["脱敏完备性"]["整改要求"] == "需要整改"


class TestLateExemption:
    def test_late_exemption_retroactively_covers_observation_date(self):
        s = _s()
        _clear_exemptions()
        s["compliance_scan"].scan_fields("T-LATE", "SEA", "2026-10-09", [
            {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
            {"违规字段": "运费", "合规条款": "C9", "检查规则": "完整性"},
        ])
        original = _issue("T-LATE", "C7")
        assert original["status"] == "待确认"

        entry, missing = s["clause_exemption"].create_entry({
            "适用条款": "C7", "数据分区": "SEA", "违规字段": "运费",
            "生效日期": "2026-10-01", "失效日期": "2026-10-10",
            "申请日期": "2026-10-12", "申请依据": "补交备案",
        })
        assert missing == []
        assert entry["申请方式"] == "补报"

        # 已派出的整改单仍然保留，但回写了豁免凭据和适用日期
        assert original["status"] == "已豁免"
        assert original["豁免编号"] == entry["豁免编号"]
        assert original["豁免适用日期"] == "2026-10-01 至 2026-10-10"
        trace = s["issue_track"].trace_exemption(original["id"])
        assert trace["exemption"]["豁免编号"] == entry["豁免编号"]
        assert trace["exempted_on"] == "2026-10-01 至 2026-10-10"

        # C9 仍可凭条款找到运费
        c9_issue = _issue("T-LATE", "C9")
        assert c9_issue["status"] == "待确认"
        scope = s["compliance_scan"].remediation_scope(observation_date="2026-10-09")
        need = [row for row in scope["fields_to_remediate"] if row["目标资产"] == "T-LATE"]
        assert [(row["违规字段"], row["合规条款"]) for row in need] == [("运费", "C9")]

    def test_late_exemption_does_not_cover_later_observation(self):
        s = _s()
        s["compliance_scan"].scan_fields("T-LATE2", "SEA", "2026-10-09", [
            {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
        ])
        s["clause_exemption"].create_entry({
            "适用条款": "C7", "数据分区": "SEA", "违规字段": "运费",
            "生效日期": "2026-10-01", "失效日期": "2026-10-10", "申请日期": "2026-10-12",
        })
        later = s["compliance_scan"].scan_fields("T-LATE2", "SEA", "2026-10-11", [
            {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
        ])
        row = later["details"][0]
        assert row["核验结论"] == "豁免不覆盖"
        assert row["整改要求"] == "需要整改"
        assert row["豁免编号"] == ""

    def test_scope_check_reports_reason(self):
        s = _s()
        inside = s["clause_exemption"].check_field(
            clause="C7", partition="SEA", field="运费", observed_on="2026-10-05",
        )
        outside = s["clause_exemption"].check_field(
            clause="C7", partition="SEA", field="运费", observed_on="2026-10-11",
        )
        other_clause = s["clause_exemption"].check_field(
            clause="C9", partition="SEA", field="运费", observed_on="2026-10-05",
        )
        assert inside["exempted"] is True
        assert outside["exempted"] is False and outside["conclusion"] == "豁免不覆盖"
        assert other_clause["exempted"] is False and other_clause["conclusion"] == "无适用豁免"


class TestDuplicateSubmission:
    def test_duplicate_exemption_keeps_single_active_credential(self):
        s = _s()
        first, _ = s["clause_exemption"].create_entry({
            "适用条款": "C7", "数据分区": "SEA", "违规字段": "运费",
            "生效日期": "2026-10-01", "失效日期": "2026-10-10",
        })
        second, _ = s["clause_exemption"].create_entry({
            "适用条款": "C7", "数据分区": "SEA", "违规字段": "运费",
            "生效日期": "2026-10-01", "失效日期": "2026-10-10",
        })
        assert second["豁免编号"] == first["豁免编号"]
        active = [row for row in _rows("clause_exemption")
                  if row["适用条款"] == "C7" and row["数据分区"] == "SEA" and row["status"] == "已生效"]
        assert len(active) == 1

    def test_duplicate_violation_keeps_single_remediation(self):
        s = _s()
        hit = {"违规字段": "运费", "合规条款": "C9", "检查规则": "完整性"}
        s["compliance_scan"].scan_fields("T-DUP", "SEA", "2026-10-09", [hit])
        s["compliance_scan"].scan_fields("T-DUP", "SEA", "2026-10-09", [hit])
        rows = [row for row in _rows("scan_violation")
                if row["目标资产"] == "T-DUP" and row["合规条款"] == "C9"]
        issues = [row for row in _rows("issue_track")
                  if row.get("关联资产") == "T-DUP" and row.get("合规条款") == "C9"]
        assert len(rows) == 1
        assert len(issues) == 1


class TestAuditTrail:
    def test_check_detail_records_that_day_reason(self):
        s = _s()
        s["compliance_scan"].scan_fields("T-AUD", "SEA", "2026-10-09", [
            {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
            {"违规字段": "运费", "合规条款": "C9", "检查规则": "完整性"},
        ])
        checks = s["exemption_check"].get_by_observation_date("2026-10-09")
        assert len(checks) == 2
        by_clause = {row["合规条款"]: row for row in checks}
        assert by_clause["C7"]["豁免编号"] == "EXEM-0001"
        assert by_clause["C9"]["豁免编号"] == ""
        assert by_clause["C7"]["豁免适用起"] == "2026-10-01"
        assert by_clause["C7"]["豁免适用止"] == "2026-10-10"

    def test_reevaluate_after_late_submission_appends_new_round(self):
        s = _s()
        _clear_exemptions()
        s["compliance_scan"].scan_fields("T-AUD2", "SEA", "2026-10-09", [
            {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
        ])
        s["clause_exemption"].create_entry({
            "适用条款": "C7", "数据分区": "SEA", "违规字段": "运费",
            "生效日期": "2026-10-01", "失效日期": "2026-10-10", "申请日期": "2026-10-12",
        })
        checks = s["exemption_check"].get_by_observation_date("2026-10-09")
        assert [row["核验结论"] for row in checks] == ["无适用豁免", "豁免成立"]
        summary = s["exemption_check"].get_audit_summary("2026-10-09")
        assert summary["by_conclusion"]["豁免成立"] == 1

    def test_revoking_exemption_restores_remediation(self):
        s = _s()
        _clear_exemptions()
        s["compliance_scan"].scan_fields("T-REV", "SEA", "2026-10-09", [
            {"违规字段": "运费", "合规条款": "C7", "检查规则": "完整性"},
        ])
        assert _issue("T-REV", "C7")["status"] == "待确认"
        entry, _ = s["clause_exemption"].create_entry({
            "适用条款": "C7", "数据分区": "SEA", "违规字段": "运费",
            "生效日期": "2026-10-01", "失效日期": "2026-10-10",
        })
        assert _issue("T-REV", "C7")["status"] == "已豁免"
        s["clause_exemption"].run_action(entry["id"], "驳回豁免")
        issue = _issue("T-REV", "C7")
        assert issue["status"] == "待确认"
        assert issue["豁免编号"] == ""
        scope = s["compliance_scan"].remediation_scope(observation_date="2026-10-09")
        assert any(row["目标资产"] == "T-REV" for row in scope["fields_to_remediate"])


class TestJunePartition:
    def test_partition_exemption_then_anonymization_clause_hits_same_column(self):
        s = _s()
        exemption, missing = s["clause_exemption"].create_entry({
            "适用条款": "C3", "数据分区": "ORDER_202606", "违规字段": "订单金额",
            "检查规则": "完整性", "生效日期": "2026-06-01", "失效日期": "2026-06-30",
        })
        assert missing == []
        result = s["compliance_scan"].scan_fields("T-ORD", "ORDER_202606", "2026-06-15", [
            {"违规字段": "订单金额", "合规条款": "C3", "检查规则": "完整性"},
            {"违规字段": "订单金额", "合规条款": "C11", "检查规则": "脱敏完备性"},
        ])
        details = {row["合规条款"]: row for row in result["details"]}
        assert details["C3"]["核验结论"] == "豁免成立"
        assert details["C3"]["豁免编号"] == exemption["豁免编号"]
        assert details["C11"]["整改要求"] == "需要整改"

        scope = s["compliance_scan"].remediation_scope(
            observation_date="2026-06-15", assignee="合规管理员",
        )
        assert scope["summary"]["clauses_to_remediate"] == ["C11"]
        assert scope["summary"]["clauses_exempted"] == ["C3"]
        assert len(scope["active_issues"]) == 1
        assert scope["active_issues"][0]["合规条款"] == "C11"
        assert scope["active_issues"][0]["违规字段"] == "订单金额"

    def test_partition_exemption_does_not_leak_to_other_partition(self):
        s = _s()
        s["clause_exemption"].create_entry({
            "适用条款": "C3", "数据分区": "ORDER_202606", "违规字段": "订单金额",
            "检查规则": "完整性", "生效日期": "2026-06-01", "失效日期": "2026-06-30",
        })
        result = s["compliance_scan"].scan_fields("T-ORD2", "ORDER_202607", "2026-06-15", [
            {"违规字段": "订单金额", "合规条款": "C3", "检查规则": "完整性"},
        ])
        assert result["details"][0]["整改要求"] == "需要整改"
