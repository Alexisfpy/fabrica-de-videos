import { useState } from 'react'
import type { VisualStyleGuide as VisualStyleGuideType } from '../types'

/**
 * Muestra el "Concepto Visual" del proyecto: el estilo artístico que se
 * aplicará a TODAS las imágenes generadas, y la guía de personajes/lugares
 * clave para que se dibujen igual en cada plano.
 *
 * Antes no existía nada de esto: cada imagen se generaba con un estilo fijo
 * en el código ("anime aesthetic") sin relación con el tema real del vídeo.
 * Aquí se puede revisar y corregir a mano antes de gastar créditos generando
 * el material, o volver a detectarlo si el resultado automático no encaja.
 */
export default function VisualStyleGuidePanel({
  guide,
  onSave,
  onRegenerate,
  regenerating,
}: {
  guide: VisualStyleGuideType | null
  onSave: (guide: VisualStyleGuideType) => void
  onRegenerate: () => void
  regenerating: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [style, setStyle] = useState(guide?.visual_style ?? '')
  const [entities, setEntities] = useState<[string, string][]>(
    Object.entries(guide?.entities ?? {})
  )

  const startEditing = () => {
    setStyle(guide?.visual_style ?? '')
    setEntities(Object.entries(guide?.entities ?? {}))
    setEditing(true)
  }

  const save = () => {
    const cleanEntities = Object.fromEntries(
      entities.filter(([name]) => name.trim().length > 0)
    )
    onSave({ visual_style: style.trim(), entities: cleanEntities })
    setEditing(false)
  }

  if (!guide && !editing) {
    return (
      <div className="border border-line rounded-md p-3 mb-4 bg-panel-raised">
        <p className="text-sm text-muted mb-2">
          Aún no se ha definido el estilo visual del proyecto (se genera automáticamente
          al crear el plan visual).
        </p>
        <button
          onClick={onRegenerate}
          disabled={regenerating}
          className="font-mono text-xs border border-signal-dim text-signal px-2.5 py-1 rounded-md hover:bg-signal/10 transition disabled:opacity-40"
        >
          {regenerating ? 'Detectando estilo...' : 'Detectar estilo ahora'}
        </button>
      </div>
    )
  }

  if (editing) {
    return (
      <div className="border border-signal-dim rounded-md p-3 mb-4 bg-panel-raised space-y-3">
        <div>
          <label className="font-mono text-[11px] text-muted block mb-1">
            ESTILO ARTÍSTICO (en inglés, se aplica a todas las imágenes)
          </label>
          <textarea
            value={style}
            onChange={(e) => setStyle(e.target.value)}
            rows={2}
            className="w-full bg-panel border border-line rounded-md px-2.5 py-1.5 text-sm outline-none focus:border-signal-dim"
            placeholder="ej: Cinematic historical drama film still, realistic textures, natural lighting, 35mm lens"
          />
        </div>

        <div>
          <label className="font-mono text-[11px] text-muted block mb-1">
            PERSONAJES / LUGARES CLAVE (para que se vean igual en cada plano)
          </label>
          <div className="space-y-1.5">
            {entities.map(([name, desc], i) => (
              <div key={i} className="flex gap-1.5">
                <input
                  value={name}
                  onChange={(e) =>
                    setEntities((prev) => prev.map((p, j) => (j === i ? [e.target.value, p[1]] : p)))
                  }
                  placeholder="Nombre"
                  className="w-1/3 bg-panel border border-line rounded-md px-2 py-1 text-xs outline-none focus:border-signal-dim"
                />
                <input
                  value={desc}
                  onChange={(e) =>
                    setEntities((prev) => prev.map((p, j) => (j === i ? [p[0], e.target.value] : p)))
                  }
                  placeholder="Descripción física en inglés"
                  className="flex-1 bg-panel border border-line rounded-md px-2 py-1 text-xs outline-none focus:border-signal-dim"
                />
                <button
                  onClick={() => setEntities((prev) => prev.filter((_, j) => j !== i))}
                  className="text-muted hover:text-error text-xs px-1"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
          <button
            onClick={() => setEntities((prev) => [...prev, ['', '']])}
            className="font-mono text-[11px] text-muted hover:text-signal mt-1.5"
          >
            + añadir personaje/lugar
          </button>
        </div>

        <div className="flex gap-2">
          <button
            onClick={save}
            className="bg-signal text-ink font-medium text-xs px-3 py-1.5 rounded-md hover:brightness-110 transition"
          >
            Guardar
          </button>
          <button
            onClick={() => setEditing(false)}
            className="font-mono text-xs text-muted hover:text-paper px-2 py-1.5"
          >
            Cancelar
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="border border-line rounded-md p-3 mb-4 bg-panel-raised">
      <div className="flex items-start justify-between gap-3 mb-2">
        <p className="text-sm leading-relaxed">{guide!.visual_style}</p>
        <div className="flex gap-1.5 shrink-0">
          <button
            onClick={startEditing}
            className="font-mono text-[11px] text-muted hover:text-signal px-2 py-1 border border-line rounded-md"
          >
            Editar
          </button>
          <button
            onClick={onRegenerate}
            disabled={regenerating}
            className="font-mono text-[11px] text-muted hover:text-signal px-2 py-1 border border-line rounded-md disabled:opacity-40"
          >
            {regenerating ? '...' : 'Redetectar'}
          </button>
        </div>
      </div>

      {Object.keys(guide!.entities).length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {Object.entries(guide!.entities).map(([name, desc]) => (
            <span
              key={name}
              title={desc}
              className="font-mono text-[10px] border border-line rounded-full px-2 py-0.5 text-muted"
            >
              {name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
