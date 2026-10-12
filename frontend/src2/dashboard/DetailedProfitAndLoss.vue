<script setup lang="ts">
import { call } from 'frappe-ui'
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { Dashboard } from './dashboard'
import { __, locale, periodLabel } from '../translation'
import FinancialMultiSelect from './FinancialMultiSelect.vue'
import FinancialCharts from './FinancialCharts.vue'

type Option = { value: string; label: string; start?: string; end?: string }
type Catalog = { companies: Option[]; fiscal_years: Option[]; finance_books: Option[]; cost_centers: Option[]; projects: Option[]; dimensions: {value: string; label: string; options: Option[]}[] }
type Period = { key: string; label: string; start: string; end: string }
type Row = { account: string; parent_account: string; account_name: string; company_label: string; source: string; company: string; account_type: string; indent: number; is_group: boolean; total: number; [key: string]: string | number | boolean | null }
type CompanySummary = { company_label: string; source: string; source_label: string; company: string; income: number; expense: number; profit: number; margin: number | null; chart: { income: number[]; expense: number[]; profit: number[] } }
type Report = { periods: Period[]; rows: Row[]; companies: CompanySummary[]; has_data: boolean; summary: Record<string, number>; chart: { income: number[]; expense: number[]; profit: number[] }; currency: string; updated_at: string }
type Entry = { name: string; posting_date: string; account: string; voucher_type: string; voucher_no: string; debit: number; credit: number; cost_center: string; project: string; finance_book: string }

