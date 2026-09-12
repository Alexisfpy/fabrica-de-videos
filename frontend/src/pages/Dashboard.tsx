import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { ProjectFormat, ProjectSummary } from '../types'
import StatusBadge from '../components/StatusBadge'

const FORMAT_LABEL: Record<ProjectFormat, string> = {
  horizontal_16_9: '16:9 · 20 min',
  shortform_9_16: '9:16 · Shortform',
  longform_9_16: '9:16 · Longform',
}

function formatDuration(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function Dashboard() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [showForm, setShowForm] = useState(false)
  const navigate = useNavigate()

  const load = () => {
    api.listProjects().then(setProjects)
  }

  useEffect(load, [])

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold">Tus vídeos</h1>
          <p className="text-muted text-sm mt-1">Dices el tema. Sale el vídeo montado.</p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="bg-signal text-ink font-medium px-4 py-2 rounded-md hover:brightness-110 transition"
        >
          {showForm ? 'Cancelar' : '+ Nuevo vídeo'}
        </button>
      </div>

      {showForm && (
        <NewProjectForm
          onCreated={(id) => navigate(`/projects/${id}`)}
        />
      )}

      {projects === null && <p className="text-muted">Cargando...</p>}

      {projects !== null && projects.length === 0 && !showForm && (
        <div className="border border-dashed border-line rounded-lg py-16 text-center text-muted">
          Todavía no tienes ningún vídeo. Crea el primero para empezar.
        </div>
      )}

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-6">
        {projects?.map((p) => (
          <button
            key={p.id}
            onClick={() => navigate(`/projects/${p.id}`)}
            className="text-left bg-panel border border-line rounded-lg p-4 hover:border-signal-dim transition flex flex-col gap-3"
          >
            <div className="flex items-start justify-between gap-2">
              <h3 className="font-medium leading-snug line-clamp-2">{p.title}</h3>
              <StatusBadge status={p.status} />
            </div>
            <div className="font-mono text-xs text-muted flex items-center gap-3">
              <span>{FORMAT_LABEL[p.format]}</span>
              <span>·</span>
              <span>
                {p.actual_duration_seconds
                  ? formatDuration(p.actual_duration_seconds)
                  : formatDuration(p.target_duration_seconds)}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function NewProjectForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [format, setFormat] = useState<ProjectFormat>('horizontal_16_9')
  const [minutes, setMinutes] = useState(7)
  const [referenceUrl, setReferenceUrl] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    setSubmitting(true)
    try {
      const project = await api.createProject({
        title,
        description,
        format,
        target_duration_seconds: minutes * 60,
        reference_url: referenceUrl || undefined,
      })
      onCreated(project.id)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-panel border border-line rounded-lg p-5 mb-8 flex flex-col gap-4"
    >
      <div>
        <label className="text-xs font-mono text-muted block mb-1">TÍTULO DEL VÍDEO</label>
        <input
          autoFocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder='Ej. "Todas las consolas de Nintendo, explicadas en 7 minutos"'
          className="w-full bg-panel-raised border border-line rounded-md px-3 py-2 outline-none focus:border-signal-dim"
        />
      </div>
      <div>
        <label className="text-xs font-mono text-muted block mb-1">DESCRIPCIÓN / ÁNGULO</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Cuanto más concreto seas aquí, mejor sale el guion — di el ángulo, no solo el tema."
          rows={2}
          className="w-full bg-panel-raised border border-line rounded-md px-3 py-2 outline-none focus:border-signal-dim resize-none"
        />
      </div>
      <div className="grid sm:grid-cols-3 gap-4">
        <div>
          <label className="text-xs font-mono text-muted block mb-1">FORMATO</label>
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value as ProjectFormat)}
            className="w-full bg-panel-raised border border-line rounded-md px-3 py-2 outline-none focus:border-signal-dim"
          >
            <option value="horizontal_16_9">16:9 (20 min)</option>
            <option value="shortform_9_16">Shortform 9:16</option>
            <option value="longform_9_16">Longform 9:16</option>
          </select>
        </div>
        <div>
          <label className="text-xs font-mono text-muted block mb-1">DURACIÓN OBJETIVO</label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              max={20}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value))}
              className="w-20 bg-panel-raised border border-line rounded-md px-3 py-2 outline-none focus:border-signal-dim"
            />
            <span className="text-muted text-sm">min</span>
          </div>
        </div>
        <div>
          <label className="text-xs font-mono text-muted block mb-1">REFERENCIA (opcional)</label>
          <input
            value={referenceUrl}
            onChange={(e) => setReferenceUrl(e.target.value)}
            placeholder="youtube.com/watch?v=..."
            className="w-full bg-panel-raised border border-line rounded-md px-3 py-2 outline-none focus:border-signal-dim"
          />
        </div>
      </div>
      <div className="flex justify-end">
        <button
          type="submit"
          disabled={submitting || !title.trim()}
          className="bg-signal text-ink font-medium px-5 py-2 rounded-md disabled:opacity-40 hover:brightness-110 transition"
        >
          {submitting ? 'Creando...' : 'Crear proyecto'}
        </button>
      </div>
    </form>
  )
}
