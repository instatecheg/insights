import { createResource } from "frappe-ui";
import type { App } from "vue";

function getTranslatedMessage(message: string): string {
    const translatedMessages = (("translatedMessages" in window ? window["translatedMessages"] : null) ?? {}) as Record<string, string>;
    return translatedMessages[message] || message;
}

function translate(message: string): string;
function translate(message: string, ...args: string[]): string;
function translate(message: string, ...args: string[]): string {
    const translatedMessage = getTranslatedMessage(message)
    if (args.length === 0) {
        return translatedMessage;
    }
    return translatedMessage.replace(/{(\d+)}/g, function (match, index) {
        return typeof args[index] != 'undefined' ? args[index] : match;
    });
}

export const __ = translate;

declare global {
    interface Window {
        translatedMessages?: Record<string, string>;
        __?: typeof translate;
    }
}

export let language = 'en'
export let locale = 'en-US'
export let direction: 'rtl' | 'ltr' = 'ltr'

export async function initializeTranslations() {
    const [selectedLanguage, messages] = await Promise.all([
        createResource({ url: 'insights.api.translations.get_language', method: 'GET' }).fetch(),
        createResource({ url: 'insights.api.translations.get_translations', method: 'GET' }).fetch(),
    ])
    language = selectedLanguage
    locale = language.startsWith('ar') ? 'ar-SA-u-ca-gregory-nu-latn' : language
    direction = /^(ar|he|fa|ur)(-|$)/.test(language) ? 'rtl' : 'ltr'
    window.translatedMessages = messages
    document.documentElement.lang = language
    document.documentElement.dir = direction
}

export function translateHtml(html: string): string {
    const template = document.createElement('template')
    template.innerHTML = html
    const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT)
    let node: Node | null
    while ((node = walker.nextNode())) {
        const text = node.textContent || ''
        const trimmed = text.trim()
        if (trimmed) node.textContent = text.replace(trimmed, translate(trimmed))
    }
    return template.innerHTML
}

export function periodLabel(period: { label: string; start: string; end: string }): string {
    if (!language.startsWith('ar')) return period.label
    const from = new Date(`${period.start}T12:00:00`)
    const to = new Date(`${period.end}T12:00:00`)
    if (period.label === String(from.getFullYear())) return String(from.getFullYear())
    const format = new Intl.DateTimeFormat(locale, { month: 'short', year: 'numeric' })
    const start = format.format(from)
    const end = format.format(to)
    return start === end ? start : `${start} – ${end}`
}

export function translationPlugin(app: App<Element>) {
    app.config.globalProperties.__ = translate;
    window.__ = translate;
}

declare module '@vue/runtime-core' {
  interface ComponentCustomProperties {
    __: typeof translate;
  }
}
