<script setup>
import { __ } from '../../translation'
const props = defineProps({
	title: { type: String, required: false },
})
</script>

<template>
	<div class="px-4 py-2 font-medium leading-6 text-gray-800">
		{{ title ? __(title) : '' }}
	</div>
</template>
