import { frappeRequest, setConfig } from 'frappe-ui'
import { spritePlugin } from 'frappe-ui/icons'
import { GridItem, GridLayout } from 'grid-layout-plus'
import { createPinia } from 'pinia'
import { createApp, watchEffect } from 'vue'
import App from './App.vue'
import { registerControllers, registerGlobalComponents } from './globals.ts'
import './index.css'
import router from './router.ts'
import { initializeTranslations, translationPlugin } from './translation.ts'
import telemetryPlugin from './telemetry'
import session from './session.ts'

setConfig('resourceFetcher', frappeRequest)

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(spritePlugin)
app.component('grid-layout', GridLayout)
app.component('grid-item', GridItem)

const stop = watchEffect(() => {
	if (session.isLoggedIn) {
		app.use(telemetryPlugin, { app_name: 'insights' })
		stop()
	}
})

app.config.errorHandler = (err, vm, info) => {
	console.groupCollapsed('Unhandled Error in: ', info)
	console.error('Context:', vm)
	console.error('Error:', err)
	console.groupEnd()
	return false
}

registerGlobalComponents(app)
registerControllers(app)

async function mount() {
	await initializeTranslations()
	app.use(translationPlugin)
	app.use(router)
	app.mount('#app')
}
mount().catch(error => {
	console.error('Unable to initialize Insights language', error)
	const root = document.getElementById('app')
	if (root) {
		root.setAttribute('role', 'alert')
		root.textContent = `Unable to load Insights language. Reload to retry. ${error instanceof Error ? error.message : String(error)}`
	}
})
