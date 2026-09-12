import { useState } from 'react'
import type { ScriptBlock } from '../types'

interface Props {
  script: ScriptBlock[]
  onSave: (script: ScriptBlock[]) => Promise<void>
  onRegenerate: () => Promise<void>
  regenerating: boolean
}

export default function ScriptEditor({ script, onSave, onRegenerate, regenerating }: Props) {
  const [blocks, setBlocks] = useState(script)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  const totalSeconds = blocks.reduce((acc, b) => acc + b.estimated_seconds, 0)
  const totalWords = blocks.reduce((acc, b) => acc + b.text.split(/\s+/).filter(Boolean).length, 0)

  const updateText = (id: string, text: string) => {
    setBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, text } : b)))
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      await onSave(blocks)
      setDirty(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-3">
        <button
          onClick={onRegenerate}
          disabled={regenerating}
          className="bg-signal text-ink font-medium text-sm px-3 py-1.5 rounded-md disabled:opacity-40 hover:brightness-110 transition"
        >
          {regenerating ? 'Reescribiendo...' : 'Reescribir'}
        </button>
        <span className="font-mono text-xs text-muted">
          {Math.round(totalSeconds / 60)} min · {totalWords} palabras
        </span>
        {dirty && (
          <button
            onClick={save}
            disabled={saving}
            className="font-mono text-xs text-signal hover:underline ml-auto disabled:opacity-40"
          >
            {saving ? 'guardando...' : 'guardar cambios'}
          </button>
        )}
      </div>

      <div className="flex flex-col gap-3">
        {blocks.map((block) => (
          <div key={block.id} className="border border-line rounded-md bg-panel-raised p-3">
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-mono text-xs text-muted">bloque {block.order}</span>
              <span className="font-mono text-xs text-muted">{block.estimated_seconds.toFixed(0)}s</span>
            </div>
            <textarea
              value={block.text}
              onChange={(e) => updateText(block.id, e.target.value)}
              rows={2}
              className="w-full bg-transparent outline-none resize-none text-sm leading-relaxed"
            />
          </div>
        ))}
      </div>
    </div>
  )
}
