<script setup lang="ts">
import { computed, inject, onMounted, ref, watch } from 'vue'
import { showErrorToast } from '../helpers'
import { WorkbookDashboardFilter } from '../types/workbook.types'
import { Dashboard } from './dashboard'
import { __, locale } from '../translation'

const props = defineProps<{ item: WorkbookDashboardFilter }>()
const dashboard = inject<Dashboard>('dashboard')!
const currentYear = new Date().getFullYear()
const years = ref<string[]>([String(currentYear)])
const year = ref(String(currentYear))
const month = ref('all')
const mode = ref('calendar')
const from = ref('')
const to = ref('')
watch(
	() => dashboard.filterStates[props.item.filter_name],
	(state) => {
		if (state?.operator === 'between' && Array.isArray(state.value)) {
			from.value = String(state.value[0])
			to.value = String(state.value[1])
			const selectedYear = from.value.slice(0, 4)
			const selectedMonth = Number(from.value.slice(5, 7))
			const lastDay = new Date(Number(selectedYear), selectedMonth, 0).getDate()
			if (from.value === `${selectedYear}-01-01` && to.value === `${selectedYear}-12-31`) {
				mode.value = 'calendar'
				year.value = selectedYear
				month.value = 'all'
			} else if (
				from.value.endsWith('-01') &&
				to.value === `${selectedYear}-${String(selectedMonth).padStart(2, '0')}-${lastDay}`
			) {
				mode.value = 'calendar'
				year.value = selectedYear
				month.value = String(selectedMonth)
			} else {
				mode.value = 'custom'
			}
		} else if (state?.operator === 'within' && state.value === 'Current Year') {
			mode.value = 'calendar'
			year.value = String(currentYear)
			month.value = 'all'
		}
	},
	{ immediate: true, deep: true }
)
const error = ref('')
const monthOptions = computed(() => [
	{ label: __('All months'), value: 'all' },
	...Array.from({ length: 12 }, (_, index) => ({
		label: new Date(2000, index, 1).toLocaleString(locale, { month: 'long' }),
		value: String(index + 1),
	})),
])
onMounted(async () => {
	try {
		const link = dashboard.getColumnFromFilterLink(Object.values(props.item.links)[0])
		if (!link) throw new Error('Reporting period has no linked financial query')
		const values = await dashboard.getDistinctColumnValues(props.item.values_query || link.query, 'report_year')
		years.value = [...new Set([String(currentYear), ...values.map(String)])].sort().reverse()
	} catch (error) {
		showErrorToast(error)
	}
})
function apply() {
	error.value = ''
	let start = from.value
	let end = to.value
	if (mode.value === 'calendar') {
		const selectedYear = Number(year.value)
		const selectedMonth = month.value === 'all' ? 0 : Number(month.value)
		start = `${selectedYear}-${String(selectedMonth || 1).padStart(2, '0')}-01`
		const lastMonth = selectedMonth || 12
		const lastDay = new Date(selectedYear, lastMonth, 0).getDate()
		end = `${selectedYear}-${String(lastMonth).padStart(2, '0')}-${lastDay}`
	}
	if (!start || !end || start > end) {
		error.value = __('Choose a valid start and end date')
		return
	}
	dashboard.updateFilterState(props.item.filter_name, 'between', [start, end])
}
function reset() {
	mode.value = 'calendar'
	year.value = String(currentYear)
	month.value = 'all'
	dashboard.updateFilterState(props.item.filter_name, 'within', 'Current Year')
}
</script>

<template>
	<div class="w-full space-y-1">
		<div class="text-xs text-gray-600">{{ __('Reporting Period') }}</div>
		<div class="flex flex-wrap items-center gap-2">
			<select v-model="mode" class="rounded border border-gray-300 px-2 py-1 text-sm" :aria-label="__('Period mode')">
				<option value="calendar">{{ __('Year / month') }}</option>
				<option value="custom">{{ __('Custom period') }}</option>
			</select>
			<template v-if="mode === 'calendar'">
				<select v-model="year" class="rounded border border-gray-300 px-2 py-1 text-sm" :aria-label="__('Reporting year')">
					<option v-for="option in years" :key="option" :value="option">{{ option }}</option>
				</select>
				<select v-model="month" class="rounded border border-gray-300 px-2 py-1 text-sm" :aria-label="__('Reporting month')">
					<option v-for="option in monthOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
				</select>
			</template>
			<template v-else>
				<input type="date" v-model="from" class="rounded border border-gray-300 px-2 py-1 text-sm" :aria-label="__('Period start')" />
				<input type="date" v-model="to" class="rounded border border-gray-300 px-2 py-1 text-sm" :aria-label="__('Period end')" />
			</template>
			<Button variant="solid" @click="apply">{{ __('Apply') }}</Button>
			<Button variant="outline" @click="reset">{{ __('Current YTD') }}</Button>
		</div>
		<p v-if="error" class="text-xs text-red-600">{{ error }}</p>
		<p v-else class="text-xs text-gray-600">
			{{ __('Applied:') }} {{ dashboard.filterStates[item.filter_name]?.value }}
		</p>
	</div>
</template>