const dashboard = inject<Dashboard>('dashboard')!
const catalog = ref<Catalog>()
const report = ref<Report>()
const expandedAccounts = ref(new Set<string>())
const expandedCompanies = ref(new Set<string>())
const companyKey = (company: { source: string; company: string }) => JSON.stringify([company.source, company.company])
const rowKey = (row: Row, account = row.account) => JSON.stringify([row.source, row.company, account])
const visibleRows = computed(() => {
	const rows = report.value?.rows || []
	const lookup = new Map(rows.map(row => [rowKey(row), row]))
	return rows.filter(row => {
		let parent = lookup.get(rowKey(row, row.parent_account))
		while (parent) {
			if (!expandedAccounts.value.has(rowKey(parent))) return false
			parent = lookup.get(rowKey(parent, parent.parent_account))
		}
		return true
	})
})
function toggleAccount(row: Row) {
	const key = rowKey(row)
	if (expandedAccounts.value.has(key)) expandedAccounts.value.delete(key)
	else expandedAccounts.value.add(key)
}
function toggleCompany(company: CompanySummary) {
	const key = companyKey(company)
	if (expandedCompanies.value.has(key)) expandedCompanies.value.delete(key)
	else expandedCompanies.value.add(key)
}
function companyRows(company: CompanySummary) {
	return visibleRows.value.filter(row => companyKey(row) === companyKey(company))
}
const loading = ref(false)
const error = ref('')
const mode = ref('Current YTD')
const companies = ref<string[]>([])
const fromYear = ref('')
const toYear = ref('')
const start = ref('')
const end = ref('')
const periodicity = ref('Quarterly')
const view = ref('Report')
const accumulated = ref(false)
const defaultBook = ref(true)
const showZeros = ref(false)
const financeBooks = ref<string[]>([])
const costCenters = ref<string[]>([])
const projects = ref<string[]>([])
const dimensions = ref<Record<string, string[]>>({})
const applied = ref<ReturnType<typeof requestFilters>>()
const appliedSelection = ref<{ mode: string; fromYear: string; toYear: string }>()
const languageStateKey = `insights:pl-language-state:${dashboard.doc.name}`
useEventListener(window, 'insights:language-change', () => {
	if (applied.value) sessionStorage.setItem(languageStateKey, JSON.stringify({
		filters: applied.value, selection: appliedSelection.value,
	}))
})
const appliedView = computed(() => String(applied.value?.view || 'Report'))
const entries = ref<Entry[]>()
const entriesTitle = ref('')
const entryError = ref('')
const entriesLoading = ref(false)
const truncated = ref(false)
const formatter = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const money = (value: number) => `${__('SAR')} ${formatter.format(value)}`
function currentDates() {
	const today = new Date()
	const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
	return [`${today.getFullYear()}-01-01`, localDate]
}
function requestFilters() {
	let [from, to] = mode.value === 'Current YTD' ? currentDates() : [start.value, end.value]
	if (mode.value === 'Fiscal Year') {
		from = catalog.value?.fiscal_years.find(option => option.value === fromYear.value)?.start || ''
		to = catalog.value?.fiscal_years.find(option => option.value === toYear.value)?.end || ''
	}
	if (!from || !to || from > to) throw new Error(__('Choose a valid start and end fiscal year/date.'))
	return {
		start: from, end: to,
		company: companies.value.length ? { operator: 'in', value: companies.value } : undefined,
		periodicity: periodicity.value, view: view.value,
		accumulated_values: accumulated.value,
		include_default_book_entries: defaultBook.value,
		show_zero_values: showZeros.value,
		finance_books: financeBooks.value, cost_centers: costCenters.value, projects: projects.value,
		dimensions: dimensions.value,
	}
}
let requestVersion = 0
let entryRequestVersion = 0
async function run() {
	if (!catalog.value) return
	const version = ++requestVersion
	loading.value = true
	error.value = ''
	entries.value = undefined
	entryRequestVersion++
	try {
		const filters = requestFilters()
		const selection = { mode: mode.value, fromYear: fromYear.value, toYear: toYear.value }
		start.value = filters.start
		end.value = filters.end
		const result: Report = await call('insights.ceo_finance.detailed_profit_and_loss.run', {
			dashboard_name: dashboard.doc.name, filters,
		})
		if (version !== requestVersion) return
		report.value = result
		expandedAccounts.value = new Set()
		expandedCompanies.value = new Set()
		applied.value = JSON.parse(JSON.stringify(filters))
		appliedSelection.value = selection
	} catch (failure) {
		if (version !== requestVersion) return
		report.value = undefined
		error.value = failure instanceof Error ? failure.message : String(failure)
	} finally {
		if (version === requestVersion) loading.value = false
	}
}
async function apply() {
	await run()
}
async function loadOptions() {
	loading.value = true
	error.value = ''
	try {
		const options: Catalog = await call('insights.ceo_finance.detailed_profit_and_loss.get_options', { dashboard_name: dashboard.doc.name })
		catalog.value = options
		for (const dimension of options.dimensions) {
			if (!dimensions.value[dimension.value]) dimensions.value[dimension.value] = []
		}
		const saved = sessionStorage.getItem(languageStateKey)
		if (saved) {
			sessionStorage.removeItem(languageStateKey)
			const state: { filters: ReturnType<typeof requestFilters>; selection: { mode: string; fromYear: string; toYear: string } } = JSON.parse(saved)
			const filters = state.filters
			mode.value = state.selection.mode
			fromYear.value = state.selection.fromYear
			toYear.value = state.selection.toYear
			start.value = filters.start
			end.value = filters.end
			companies.value = filters.company?.value || []
			periodicity.value = filters.periodicity
			view.value = filters.view
			accumulated.value = filters.accumulated_values
			defaultBook.value = filters.include_default_book_entries
			showZeros.value = filters.show_zero_values
			financeBooks.value = filters.finance_books
			costCenters.value = filters.cost_centers
			projects.value = filters.projects
			dimensions.value = filters.dimensions
		}
		if (!start.value || !end.value) [start.value, end.value] = currentDates()
		await run()
	} catch (failure) {
		error.value = failure instanceof Error ? failure.message : String(failure)
		loading.value = false
	}
}
watch(() => dashboard.analysisRefreshVersion, run)
onMounted(loadOptions)
async function resetFilters() {
	companies.value = []
	financeBooks.value = []
	costCenters.value = []
	projects.value = []
	for (const key of Object.keys(dimensions.value)) dimensions.value[key] = []
	mode.value = 'Current YTD'
	periodicity.value = 'Quarterly'
	view.value = 'Report'
	accumulated.value = false
	defaultBook.value = true
	showZeros.value = false
	await run()
}
function closeEntries() {
	entryRequestVersion++
	entries.value = undefined
	entriesLoading.value = false
}
async function drill(row: Row, period: Period) {
	if (!applied.value) return
	entriesLoading.value = true
	entries.value = []
	entryError.value = ''
	entriesTitle.value = `${row.company_label} | ${row.account_name} | ${periodLabel(period)}`
	const version = requestVersion
	const entryVersion = ++entryRequestVersion
	try {
		const result: { rows: Entry[]; truncated: boolean } = await call('insights.ceo_finance.detailed_profit_and_loss.get_entries', {
			dashboard_name: dashboard.doc.name, filters: applied.value,
			source_name: row.source, company_name: row.company,
			account: row.account, period_key: period.key,
		})
		if (version !== requestVersion || entryVersion !== entryRequestVersion) return
		entries.value = result.rows
		truncated.value = result.truncated
	} catch (failure) {
		if (version === requestVersion && entryVersion === entryRequestVersion) entryError.value = failure instanceof Error ? failure.message : String(failure)
	} finally {
		if (entryVersion === entryRequestVersion) entriesLoading.value = false
	}
}
function display(row: Row, key: string, index: number) {
	const value = row[key]
	if (typeof value !== 'number') return __('N/A')
	return appliedView.value === 'Margin' || (appliedView.value === 'Growth' && index > 0 && key !== 'total')
		? `${formatter.format(value)}%`
		: money(value)
}
const seriesLabels = { income: 'Income', expense: 'Expense', profit: 'Net Profit' }
const series = ['income', 'expense', 'profit'] as const
</script>

