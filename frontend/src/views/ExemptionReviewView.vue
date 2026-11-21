<template>
  <section class="page" data-testid="exemption-review">
    <header class="page-head">
      <div>
        <h2>条款豁免核验</h2>
        <p class="page-desc">
          按条款、分区和字段判定有效期内的例外；补报的豁免按原观测日生效，
          复查时以“仍需整改”清单为准。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="reload-scope" type="button" @click="loadScope">
          刷新整改范围
        </button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="loadScope">
      <label class="filter-item">
        <span>观测日期</span>
        <input v-model="scopeQuery.observation_date" data-testid="scope-observation-date" placeholder="YYYY-MM-DD" />
      </label>
      <label class="filter-item">
        <span>目标资产</span>
        <input v-model="scopeQuery.asset" data-testid="scope-asset" placeholder="资产编号" />
      </label>
      <label class="filter-item">
        <span>数据分区</span>
        <input v-model="scopeQuery.partition" data-testid="scope-partition" placeholder="如 SEA" />
      </label>
      <label class="filter-item">
        <span>经办人</span>
        <input v-model="scopeQuery.assignee" data-testid="scope-assignee" placeholder="指派处理人" />
      </label>
      <button class="btn" data-testid="scope-query" type="submit">查询</button>
    </form>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">仍需整改字段</span>
        <strong class="stat-value" data-testid="need-remediation-count">{{ scope.summary.need_remediation }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">豁免成立字段</span>
        <strong class="stat-value" data-testid="exempted-count">{{ scope.summary.exempted }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">仍适用条款</span>
        <strong class="stat-value" data-testid="active-clauses">{{ scope.summary.clauses_to_remediate.join('、') || '—' }}</strong>
      </article>
    </div>

    <h3>仍需整改的字段</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>目标资产</th><th>数据分区</th><th>违规字段</th><th>合规条款</th>
          <th>检查规则</th><th>观测日期</th><th>核验结论</th><th>关联问题</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in scope.fields_to_remediate" :key="`${row.合规条款}-${row.违规字段}-${row.观测日期}`">
          <td>{{ row.目标资产 }}</td>
          <td>{{ row.数据分区 }}</td>
          <td>{{ row.违规字段 }}</td>
          <td>{{ row.合规条款 }}</td>
          <td>{{ row.检查规则 }}</td>
          <td>{{ row.观测日期 }}</td>
          <td>{{ row.核验结论 }}</td>
          <td>{{ row.关联问题编号 || '—' }}</td>
        </tr>
        <tr v-if="!scope.fields_to_remediate.length">
          <td colspan="8" class="empty-state">该观测日没有仍需整改的字段</td>
        </tr>
      </tbody>
    </table>

    <h3>已豁免字段（保留追溯）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>目标资产</th><th>数据分区</th><th>违规字段</th><th>合规条款</th>
          <th>豁免编号</th><th>豁免适用日期</th><th>观测日期</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in scope.exempted_fields" :key="`${row.豁免编号}-${row.合规条款}-${row.违规字段}`">
          <td>{{ row.目标资产 }}</td>
          <td>{{ row.数据分区 }}</td>
          <td>{{ row.违规字段 }}</td>
          <td>{{ row.合规条款 }}</td>
          <td>{{ row.豁免编号 }}</td>
          <td>{{ row.豁免适用日期 }}</td>
          <td>{{ row.观测日期 }}</td>
        </tr>
        <tr v-if="!scope.exempted_fields.length">
          <td colspan="7" class="empty-state">该观测日没有豁免字段</td>
        </tr>
      </tbody>
    </table>

    <h3>已派出整改单的豁免追溯</h3>
    <table class="data-table">
      <thead>
        <tr><th>问题编号</th><th>合规条款</th><th>违规字段</th><th>当前状态</th><th>豁免编号</th><th>豁免适用日期</th><th>追溯</th></tr>
      </thead>
      <tbody>
        <tr v-for="issue in scope.exempted_issues" :key="String(issue.id)">
          <td>{{ issue.问题编号 }}</td>
          <td>{{ issue.合规条款 }}</td>
          <td>{{ issue.违规字段 }}</td>
          <td>{{ issue.status }}</td>
          <td>{{ issue.豁免编号 }}</td>
          <td>{{ issue.豁免适用日期 }}</td>
          <td>
            <button class="link" type="button" :data-testid="`trace-${issue.id}`" @click="trace(issue)">
              查看凭据
            </button>
          </td>
        </tr>
        <tr v-if="!scope.exempted_issues.length">
          <td colspan="7" class="empty-state">没有后补豁免的已派单条目</td>
        </tr>
      </tbody>
    </table>

    <p v-if="traceMessage" class="page-foot" data-testid="trace-result">{{ traceMessage }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="scope-error">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { loadRemediationScope, traceIssueExemption } from '@/api/exemption-service'
import type { RemediationScope } from '@/api/exemption-service'
import type { EntryRow } from '@/data/types'

const scopeQuery = reactive({ observation_date: '', asset: '', partition: '', assignee: '' })
const emptyScope: RemediationScope = {
  observation_date: '',
  assignee: '',
  asset: '',
  partition: '',
  fields_to_remediate: [],
  exempted_fields: [],
  active_issues: [],
  exempted_issues: [],
  summary: {
    need_remediation: 0,
    exempted: 0,
    active_issues: 0,
    exempted_issues: 0,
    clauses_to_remediate: [],
    clauses_exempted: [],
  },
}
const scope = ref<RemediationScope>(emptyScope)
const errorMessage = ref('')
const traceMessage = ref('')

async function loadScope() {
  errorMessage.value = ''
  try {
    scope.value = await loadRemediationScope({ ...scopeQuery })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '整改范围读取失败'
  }
}

async function trace(issue: EntryRow) {
  errorMessage.value = ''
  traceMessage.value = ''
  try {
    const detail = (await traceIssueExemption(Number(issue.id))) as {
      issue?: EntryRow
      exemption?: EntryRow | null
      exempted_on?: string
      observation_date?: string
    }
    const exemption = detail.exemption
    traceMessage.value = exemption
      ? `${detail.issue?.问题编号} 由豁免${exemption.豁免编号}（${exemption.适用条款}/${exemption.数据分区}/${exemption.违规字段}）覆盖，适用 ${detail.exempted_on}，观测日 ${detail.observation_date}`
      : `${detail.issue?.问题编号} 暂无挂接的豁免凭据`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '豁免追溯读取失败'
  }
}

onMounted(loadScope)
</script>
