<script setup lang="ts">
import { computed, ref } from 'vue'
import { __ } from '../translation'

const props = defineProps<{
	label: string
	options: { value: string; label: string }[]
	emptyLabel?: string
}>()
const model = defineModel<string[]>({ required: true })
const search = ref('')
const open = ref(false)
const visible = computed(() => props.options.filter(option =>
	option.label.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())
))
const description = computed(() => model.value.length
	? model.value.map(value => props.options.find(option => option.value === value)?.label || value).join(', ')
	: props.emptyLabel || __('All / unfiltered')
)
function toggle(value: string) {
	model.value = model.value.includes(value)
		? model.value.filter(selected => selected !== value)
		: [...model.value, value]
}
</script>

<template>
	<div class="relative min-w-0 text-sm" @keydown.esc="open = false">
		<div class="mb-1 text-gray-700">{{ __(label) }}</div>
		<button type="button" :aria-label="`P&L ${label}`" :aria-expanded="open" :title="description"
			class="flex h-9 w-full items-center justify-between gap-2 rounded border border-gray-300 bg-white px-2 text-left"
			@click="open = !open">
			<span class="truncate">{{ description }}</span><span aria-hidden="true">+</span>
		</button>
		<div v-if="open" class="absolute left-0 top-full z-50 mt-1 w-full min-w-64 rounded border bg-white p-2 shadow-lg">
			<div class="mb-2 flex items-center gap-2">
				<input v-model="search" :aria-label="`Search ${label}`" :placeholder="__('Search')" class="min-w-0 flex-1 rounded border px-2 py-1" />
				<button type="button" class="text-blue-700" @click="model = []">{{ __('Clear selections') }}</button>
				<button type="button" class="text-blue-700" @click="open = false">{{ __('Done selecting') }}</button>
			</div>
			<div class="max-h-56 overflow-y-auto">
				<label v-for="option in visible" :key="option.value" class="flex cursor-pointer items-start gap-2 rounded p-2 hover:bg-gray-50">
					<input type="checkbox" class="mt-1" :checked="model.includes(option.value)" @change="toggle(option.value)" />
					<span class="break-words">{{ option.label }}</span>
				</label>
				<p v-if="!visible.length" class="p-2 text-gray-500">{{ __('No options available') }}</p>
			</div>
		</div>
	</div>
</template>
