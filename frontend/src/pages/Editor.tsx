import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import type { Asset, Project, Voice } from '../types'
import StepCard from '../components/StepCard'
import StatusBadge from '../components/StatusBadge'
import ScriptEditor from '../components/ScriptEditor'
import StoryboardGrid from '../components/StoryboardGrid'
import VisualStyleGuidePanel from '../components/VisualStyleGuide'

const BUSY_STATES = ['analyzing', 'scripting', 'storyboarding', 'provisioning', 'rendering']
const GRAPHIC_STYLES: { id: Project['graphics_style']; label: string; dot: string }[] = [
  { id: 'neon', label: 'Neón', dot: 'bg-signal' },
  { id: 'ambar', label: 'Ámbar', dot: 'bg-amber' },
  { id: 'bloque', label: 'Bloque', dot: 'bg-bloque' },
  { id: 'cine', label: 'Cine', dot: 'bg-cine' },
]

function formatDuration(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function Editor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [project, setProject] = useState<Project | null>(null)
  const [assets, setAssets] = useState<Asset[]>([])
  const [voices, setVoices] = useState<Voice[]>([])
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    if (!id) return
    const [p, a] = await Promise.all([api.getProject(id), api.listAssets(id)])
    setProject(p)
    setAssets(a)
    return p
  }, [id])

  useEffect(() => {
    refresh()
    api.listVoices().then(setVoices)
  }, [refresh])

  // Polling mientras el proyecto está en un estado "ocupado" (equivalente al
  // WebSocket de progreso del diseño técnico; aquí, por simplicidad, polling).
  useEffect(() => {
    if (project && BUSY_STATES.includes(project.status)) {
      pollRef.current = window.setInterval(refresh, 1200)
      return () => {
        if (pollRef.current) window.clearInterval(pollRef.current)
      }
    }
  }, [project, refresh])

  if (!project) {
    return <div className="max-w-4xl mx-auto px-6 py-10 text-muted">Cargando proyecto...</div>
  }

  const busy = BUSY_STATES.includes(project.status)

  const run = async (action: string, fn: () => Promise<unknown>) => {
    setBusyAction(action)
    try {
      await fn()
      await new Promise((r) => setTimeout(r, 500))
      await refresh()
    } finally {
      setBusyAction(null)
    }
  }

  const assetsReady = assets.filter((a) => a.status === 'ready').length
  const assetsError = assets.filter((a) => a.status === 'error').length
  const assetsTotal = project.storyboard?.length ?? 0
  const allProvisioned = assetsTotal > 0 && assetsReady + assetsError === assetsTotal

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <button onClick={() => navigate('/')} className="font-mono text-xs text-muted hover:text-signal mb-6">
        ← tus vídeos
      </button>

      <div className="mb-8">
        <div className="flex items-start justify-between gap-4 mb-2">
          <h1 className="text-xl font-semibold leading-snug">{project.title}</h1>
          <StatusBadge status={project.status} />
        </div>
        {project.description && <p className="text-muted text-sm mb-3">{project.description}</p>}
        <div className="flex flex-wrap gap-2 font-mono text-xs">
          <span className="border border-line rounded-full px-2.5 py-1 text-muted">{project.format}</span>
          <span className="border border-line rounded-full px-2.5 py-1 text-muted">
            {Math.round(project.target_duration_seconds / 60)} MIN OBJETIVO
          </span>
          {project.actual_duration_seconds != null && (
            <span className="border border-signal-dim rounded-full px-2.5 py-1 text-signal">
              {formatDuration(project.actual_duration_seconds)} REALES
            </span>
          )}
          {assetsTotal > 0 && (
            <span className="border border-line rounded-full px-2.5 py-1 text-muted">
              {assetsTotal} PLANOS
            </span>
          )}
        </div>
      </div>

      {project.error_message && (
        <div className="border border-error/60 bg-error/10 text-error text-sm rounded-md px-3 py-2 mb-6">
          {project.error_message}
        </div>
      )}

      <div>
        {/* 1. Referencia */}
        <StepCard
          number={1}
          title="Referencia"
          meta={
            project.reference_analysis
              ? `un plano cada ${project.reference_analysis.avg_shot_seconds}s · ${project.reference_analysis.total_shots} escenas`
              : undefined
          }
        >
          {!project.reference_analysis ? (
            <ReferenceForm
              defaultUrl={project.reference_url ?? ''}
              disabled={busy}
              onAnalyze={(url) =>
                run('reference', () => api.analyzeReference(project.id, url))
              }
              loading={busyAction === 'reference'}
            />
          ) : (
            <p className="text-sm text-muted leading-relaxed">{project.reference_analysis.summary}</p>
          )}
        </StepCard>

        {/* 2. Guion */}
        <StepCard number={2} title="Guion" disabled={!project.reference_analysis && !!project.reference_url}>
          {!project.script ? (
            <button
              onClick={() => run('script', () => api.generateScript(project.id))}
              disabled={busyAction === 'script'}
              className="bg-signal text-ink font-medium text-sm px-3 py-1.5 rounded-md disabled:opacity-40 hover:brightness-110 transition"
            >
              {busyAction === 'script' ? 'Generando guion...' : 'Generar guion'}
            </button>
          ) : (
            <ScriptEditor
              script={project.script}
              onSave={(script) => api.updateScript(project.id, script).then(() => refresh()).then(() => {})}
              onRegenerate={() => run('script', () => api.generateScript(project.id))}
              regenerating={busyAction === 'script'}
            />
          )}
        </StepCard>

        {/* 3. Voz y tiempos */}
        <StepCard number={3} title="Voz y tiempos" disabled={!project.script}>
          <VoicePicker
            voices={voices}
            selected={project.voice_selection?.voice_id ?? null}
            onSelect={(voiceId) =>
              run('voice', () => api.setVoice(project.id, voiceId, 1.0, 'es'))
            }
          />
        </StepCard>

        {/* 4. Plan visual */}
        <StepCard number={4} title="Plan visual" disabled={!project.script}>
          {!project.storyboard ? (
            <button
              onClick={() => run('storyboard', () => api.generateStoryboard(project.id))}
              disabled={busyAction === 'storyboard'}
              className="bg-signal text-ink font-medium text-sm px-3 py-1.5 rounded-md disabled:opacity-40 hover:brightness-110 transition"
            >
              {busyAction === 'storyboard' ? 'Generando plan visual...' : 'Generar plan visual'}
            </button>
          ) : (
            <>
              <p className="text-sm text-muted mb-3">
                {project.storyboard.length} planos generados. Revisa el estilo visual y los
                personajes clave antes de generar el material — así todas las imágenes
                mantienen la misma estética y los mismos rostros/lugares.
              </p>
              <VisualStyleGuidePanel
                guide={project.visual_style_guide}
                regenerating={busyAction === 'style-guide'}
                onSave={(guide) =>
                  run('style-guide', () => api.updateStyleGuide(project.id, guide))
                }
                onRegenerate={() => run('style-guide', () => api.analyzeStyle(project.id))}
              />
            </>
          )}
        </StepCard>

        {/* 5. Abastecimiento */}
        <StepCard
          number={5}
          title="Abastecimiento"
          disabled={!project.storyboard}
          meta={assetsTotal > 0 ? `${assetsReady}/${assetsTotal} listos${assetsError ? ` · ${assetsError} con error` : ''}` : undefined}
        >
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div className="flex flex-wrap gap-2">
              {GRAPHIC_STYLES.map((style) => (
                <button
                  key={style.id}
                  onClick={() => api.updateProject(project.id, { graphics_style: style.id }).then(refresh)}
                  className={`flex items-center gap-1.5 font-mono text-xs px-2.5 py-1 rounded-full border transition ${
                    project.graphics_style === style.id
                      ? 'border-signal text-signal'
                      : 'border-line text-muted hover:border-signal-dim'
                  }`}
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
                  {style.label}
                </button>
              ))}
            </div>

            {!allProvisioned && (
              <button
                onClick={() => run('provision', () => api.provision(project.id))}
                disabled={busyAction === 'provision' || busy}
                className="bg-signal text-ink font-medium text-sm px-3 py-1.5 rounded-md disabled:opacity-40 hover:brightness-110 transition"
              >
                {busyAction === 'provision' || project.status === 'provisioning'
                  ? 'Consiguiendo material...'
                  : 'Conseguir material'}
              </button>
            )}
          </div>

          <StoryboardGrid storyboard={project.storyboard ?? []} assets={assets} onRetried={refresh} />
        </StepCard>

        {/* 6. Montaje */}
        <StepCard number={6} title="Montaje" last disabled={!allProvisioned}>
          {project.final_video_url ? (
            <div className="border border-signal-dim rounded-md p-4 bg-panel-raised">
              <p className="font-mono text-xs text-signal mb-2">vídeo listo</p>
              <a
                href={project.final_video_url}
                className="text-sm underline break-all text-paper hover:text-signal"
              >
                {project.final_video_url}
              </a>
            </div>
          ) : (
            <button
              onClick={() => run('render', () => api.render(project.id))}
              disabled={busyAction === 'render' || !allProvisioned}
              className="bg-signal text-ink font-medium text-sm px-3 py-1.5 rounded-md disabled:opacity-40 hover:brightness-110 transition"
            >
              {busyAction === 'render' ? 'Montando...' : 'Montar y exportar'}
            </button>
          )}
        </StepCard>
      </div>
    </div>
  )
}

