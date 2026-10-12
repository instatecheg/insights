<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import BaseChart from '../charts/components/BaseChart.vue'
import { __, locale, periodLabel } from '../translation'

type Values = { income: number[]; expense: number[]; profit: number[] }
type Company = { source: string; source_label: string; company: string; company_label: string; income: number; expense: number; profit: number; margin: number | null; chart: Values }
const props = defineProps<{
	companies: Company[]
	periods: { key: string; label: string; start: string; end: string }[]
	chart: Values
	accumulated: boolean
}>()
const companyKey = (company: Company) => JSON.stringify([company.source, company.company])
const selected = ref('')
watch(() => props.companies, companies => {
	if (selected.value && !companies.some(company => companyKey(company) === selected.value)) {
		selected.value = ''
	}
}, { immediate: true })
const active = computed(() => {
	if (selected.value) return props.companies.find(company => companyKey(company) === selected.value)
	return {
		company_label: __('All companies (main filters)'),
		income: props.companies.reduce((total, company) => total + company.income, 0),
		expense: props.companies.reduce((total, company) => total + company.expense, 0),
		profit: props.companies.reduce((total, company) => total + company.profit, 0),
		chart: props.chart,
	}
})
const formatter = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const money = (value: number) => `${__('SAR')} ${formatter.format(value)}`
const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })
const keys = ['income', 'expense', 'profit'] as const
const labels = { income: 'Income', expense: 'Expense', profit: 'Net Profit/Loss' }
const colors = { income: '#f77eaf', expense: '#318bdc', profit: '#42b97b' }
const periodLabels = computed(() => props.periods.map(periodLabel))
function options(categories: string[], series: { name: string; type: 'bar' | 'line'; data: number[]; color?: string }[]) {
	return {
		animation: false,
		aria: { enabled: true },
		tooltip: { trigger: 'axis', valueFormatter: (value: number) => money(value), confine: true },
		legend: { bottom: 0, type: 'scroll' },
		grid: { top: 25, left: 15, right: 25, bottom: 65, containLabel: true },
		xAxis: { type: 'category', data: categories, axisLabel: { hideOverlap: true }, axisTick: { alignWithLabel: true } },
		yAxis: { type: 'value', name: __('SAR'), axisLabel: { formatter: (value: number) => compact.format(value) }, splitLine: { lineStyle: { color: '#e5e7eb' } } },
		series: series.map(item => ({ ...item, itemStyle: item.color ? { color: item.color } : undefined, barMaxWidth: 50, symbolSize: 7 })),
	}
}
const companyOptions = computed(() => {
	if (selected.value) return options(periodLabels.value, keys.map(key => ({
		name: __(labels[key]), type: props.accumulated ? 'line' : 'bar',
		data: active.value?.chart[key] || [], color: colors[key],
	})))
	const chart = options(props.companies.map(company => company.company_label), keys.map(key => ({
		name: __(labels[key]), type: 'bar', data: props.companies.map(company => company[key]), color: colors[key],
	})))
	return {
		...chart,
		xAxis: { ...chart.xAxis, axisLabel: { interval: 0, width: 180, overflow: 'truncate', rotate: 15 } },
		grid: { ...chart.grid, bottom: 95 },
	}
})
const revenueOptions = computed(() => options(periodLabels.value, keys.slice(0, 2).map(key => ({
	name: __(key === 'income' ? 'Revenue' : 'Expenses'), type: props.accumulated ? 'line' : 'bar',
	data: props.chart[key], color: key === 'income' ? colors.expense : colors.income,
}))))
const trendOptions = computed(() => options(periodLabels.value, props.companies.map(company => ({
	name: company.company_label, type: 'line', data: company.chart.profit,
}))))
const comparisonOptions = computed(() => {
	const chart = options(props.companies.map(company => company.company_label), [{
		name: __('Net Profit/Loss'), type: 'bar', data: props.companies.map(company => company.profit),
	}])
	return {
		...chart,
		xAxis: { ...chart.xAxis, axisLabel: { interval: 0, width: 140, overflow: 'truncate', rotate: 15 } },
		grid: { ...chart.grid, bottom: 95 },
	}
})
</script>