<template>
	<section class="financial-report w-full rounded border border-gray-200 bg-white p-4">
		<h2 class="text-lg font-semibold">{{ __('Detailed Profit and Loss') }}</h2>
		<p class="mb-3 text-sm text-gray-600">
			{{ __('Live Profit and Loss across registered sources. Select companies and reporting dates here, then Apply. Filters are independent of the CEO dashboard. SAR; no intercompany eliminations.') }}
		</p>
		<p class="mb-3 text-sm">{{ __('Applied companies:') }} {{ applied?.company?.value.join(', ') || __('All companies') }}</p>
		<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
			<FinancialMultiSelect v-model="companies" label="Company" :options="catalog?.companies || []" :empty-label="__('All companies')" />
			<label class="text-sm">{{ __('Filter Based On') }}
				<select v-model="mode" aria-label="P&L Filter Based On" class="block w-full rounded border p-1">
					<option value="Current YTD">{{ __('Current YTD') }}</option><option value="Fiscal Year">{{ __('Fiscal Year') }}</option><option value="Date Range">{{ __('Date Range') }}</option>
				</select>
			</label>
			<template v-if="mode === 'Fiscal Year'">
				<label class="text-sm">{{ __('Start Year') }}
					<select v-model="fromYear" aria-label="P&L Start Year" class="block w-full rounded border p-1">
						<option value="">{{ __('Select fiscal year') }}</option>
						<option v-for="option in catalog?.fiscal_years" :key="option.value" :value="option.value">{{ option.label }}</option>
					</select>
				</label>
				<label class="text-sm">{{ __('End Year') }}
					<select v-model="toYear" aria-label="P&L End Year" class="block w-full rounded border p-1">
						<option value="">{{ __('Select fiscal year') }}</option>
						<option v-for="option in catalog?.fiscal_years" :key="option.value" :value="option.value">{{ option.label }}</option>
					</select>
				</label>
			</template>

			<style scoped>
			select {
				background-repeat: no-repeat;
				background-position: right 0.5rem center;
				background-size: 1rem;
				padding-right: 2rem;
			}
			</style>
			<template v-if="mode === 'Date Range'">
				<label class="text-sm">{{ __('Start Date') }}<input v-model="start" aria-label="P&L Start Date" type="date" class="block w-full rounded border p-1" /></label>
				<label class="text-sm">{{ __('End Date') }}<input v-model="end" aria-label="P&L End Date" type="date" class="block w-full rounded border p-1" /></label>
			</template>
			<label class="text-sm">{{ __('Periodicity') }}
				<select v-model="periodicity" aria-label="P&L Periodicity" class="block w-full rounded border p-1">
					<option v-for="option in ['Monthly', 'Quarterly', 'Half-Yearly', 'Yearly']" :key="option" :value="option">{{ __(option) }}</option>
				</select>
			</label>
			<label class="text-sm">{{ __('Select View') }}
				<select v-model="view" aria-label="P&L Select View" class="block w-full rounded border p-1">
					<option v-for="option in ['Report', 'Growth', 'Margin']" :key="option" :value="option">{{ __(option) }}</option>
				</select>
			</label>
			<label class="text-sm">{{ __('Currency') }}<input :value="__('SAR')" readonly aria-label="P&L Currency" class="block w-full rounded border p-1" /></label>
			<FinancialMultiSelect v-model="financeBooks" label="Finance Book" :options="catalog?.finance_books || []" :empty-label="__('Company default / unassigned')" />
			<FinancialMultiSelect v-model="costCenters" label="Cost Center" :options="catalog?.cost_centers || []" />
			<FinancialMultiSelect v-model="projects" label="Project" :options="catalog?.projects || []" />
			<FinancialMultiSelect v-for="dimension in catalog?.dimensions" :key="dimension.value" v-model="dimensions[dimension.value]" :label="dimension.label" :options="dimension.options" />
		</div>
		<div class="my-3 flex flex-wrap items-center gap-4 text-sm">
			<label><input v-model="accumulated" type="checkbox" /> {{ __('Accumulated Values') }}</label>
			<label><input v-model="defaultBook" type="checkbox" /> {{ __('Include Default FB Entries') }}</label>
			<label><input v-model="showZeros" type="checkbox" /> {{ __('Show zero values') }}</label>
			<Button :loading="loading" :disabled="loading" variant="solid" @click="apply">{{ __('Apply P&L Filters') }}</Button>
			<Button :disabled="loading" @click="loadOptions">{{ __('Reload P&L Options') }}</Button>
			<Button :disabled="loading" @click="resetFilters">{{ __('Reset All Filters') }}</Button>
			<Button :disabled="loading" @click="() => { financeBooks = []; costCenters = []; projects = []; Object.keys(dimensions).forEach(key => dimensions[key] = []); apply() }">{{ __('Clear Advanced Filters') }}</Button>
		</div>
		<p class="text-xs text-gray-600">{{ __('Source-qualified filters prevent mixing same-named projects/books. Cost Center includes descendants. Empty Finance Book uses each company default book plus unassigned entries when Include Default FB Entries is checked. Custom report templates are excluded.') }}</p>
		<p v-if="error" role="alert" class="my-3 text-sm text-red-700">{{ error }}</p>
		<p v-if="loading" class="my-3 text-sm">{{ __('Loading live P&L data...') }}</p>
		<template v-else-if="report">
			<p class="my-2 text-xs text-gray-600">{{ __('Applied period:') }} <bdi>{{ applied?.start }}</bdi> {{ __('to') }} <bdi>{{ applied?.end }}</bdi> | {{ __(String(applied?.periodicity || '')) }} | {{ __(appliedView) }}. {{ __('Live data updated:') }} <bdi>{{ report.updated_at }}</bdi>. {{ __('Accumulated summaries use the last column; running values reset on each source fiscal year.') }}</p>
			<div class="my-3 grid grid-cols-3 gap-3">
				<div v-for="(value, label) in report.summary" :key="label" class="rounded border p-3">
					<div class="text-sm">{{ __(String(label)) }}</div><strong><bdi dir="ltr">{{ report.has_data ? money(value) : __('No data') }}</bdi></strong>
				</div>
			</div>
			<p v-if="!report.has_data" role="status" class="my-3 rounded bg-gray-50 p-3">{{ __('No posted Income/Expense entries match these filters. Reset All Filters or choose another company/period.') }}</p>
			<FinancialCharts v-if="report.has_data" :companies="report.companies" :periods="report.periods" :chart="report.chart" :accumulated="Boolean(applied?.accumulated_values)" />
			<p class="mb-2 text-xs text-gray-600">{{ __('Click an account amount for matching GL entries and voucher references. Parent rows include children; do not sum parent and child rows together. Growth shows ERPNext period-over-period growth; the first column stays an amount. Margin is relative to each company income.') }}</p>
			<div class="mb-3 flex flex-wrap items-center gap-3">
				<h3 class="font-semibold">{{ __('P&L accounts') }}</h3>
				<span class="text-xs text-gray-600">{{ __('Click a company name to show its accounts, then click a parent account name to show its children. Click again to collapse.') }}</span>
			</div>
			<div class="overflow-x-auto">
				<table class="w-full text-sm" aria-label="Detailed Profit and Loss accounts">
					<thead class="sticky top-0 bg-gray-50"><tr><th class="p-2 text-left">{{ __('Company / Account') }}</th><th v-for="period in report.periods" :key="period.key" class="p-2 text-right">{{ periodLabel(period) }}</th><th class="p-2 text-right">{{ __('Total') }}</th></tr></thead>
					<tbody>
						<template v-for="company in report.companies" :key="companyKey(company)">
							<tr class="border-b bg-gray-50">
								<td :colspan="report.periods.length + 2" class="p-2">
									<button class="w-full rounded p-1 text-left font-semibold text-blue-700 hover:bg-gray-100" :aria-expanded="expandedCompanies.has(companyKey(company))" :aria-label="company.company_label" @click="toggleCompany(company)"><span aria-hidden="true" class="mr-2">{{ expandedCompanies.has(companyKey(company)) ? '-' : '+' }}</span>{{ company.company_label }}</button>
								</td>
							</tr>
							<template v-if="expandedCompanies.has(companyKey(company))">
						<tr v-for="row in companyRows(company)" :key="rowKey(row)" class="border-b" :class="row.is_group ? 'font-semibold' : ''">
							<td class="p-2" :style="{paddingInlineStart: `${24 + row.indent * 16}px`}"><button v-if="row.is_group" class="w-full rounded p-1 text-left text-blue-700 hover:bg-gray-50" :aria-expanded="expandedAccounts.has(rowKey(row))" :aria-label="`${row.company_label} | ${row.account_name}`" @click="toggleAccount(row)"><span aria-hidden="true" class="mr-2">{{ expandedAccounts.has(rowKey(row)) ? '-' : '+' }}</span>{{ row.account_name }}</button><span v-else>{{ row.account_name }}</span></td>
							<td v-for="(period, index) in report.periods" :key="period.key" class="whitespace-nowrap p-2 text-right"><button class="text-blue-700 hover:underline" @click="drill(row, period)">{{ display(row, period.key, index) }}</button></td>
							<td class="whitespace-nowrap p-2 text-right">{{ display(row, 'total', 0) }}</td>
						</tr>
						<tr v-if="!companyRows(company).length"><td :colspan="report.periods.length + 2" class="p-4">{{ __('No data for these filters') }}</td></tr>
							</template>
						</template>
					</tbody>
					<tfoot><tr v-for="key in series" :key="key" class="border-t font-semibold"><td class="p-2">{{ __(seriesLabels[key]) }}</td><td v-for="(period, index) in report.periods" :key="period.key" class="whitespace-nowrap p-2 text-right">{{ money(report.chart[key][index]) }}</td><td class="p-2 text-right">{{ money(key === 'profit' ? report.summary['Net Profit'] : report.summary[key === 'income' ? 'Income' : 'Expense']) }}</td></tr></tfoot>
				</table>
			</div>
		</template>
		<div v-if="entries" class="mt-4 rounded border p-3">
			<div class="flex items-center justify-between gap-2"><h3 class="font-semibold">{{ entriesTitle }}</h3><Button @click="closeEntries">{{ __('Close GL detail') }}</Button></div>
			<p v-if="entriesLoading">{{ __('Loading entries...') }}</p>
			<p v-if="entryError" role="alert" class="text-red-700">{{ entryError }}</p>
			<p v-if="truncated" class="text-amber-700">{{ __('Showing the first 1,000 entries. Narrow the period to see the rest.') }}</p>
			<div class="max-h-80 overflow-auto"><table class="w-full text-sm" aria-label="Detailed P&L GL entries">
				<thead><tr><th>{{ __('Date') }}</th><th>{{ __('Account') }}</th><th>{{ __('Voucher') }}</th><th>{{ __('Debit') }}</th><th>{{ __('Credit') }}</th><th>{{ __('Cost Center / Project / Book') }}</th><th>{{ __('GL Entry') }}</th></tr></thead>
				<tbody><tr v-for="entry in entries" :key="entry.name" class="border-b"><td class="p-2"><bdi>{{ entry.posting_date }}</bdi></td><td class="p-2">{{ entry.account }}</td><td class="p-2">{{ __(entry.voucher_type) }} <bdi>{{ entry.voucher_no }}</bdi></td><td class="p-2"><bdi dir="ltr">{{ money(Number(entry.debit)) }}</bdi></td><td class="p-2"><bdi dir="ltr">{{ money(Number(entry.credit)) }}</bdi></td><td class="p-2">{{ entry.cost_center }} / {{ entry.project }} / {{ entry.finance_book }}</td><td class="p-2"><bdi>{{ entry.name }}</bdi></td></tr></tbody>
			</table></div>
		</div>
	</section>
</template>
