/** 条款豁免与复查接口：字段级扫描、核实、整改范围与追溯。 */
import { request } from './client'
import type { EntryRow } from '@/data/types'

export type RemediationField = {
  目标资产: string
  数据分区: string
  违规字段: string
  合规条款: string
  检查规则: string
  观测日期: string
  核验结论: string
  豁免编号: string
  豁免适用日期: string
  关联问题编号: string
}

export type RemediationScope = {
  observation_date: string
  assignee: string
  asset: string
  partition: string
  fields_to_remediate: RemediationField[]
  exempted_fields: RemediationField[]
  active_issues: EntryRow[]
  exempted_issues: EntryRow[]
  summary: {
    need_remediation: number
    exempted: number
    active_issues: number
    exempted_issues: number
    clauses_to_remediate: string[]
    clauses_exempted: string[]
  }
}

export type FieldScanResult = {
  ok: boolean
  passed: boolean
  fields_to_remediate: { 违规字段: string; 合规条款: string; 检查规则: string; 核验结论: string }[]
  exempted_fields: { 违规字段: string; 合规条款: string; 豁免编号: string; 豁免适用日期: string }[]
  details: EntryRow[]
}

export async function fieldScan(payload: {
  目标资产: string
  数据分区: string
  观测日期: string
  违规项: { 违规字段: string; 合规条款: string; 检查规则: string }[]
}): Promise<FieldScanResult> {
  const response = await request('/api/compliance_scan/field-scan', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error('字段级扫描提交失败')
  return await response.json()
}

export async function loadRemediationScope(params: {
  observation_date?: string
  assignee?: string
  asset?: string
  partition?: string
}): Promise<RemediationScope> {
  const query = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, value]) => value)),
  ).toString()
  const response = await request(`/api/exemption-review?${query}`)
  if (!response.ok) throw new Error('整改范围读取失败')
  return await response.json()
}

export async function traceIssueExemption(issueId: number): Promise<Record<string, unknown>> {
  const response = await request(`/api/issue_track/${issueId}/exemption-trace`)
  if (!response.ok) throw new Error('豁免追溯读取失败')
  return await response.json()
}