<template>
	<div class="financial-charts my-4 space-y-4">
		<div class="rounded border p-4" role="group" :aria-label="__('Company Profit and Loss')">
			<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
				<h3 class="font-semibold">{{ __('Company Profit and Loss') }}</h3>
				<label class="text-sm">{{ __('Chart company') }}
					<select v-model="selected" aria-label="P&L Chart Company" class="ml-2 max-w-full rounded border p-2">
						<option value="">{{ __('All companies (main filters)') }}</option>
						<option v-for="company in companies" :key="companyKey(company)" :value="companyKey(company)">{{ company.company_label }}</option>
					</select>
				</label>
			</div>
			<template v-if="active">
				<h4 class="mb-3 text-sm font-medium">{{ active.company_label }}</h4>
				<div class="mb-4 grid grid-cols-1 gap-3 text-center sm:grid-cols-3">
					<div v-for="key in keys" :key="key">
						<p class="text-sm text-gray-600">{{ __(labels[key]) }}</p>
						<strong class="text-lg" :class="active[key] < 0 ? 'text-red-600' : ''"><bdi dir="ltr">{{ money(active[key]) }}</bdi></strong>
					</div>
				</div>
				<p class="mb-2 text-xs text-gray-600">{{ selected ? __('Period breakdown for the selected company') : __('Income, Expense and Net Profit/Loss for each company in the main filters. Totals cover the applied period.') }}</p>
				<div class="h-80" role="img" :aria-label="selected ? `${active.company_label} ${__('Income Expense Net Profit by period')}` : __('Income Expense Net Profit by company')">
					<BaseChart :key="JSON.stringify(companyOptions)" :options="companyOptions" />
				</div>
			</template>
		</div>
		<p class="text-xs text-gray-600">{{ __('All charts and comparisons follow the applied P&L filters and periodicity. Values are SAR, including in Growth/Margin table view. Accumulated charts show running balances; totals use the final period. Negative profit appears below zero.') }}</p>
		<div class="rounded border p-4">
			<h3 class="mb-3 font-semibold">{{ __('Company profitability comparison') }}</h3>
			<div class="overflow-x-auto">
				<table class="w-full text-sm" aria-label="P&L company comparison">
					<thead><tr class="border-b text-left"><th class="p-2">{{ __('Source') }}</th><th class="p-2">{{ __('Company') }}</th><th class="p-2 text-right">{{ __('Revenue') }}</th><th class="p-2 text-right">{{ __('Expenses') }}</th><th class="p-2 text-right">{{ __('Net Profit') }}</th><th class="p-2 text-right">{{ __('Profit Margin') }}</th></tr></thead>
					<tbody><tr v-for="company in companies" :key="companyKey(company)" class="border-b">
						<td class="p-2">{{ company.source_label }}</td><td class="p-2">{{ company.company }}</td>
						<td class="whitespace-nowrap p-2 text-right">{{ money(company.income) }}</td><td class="whitespace-nowrap p-2 text-right">{{ money(company.expense) }}</td>
						<td class="whitespace-nowrap p-2 text-right" :class="company.profit < 0 ? 'text-red-600' : ''">{{ money(company.profit) }}</td>
						<td class="whitespace-nowrap p-2 text-right" :class="company.margin !== null && company.margin < 0 ? 'text-red-600' : ''">{{ company.margin === null ? __('N/A') : `${formatter.format(company.margin)}%` }}</td>
					</tr></tbody>
				</table>
			</div>
		</div>
		<div class="grid grid-cols-1 gap-4 xl:grid-cols-2">
			<div class="rounded border p-4" role="group" :aria-label="__('Revenue vs Expenses - Selected Companies')">
				<h3 class="mb-3 font-semibold">{{ __('Revenue vs Expenses - Selected Companies') }}</h3>
				<div class="h-80"><BaseChart :key="JSON.stringify(revenueOptions)" :options="revenueOptions" /></div>
			</div>
			<div class="rounded border p-4" role="group" :aria-label="__('Net Profit by Company - Selected Periods')">
				<h3 class="mb-3 font-semibold">{{ __('Net Profit by Company - Selected Periods') }}</h3>
				<div class="h-80"><BaseChart :key="JSON.stringify(trendOptions)" :options="trendOptions" /></div>
			</div>
		</div>
		<div class="rounded border p-4" role="group" :aria-label="__('Net Profit Comparison - Selected Companies')">
			<h3 class="mb-3 font-semibold">{{ __('Net Profit Comparison - Selected Companies') }}</h3>
			<div class="h-80"><BaseChart :key="JSON.stringify(comparisonOptions)" :options="comparisonOptions" /></div>
		</div>
	</div>
</template>
