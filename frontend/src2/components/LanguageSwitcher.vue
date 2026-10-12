<script setup lang="ts">
import { call } from 'frappe-ui'
import { ref } from 'vue'
import session from '../session'
import { __, language } from '../translation'

const selected = ref(language.startsWith('ar') ? 'ar' : 'en')
const saving = ref(false)
const error = ref('')
async function change() {
	saving.value = true
	error.value = ''
	try {
		await call('insights.api.translations.set_language', { language: selected.value })
		window.dispatchEvent(new Event('insights:language-change'))
		window.location.reload()
	} catch (failure) {
		selected.value = language.startsWith('ar') ? 'ar' : 'en'
		error.value = failure instanceof Error ? failure.message : String(failure)
		saving.value = false
	}
}
</script>

<template>
	<div v-if="session.isLoggedIn" class="text-sm">
		<select v-model="selected" aria-label="Insights language" :title="__('Language applies to Insights only')" :disabled="saving" class="rounded border border-gray-300 bg-white px-2 py-1" @change="change">
			<option value="en">English</option>
			<option value="ar">العربية</option>
		</select>
		<p v-if="error" role="alert" class="text-red-700">{{ error }}</p>
	</div>
</template>
