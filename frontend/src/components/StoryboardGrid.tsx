import { useMemo, useState } from 'react'
import type { Asset, StoryboardItem } from '../types'
import { api } from '../api'

const SHOT_LABEL: Record<string, string> = {
  graphic: 'gráfico',
  stock: 'archivo',
  ai_generated: 'IA',
}

const SHOT_COLOR: Record<string, string> = {
  graphic: 'text-bloque',
  stock: 'text-cine',
  ai_generated: 'text-signal',
}

interface Props {
  projectId: string
  storyboard: StoryboardItem[]
  assets: Asset[]
  onRetried: () => void
}

export default function StoryboardGrid({ projectId, storyboard, assets, onRetried }: Props) {
  const [filter, setFilter] = useState<'all' | 'graphic' | 'stock' | 'ai_generated' | 'error'>('all')
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null)
  const [cacheBuster, setCacheBuster] = useState<number>(Date.now())
  const [focusOverrides, setFocusOverrides] = useState<Record<string, { x: number; y: number }>>({})

  const assetByItem = useMemo(() => {
    const map = new Map<string, Asset>()
    assets.forEach((a) => map.set(a.storyboard_item_id, a))
    return map
  }, [assets])

  const counts = useMemo(() => {
    const c = { all: storyboard.length, graphic: 0, stock: 0, ai_generated: 0, error: 0 }
    storyboard.forEach((item) => {
      c[item.shot_type as 'graphic' | 'stock' | 'ai_generated'] += 1
      const a = assetByItem.get(item.id)
      if (a?.status === 'error' || a?.error_message) c.error += 1
    })
    return c
  }, [storyboard, assetByItem])

  const filtered = storyboard.filter((item) => {
    const a = assetByItem.get(item.id)
    const isError = a?.status === 'error' || Boolean(a?.error_message)
    if (filter === 'all') return true
    if (filter === 'error') return isError
    return item.shot_type === filter
  })

  const retry = async (assetId: string) => {
    await api.retryAsset(assetId)
    setTimeout(onRetried, 900)
  }

  const handleRegenerateShot = async (shotId: string) => {
    setRegeneratingId(shotId)
    try {
      const res = await fetch(`http://localhost:8000/api/projects/${projectId}/shots/${shotId}/regenerate`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error('Error al regenerar el plano')
      setCacheBuster(Date.now())
      onRetried()
    } catch (err) {
      console.error(err)
    } finally {
      setRegeneratingId(null)
    }
  }

  const handleUploadShot = async (shotId: string, file: File) => {
    setRegeneratingId(shotId)
    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch(`http://localhost:8000/api/projects/${projectId}/shots/${shotId}/upload`, {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) throw new Error('Error al subir la imagen')
      setCacheBuster(Date.now())
      onRetried()
    } catch (err) {
      console.error('Error al subir imagen:', err)
    } finally {
      setRegeneratingId(null)
    }
  }

  const handleUpdateFocus = async (shotId: string, focusX: number, focusY: number) => {
    setFocusOverrides((prev) => ({ ...prev, [shotId]: { x: focusX, y: focusY } }))

    try {
      const res = await fetch(`http://localhost:8000/api/projects/${projectId}/shots/${shotId}/focus`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ focus_x: focusX, focus_y: focusY }),
      })
      if (!res.ok) throw new Error('Error al actualizar el foco')
      onRetried()
    } catch (err) {
      console.error('Error actualizando posición:', err)
    }
  }

  return (
    <div>
      {/* Barra de filtros */}
      <div className="flex flex-wrap gap-2 mb-4">
        {(['all', 'graphic', 'stock', 'ai_generated', 'error'] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`font-mono text-xs px-2.5 py-1 rounded-full border transition ${
              filter === f
                ? 'border-signal text-signal bg-signal/5'
                : 'border-line text-muted hover:border-signal-dim'
            }`}
          >
            {f === 'all' ? 'todos' : f === 'error' ? 'error' : SHOT_LABEL[f]} {counts[f]}
          </button>
        ))}
      </div>

      {/* Grid de planos */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered.map((item) => {
          const asset = assetByItem.get(item.id)
          const isError = asset?.status === 'error' || Boolean(asset?.error_message)
          const isRegenerating = regeneratingId === item.id
          const errorMsg = asset?.error_message ?? 'Fallo de conexión o límite de peticiones (429)'
          
          const focusX = focusOverrides[item.id]?.x ?? (item as any).focus_x ?? 50
          const focusY = focusOverrides[item.id]?.y ?? (item as any).focus_y ?? 50

          return (
            <div
              key={item.id}
              className={`border rounded-lg p-3 bg-panel-raised flex flex-col gap-2.5 transition-colors ${
                isError ? 'border-error/70 shadow-sm shadow-error/10' : 'border-line'
              }`}
            >
              {/* Cabecera del plano */}
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs text-muted">#{item.order}</span>
                <span className={`font-mono text-[11px] uppercase ${SHOT_COLOR[item.shot_type]}`}>
                  {SHOT_LABEL[item.shot_type]}
                </span>
              </div>

              {/* Descripción */}
              <p className="text-sm leading-snug line-clamp-2" title={item.description}>
                {item.description}
              </p>

              {/* Miniatura / Contenedor visual */}
              {asset?.url ? (
                <div
                  className={`relative group/thumb w-full aspect-video rounded-md overflow-hidden border bg-black/40 ${
                    isError ? 'border-error/80 ring-1 ring-error/50' : 'border-line'
                  }`}
                  title={isError ? `Motivo del error: ${errorMsg}` : undefined}
                >
                  <img
                    src={`${asset.url}?t=${cacheBuster}`}
                    alt={item.description}
                    loading="lazy"
                    style={{
                      objectPosition: `${focusX}% ${focusY}%`,
                      transformOrigin: `${focusX}% ${focusY}%`,
                    }}
                    className={`w-full h-full object-cover scale-125 transition-all duration-300 ${
                      isRegenerating
                        ? 'opacity-25'
                        : isError
                        ? 'opacity-65 grayscale-30'
                        : ''
                    }`}
                  />

                  {/* Indicador de proceso en curso */}
                  {isRegenerating && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/50 text-signal font-mono text-xs gap-1.5 z-20">
                      <span className="w-4 h-4 border-2 border-signal border-t-transparent rounded-full animate-spin" />
                      <span>procesando...</span>
                    </div>
                  )}

                  {/* Indicador de estado para plano erróneo */}
                  {isError && !isRegenerating && (
                    <div className="absolute top-1.5 right-1.5 bg-error/90 text-white font-mono text-[10px] uppercase font-bold px-1.5 py-0.5 rounded shadow-md pointer-events-none">
                      Error
                    </div>
                  )}

                  {/* Tooltip superpuesto al pasar el ratón si falló */}
                  {isError && !isRegenerating && (
                    <div className="absolute inset-0 bg-black/85 backdrop-blur-[2px] p-3 flex flex-col justify-center items-center text-center opacity-0 group-hover/thumb:opacity-100 transition-opacity duration-150 pointer-events-none z-10">
                      <span className="text-error font-mono text-[11px] font-semibold mb-1">
                        Fallo de generación
                      </span>
                      <p className="text-muted text-xs leading-tight line-clamp-4">
                        {errorMsg}
                      </p>
                    </div>
                  )}
                </div>
              ) : isError ? (
                <div
                  className="w-full aspect-video rounded-md border border-dashed border-error/60 bg-error/5 flex flex-col items-center justify-center p-3 text-center"
                  title={`Motivo del error: ${errorMsg}`}
                >
                  <span className="text-error font-mono text-xs font-semibold mb-1">Error de material</span>
                  <p className="text-muted text-[11px] line-clamp-2 leading-tight">{errorMsg}</p>
                </div>
              ) : null}

              {/* Pie de tarjeta: duración, estado y botones de acción */}
              <div className="flex items-center justify-between mt-auto pt-1 border-t border-line/40">
                <span className="font-mono text-xs text-muted">{item.duration_seconds.toFixed(1)}s</span>

                <div className="flex items-center gap-1.5">
                  {!asset && <span className="font-mono text-[11px] text-muted mr-1">sin material</span>}

                  {asset && !isError && asset.status === 'ready' && (
                    <span className="font-mono text-[11px] text-signal font-medium mr-1">listo</span>
                  )}

                  {isError && (
                    <button
                      onClick={() => asset?.id && retry(asset.id)}
                      title={`Reintentar generación: ${errorMsg}`}
                      className="font-mono text-[11px] text-error hover:text-error/80 hover:underline font-semibold"
                    >
                      reabrir →
                    </button>
                  )}

                  {/* Selector rápido de encuadre */}
                  {asset?.url && (
                    <select
                      value={`${focusX}-${focusY}`}
                      onChange={(e) => {
                        const [x, y] = e.target.value.split('-').map(Number)
                        handleUpdateFocus(item.id, x, y)
                      }}
                      disabled={isRegenerating}
                      className="bg-panel border border-line rounded px-1 py-0.5 font-mono text-[10px] text-muted outline-none hover:border-signal-dim transition"
                      title="Alinear encuadre visual"
                    >
                      <option value="50-50">Centro</option>
                      <option value="50-0">Arriba</option>
                      <option value="50-100">Abajo</option>
                      <option value="0-50">Izquierda</option>
                      <option value="100-50">Derecha</option>
                      <option value="0-0">Arriba-Izquierda</option>
                      <option value="100-0">Arriba-Derecha</option>
                      <option value="0-100">Abajo-Izquierda</option>
                      <option value="100-100">Abajo-Derecha</option>
                    </select>
                  )}

                  {/* Botón para subir archivo local */}
                  <label
                    className={`font-mono text-[11px] text-muted hover:text-signal border border-line rounded px-1.5 py-0.5 cursor-pointer hover:border-signal-dim transition ${
                      isRegenerating ? 'opacity-40 pointer-events-none' : ''
                    }`}
                    title="Subir imagen desde tu ordenador"
                  >
                    ↑ subir
                    <input
                      type="file"
                      accept="image/png, image/jpeg, image/webp"
                      className="hidden"
                      disabled={isRegenerating}
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        if (file) {
                          handleUploadShot(item.id, file)
                          e.target.value = ''
                        }
                      }}
                    />
                  </label>

                  {/* Botón para regenerar con IA */}
                  <button
                    onClick={() => handleRegenerateShot(item.id)}
                    disabled={isRegenerating}
                    className="font-mono text-[11px] text-muted hover:text-signal border border-line rounded px-1.5 py-0.5 disabled:opacity-40 transition"
                    title="Regenerar esta toma individual con Cloudflare"
                  >
                    {isRegenerating ? '...' : '↻ regenerar'}
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}