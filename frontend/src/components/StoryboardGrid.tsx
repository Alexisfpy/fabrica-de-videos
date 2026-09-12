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
      if (assetByItem.get(item.id)?.status === 'error') c.error += 1
    })
    return c
  }, [storyboard, assetByItem])

  const filtered = storyboard.filter((item) => {
    if (filter === 'all') return true
    if (filter === 'error') return assetByItem.get(item.id)?.status === 'error'
    return item.shot_type === filter
  })

  const retry = async (assetId: string) => {
    await api.retryAsset(assetId)
    setTimeout(onRetried, 900)
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-4">
        {(['all', 'graphic', 'stock', 'ai_generated', 'error'] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`font-mono text-xs px-2.5 py-1 rounded-full border transition ${
              filter === f
                ? 'border-signal text-signal'
                : 'border-line text-muted hover:border-signal-dim'
            }`}
          >
            {f === 'all' ? 'todos' : f === 'error' ? 'error' : SHOT_LABEL[f]} {counts[f]}
          </button>
        ))}
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {filtered.map((item) => {
          const asset = assetByItem.get(item.id)
          return (
            <div
              key={item.id}
              className={`border rounded-lg p-3 bg-panel-raised flex flex-col gap-2 ${
                asset?.status === 'error' ? 'border-error/60' : 'border-line'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs text-muted">#{item.order}</span>
                <span className={`font-mono text-[11px] uppercase ${SHOT_COLOR[item.shot_type]}`}>
                  {SHOT_LABEL[item.shot_type]}
                </span>
              </div>
              <p className="text-sm leading-snug line-clamp-3">{item.description}</p>
              <div className="flex items-center justify-between mt-auto pt-1">
                <span className="font-mono text-xs text-muted">{item.duration_seconds.toFixed(1)}s</span>
                {!asset && <span className="font-mono text-[11px] text-muted">sin material</span>}
                {asset?.status === 'ready' && (
                  <span className="font-mono text-[11px] text-signal">listo</span>
                )}
                {asset?.status === 'error' && (
                  <button
                    onClick={() => retry(asset.id)}
                    className="font-mono text-[11px] text-error hover:underline"
                  >
                    reabrir →
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
