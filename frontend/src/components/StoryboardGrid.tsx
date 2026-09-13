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
  storyboard: StoryboardItem[]
  assets: Asset[]
  onRetried: () => void
}

export default function StoryboardGrid({ storyboard, assets, onRetried }: Props) {
  const [filter, setFilter] = useState<'all' | 'graphic' | 'stock' | 'ai_generated' | 'error'>('all')
  
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
          const errorMsg = asset?.error_message ?? 'Fallo de conexión o límite de peticiones (429)'

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
                    src={asset.url}
                    alt={item.description}
                    loading="lazy"
                    className={`w-full h-full object-cover transition-transform duration-200 ${
                      isError ? 'opacity-65 grayscale-30' : 'group-hover/thumb:scale-105'
                    }`}
                  />

                  {/* Indicador de estado para plano erróneo */}
                  {isError && (
                    <div className="absolute top-1.5 right-1.5 bg-error/90 text-white font-mono text-[10px] uppercase font-bold px-1.5 py-0.5 rounded shadow-md pointer-events-none">
                      Error
                    </div>
                  )}

                  {/* Tooltip superpuesto que aparece al pasar el ratón si falló */}
                  {isError && (
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
                /* Estado en el que hubo error antes de generar URL */
                <div
                  className="w-full aspect-video rounded-md border border-dashed border-error/60 bg-error/5 flex flex-col items-center justify-center p-3 text-center"
                  title={`Motivo del error: ${errorMsg}`}
                >
                  <span className="text-error font-mono text-xs font-semibold mb-1">Error de material</span>
                  <p className="text-muted text-[11px] line-clamp-2 leading-tight">{errorMsg}</p>
                </div>
              ) : null}

              {/* Pie de tarjeta: duración y acción */}
              <div className="flex items-center justify-between mt-auto pt-1 border-t border-line/40">
                <span className="font-mono text-xs text-muted">{item.duration_seconds.toFixed(1)}s</span>
                
                {!asset && <span className="font-mono text-[11px] text-muted">sin material</span>}
                
                {asset && !isError && asset.status === 'ready' && (
                  <span className="font-mono text-[11px] text-signal font-medium">listo</span>
                )}

                {isError && (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => asset?.id && retry(asset.id)}
                      title={`Reintentar generación: ${errorMsg}`}
                      className="font-mono text-[11px] text-error hover:text-error/80 hover:underline flex items-center gap-1 font-semibold"
                    >
                      reabrir →
                    </button>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}