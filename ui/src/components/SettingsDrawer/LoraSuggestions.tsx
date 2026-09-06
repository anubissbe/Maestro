import { useRef, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { suggestLoras, uploadImage, type LoraSuggestion } from '../../api/client'
import { useStore } from '../../stores/useStore'
import { appendLoraKeywords } from '../../lib/loraKeywords'

export function LoraSuggestions({ includeNsfw, director }: {
  includeNsfw: boolean
  director?: {
    mode: 'image' | 'video'; modelType: string; active: string[]; available: string[]
    onAdd: (filename: string) => void
  }
}) {
  const studioModel = useStore(s => s.params.model_type)
  const studioPrompt = useStore(s => s.params.prompt)
  const studioActive = useStore(s => s.params.activated_loras)
  const scene = useStore(s => s.directorSceneDescription)
  const plans = useStore(s => s.directorClipPlans)
  const directorImage = useStore(s => s.directorReferenceImage)
  const directorPath = useStore(s => s.directorReferenceImagePath)
  const characterFiles = useStore(s => s.directorCharacterRefs)
  const characterPaths = useStore(s => s.directorCharacterRefPaths)
  const locationFiles = useStore(s => s.directorLocationRefs)
  const locationPaths = useStore(s => s.directorLocationRefPaths)
  const omniRefs = useStore(s => s.directorH3References)
  const directorLoading = useStore(s => s.directorLoading)
  const field = director?.mode === 'image' ? 'image_prompt' : 'video_prompt'
  const model = director?.modelType ?? studioModel
  const prompt = director ? [scene, ...plans.map(p => p[field])].filter(Boolean).join('\n\n') : studioPrompt
  const active = director?.active ?? studioActive
  const start = useStore(s => s.startImage)
  const savedStart = useStore(s => s.params.image_start)
  const refs = useStore(s => s.imageRefs)
  const mode = useStore(s => s.generationMode)
  const workflow = useStore(s => s.studioImageWorkflow)
  const source = useStore(s => s.imageWorkflowSourcePath)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ key: string; rows: LoraSuggestion[]; imageUsed: boolean } | null>(null)
  // File identity matters even when two uploads have identical filenames.
  const fileIds = useRef(new WeakMap<File, number>())
  const nextId = useRef(0)
  const fileId = (file: File) => {
    if (!fileIds.current.has(file)) fileIds.current.set(file, ++nextId.current)
    return fileIds.current.get(file)
  }
  const key = JSON.stringify(director
    ? [director.mode, model, prompt, active, director.available, directorLoading,
      directorImage ? fileId(directorImage) : null, directorPath,
      characterFiles.map(fileId), characterPaths, locationFiles.map(fileId), locationPaths,
      omniRefs, includeNsfw]
    : [model, prompt, active, start ? fileId(start) : null,
      savedStart, refs.map(fileId), mode, workflow, source, includeNsfw])
  const currentKey = useRef(key)
  currentKey.current = key
  const visible = result?.key === key ? result : null

  const suggest = async () => {
    setBusy(true)
    setError('')
    setResult(null)
    const requestKey = key
    try {
      const paths: string[] = []
      if (director) {
        if (directorImage) paths.push((await uploadImage(directorImage)).path)
        else if (directorPath) paths.push(directorPath)
        for (const [files, saved] of [[characterFiles, characterPaths], [locationFiles, locationPaths]] as const) {
          if (files.length) for (const file of files) paths.push((await uploadImage(file)).path)
          else paths.push(...saved)
        }
        paths.push(...omniRefs.filter(ref => ref.type === 'image').map(ref => ref.path))
      } else if (mode === 'image') {
        if ((workflow === 'inpaint' || workflow === 'outpaint') && source) paths.push(source)
        else for (const file of refs) paths.push((await uploadImage(file)).path)
      } else if (start) paths.push((await uploadImage(start)).path)
      else if (typeof savedStart === 'string' && savedStart) paths.push(savedStart)
      const uniquePaths = [...new Set(paths)]
      if (uniquePaths.length > 8) throw new Error('Use at most eight reference images for LoRA suggestions.')
      const response = await suggestLoras({ model_type: model, prompt, image_paths: uniquePaths,
        active_loras: active, include_nsfw: includeNsfw })
      if (currentKey.current === requestKey) setResult({ key: requestKey, rows: response.suggestions, imageUsed: response.image_used })
    } catch (e) {
      if (currentKey.current === requestKey) setError(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const apply = (row: LoraSuggestion) => {
    if (currentKey.current !== result?.key || row.conflicts.length) return
    const state = useStore.getState()
    if (director) {
      if (state.directorLoading || director.active.includes(row.filename) || !director.available.includes(row.filename)) return
      director.onAdd(row.filename)
      state.directorClipPlans.forEach((plan, index) => {
        if (plan[field]) state.directorEditClipPlan(index, field, appendLoraKeywords(plan[field], row.trained_words))
      })
      // Future Director plans use the selected LoRAs' trainedWords through
      // the existing prompt-polish pass; image/video keywords stay separate.
      setResult(null)
      return
    }
    if (state.params.activated_loras.includes(row.filename) || !state.availableLoras.includes(row.filename)) return
    const updated = appendLoraKeywords(state.params.prompt, row.trained_words)
    state.toggleLora(row.filename)
    state.setParam('prompt', updated)
    setResult(null)
  }

  return (
    <div className="mb-2 rounded-lg border border-border p-2 text-[11px]">
      <button onClick={suggest} disabled={busy || !model || (!!director && directorLoading)}
        className="flex items-center gap-1 text-accent-blue disabled:opacity-50">
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
        {busy ? 'Checking LoRAs…' : 'Suggest LoRAs from prompt + image'}
      </button>
      {error && <p role="alert" className="mt-2 text-red-400">{error}</p>}
      {visible && <div className="mt-2 space-y-2">
        <p className="text-text-muted">{visible.imageUsed ? 'Based on your prompt and image.' : 'Based on your prompt.'} Combinations are assessed from available metadata.</p>
        {!visible.rows.length && <p className="text-text-muted">No suitable downloaded LoRAs found.</p>}
        {visible.rows.map(row => <div key={row.filename} className="rounded bg-bg-tertiary p-2">
          <p className="break-words text-text-primary">{row.filename}</p>
          <p className="mt-1 text-text-secondary">{row.reason}</p>
          {row.trained_words.length > 0 && <p className="mt-1 text-text-muted">Keywords: {row.trained_words.join(', ')}</p>}
          {!!row.warnings?.length && <p className="mt-1 text-text-muted">AI advice: {row.warnings.join(' ')} You can still add this LoRA.</p>}
          {row.conflicts.length > 0 && <p className="mt-1 text-indicator-warning">Cannot add: {row.conflicts.join(', ')}.</p>}
          <button onClick={() => apply(row)} disabled={row.conflicts.length > 0}
            className="mt-1 text-accent-blue disabled:opacity-40">Add LoRA + keywords</button>
        </div>)}
      </div>}
    </div>
  )
}
