<script setup lang="ts">
import { Breadcrumbs, call } from 'frappe-ui'
import { RefreshCcw } from 'lucide-vue-next'
import { computed, provide, ref } from 'vue'
import { useRouter } from 'vue-router'
import { downloadImage, waitUntil, wheneverChanges } from '../helpers'
import useDashboard from './dashboard'
import { __ } from '../translation'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import DashboardItem from './DashboardItem.vue'
import VueGridLayout from './VueGridLayout.vue'
import DetailedProfitAndLoss from './DetailedProfitAndLoss.vue'
import { useStorage } from '@vueuse/core'

const props = defineProps<{ name: string }>()

const dashboard_name = await call('insights.api.shared.get_dashboard_name', {
	dashboard_name: props.name,
})

const dashboard = useDashboard(dashboard_name)
provide('dashboard', dashboard)
dashboard.refresh()

const router = useRouter()
function openWorkbook() {
	router.push(`/workbook/${dashboard.doc.workbook}`)
}
await waitUntil(() => dashboard.isloaded)

document.title = `${__(dashboard.doc.title)} | Insights`

const canOpenWorkbook = ref(dashboard.doc.has_workbook_access)

const dashboardContainer = ref<HTMLElement | null>(null)
async function downloadDashboardImage() {
	if (!dashboardContainer.value) return
	await downloadImage(dashboardContainer.value, `${dashboard.doc.title}.png`)
}

const verticalCompact = useStorage('dashboard_vertical_compact', true)
const standaloneFinancialReport = computed(() =>
	dashboard.doc.items.length === 1 && dashboard.doc.items[0].type === 'financial_report'
)
const logoFailed = ref(false)
wheneverChanges(() => dashboard.doc.brand_logo, () => { logoFailed.value = false })
</script>

<template>
	<header class="flex h-12 items-center justify-between border-b py-2.5 pl-5 pr-2">
		<Breadcrumbs
			:items="[
				{ label: __('Dashboards'), route: '/dashboards' },
				{ label: __(dashboard.doc.title), route: `/dashboards/${dashboard.doc.name}` },
			]"
		/>
		<div class="flex items-center gap-2">
			<LanguageSwitcher />
			<Button
				variant="outline"
				@click="() => dashboard.refresh(true)"
				:label="dashboard.synchronizing ? __('Synchronizing sources...') : __('Refresh')"
				:loading="dashboard.refreshing"
				:disabled="dashboard.refreshing"
			>
				<template #prefix>
					<RefreshCcw class="h-4 w-4 text-gray-700" stroke-width="1.5" />
				</template>
			</Button>
			<Dropdown
				placement="left"
				:button="{ icon: 'more-vertical', variant: 'outline' }"
				:options="[
					{
						label: __('Export as PNG'),
						variant: 'outline',
						icon: 'download',
						onClick: downloadDashboardImage,
					},
					canOpenWorkbook
						? {
								label: __('Open Workbook'),
								variant: 'outline',
								icon: 'external-link',
								onClick: openWorkbook,
						  }
						: null,
				]"
			/>
		</div>
	</header>

	<div class="relative flex h-full w-full overflow-hidden">
		<div ref="dashboardContainer" class="flex-1 overflow-y-auto p-4">
			<div v-if="dashboard.doc.brand_logo || dashboard.doc.brand_name" class="mb-4 flex items-center gap-4 rounded border border-gray-200 bg-white p-3">
				<img v-if="dashboard.doc.brand_logo && !logoFailed" :src="dashboard.doc.brand_logo" :alt="dashboard.doc.brand_name || __('Holding company logo')" class="h-20 w-20 shrink-0 rounded object-contain" @error="logoFailed = true" />
				<div class="min-w-0">
					<h1 class="text-lg font-semibold">{{ dashboard.doc.brand_name }}</h1>
					<p class="text-sm text-gray-600">{{ __(dashboard.doc.title) }}</p>
					<p v-if="logoFailed" role="alert" class="text-sm text-red-700">{{ __('Unable to load the company logo. Check the dashboard Brand Logo attachment.') }}</p>
				</div>
			</div>
			<DetailedProfitAndLoss v-if="standaloneFinancialReport" />
			<VueGridLayout
				dir="ltr"
				v-else-if="dashboard.doc.items.length > 0"
				class="h-fit w-full"
				:cols="20"
				:disabled="true"
				:verticalCompact="verticalCompact"
				:modelValue="dashboard.doc.items.map((item) => item.layout)"
			>
				<template #item="{ index }">
					<DashboardItem :index="index" :item="dashboard.doc.items[index]" />
				</template>
			</VueGridLayout>
		</div>
	</div>
</template>