function ReferenceForm({
  defaultUrl,
  disabled,
  loading,
  onAnalyze,
}: {
  defaultUrl: string
  disabled: boolean
  loading: boolean
  onAnalyze: (url: string) => void
}) {
  const [url, setUrl] = useState(defaultUrl)
  return (
    <div className="flex gap-2">
      <input
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="youtube.com/watch?v=... (opcional)"
        className="flex-1 bg-panel-raised border border-line rounded-md px-3 py-2 text-sm outline-none focus:border-signal-dim"
      />
      <button
        onClick={() => onAnalyze(url)}
        disabled={disabled || loading || !url.trim()}
        className="bg-signal text-ink font-medium text-sm px-3 py-2 rounded-md disabled:opacity-40 hover:brightness-110 transition whitespace-nowrap"
      >
        {loading ? 'Analizando...' : 'Analizar'}
      </button>
    </div>
  )
}

function VoicePicker({
  voices,
  selected,
  onSelect,
}: {
  voices: Voice[]
  selected: string | null
  onSelect: (voiceId: string) => void
}) {
  if (voices.length === 0) return <p className="text-sm text-muted">Cargando voces...</p>
  return (
    <div className="grid sm:grid-cols-2 gap-2">
      {voices.map((v) => (
        <button
          key={v.id}
          onClick={() => onSelect(v.id)}
          className={`text-left border rounded-md px-3 py-2 transition ${
            selected === v.id ? 'border-signal' : 'border-line hover:border-signal-dim'
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">{v.name}</span>
            <span className="font-mono text-[11px] text-muted">{v.tone}</span>
          </div>
          <span className="font-mono text-[11px] text-muted">{v.provider} · {v.language}</span>
        </button>
      ))}
    </div>
  )
}
