/** Keep trigger spelling intact and add only phrases absent from the prompt. */
export function appendLoraKeywords(prompt: string, words: string[]): string {
  let updated = prompt
  for (const word of words) {
    const trigger = word.trim()
    const escaped = trigger.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    if (trigger && !new RegExp(`(^|[^\\p{L}\\p{N}_])${escaped}(?=$|[^\\p{L}\\p{N}_])`, 'iu').test(updated)) {
      updated = updated.trimEnd() + (updated.trim() ? ', ' : '') + trigger
    }
  }
  return updated
}
