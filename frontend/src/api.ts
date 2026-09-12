import type {
  Asset,
  Project,
  ProjectFormat,
  ProjectStatus,
  ProjectSummary,
  ScriptBlock,
  StoryboardItem,
  VisualStyleGuide,
  Voice,
} from './types'

const BASE = '/api/v1'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      // sin cuerpo JSON
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  listProjects: () => request<ProjectSummary[]>('/projects'),

  createProject: (payload: {
    title: string
    description: string
    format: ProjectFormat
    target_duration_seconds: number
    reference_url?: string
  }) => request<Project>('/projects', { method: 'POST', body: JSON.stringify(payload) }),

  getProject: (id: string) => request<Project>(`/projects/${id}`),

  updateProject: (id: string, payload: Partial<Project>) =>
    request<Project>(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),

  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: 'DELETE' }),

  listAssets: (id: string) => request<Asset[]>(`/projects/${id}/assets`),

  analyzeReference: (id: string, reference_url: string) =>
    request<{ job_id: string }>(`/projects/${id}/analyze-reference`, {
      method: 'POST',
      body: JSON.stringify({ reference_url }),
    }),

  generateScript: (id: string) =>
    request<{ job_id: string }>(`/projects/${id}/generate-script`, { method: 'POST' }),

  updateScript: (id: string, script: ScriptBlock[]) =>
    request<Project>(`/projects/${id}/script`, { method: 'PUT', body: JSON.stringify({ script }) }),

  generateStoryboard: (id: string) =>
    request<{ job_id: string }>(`/projects/${id}/generate-storyboard`, { method: 'POST' }),

  updateStoryboard: (id: string, storyboard: StoryboardItem[]) =>
    request<Project>(`/projects/${id}/storyboard`, {
      method: 'PUT',
      body: JSON.stringify({ storyboard }),
    }),

  updateStyleGuide: (id: string, guide: VisualStyleGuide) =>
    request<Project>(`/projects/${id}/style-guide`, { method: 'PUT', body: JSON.stringify(guide) }),

  analyzeStyle: (id: string) =>
    request<Project>(`/projects/${id}/analyze-style`, { method: 'POST' }),

  setVoice: (id: string, voice_id: string, speed: number, language: string) =>
    request<Project>(`/projects/${id}/voice`, {
      method: 'PUT',
      body: JSON.stringify({ voice_id, speed, language }),
    }),

  provision: (id: string) =>
    request<{ job_id: string }>(`/projects/${id}/provision`, { method: 'POST' }),

  retryAsset: (assetId: string) =>
    request<{ job_id: string }>(`/projects/assets/${assetId}/retry`, { method: 'POST' }),

  render: (id: string) => request<{ job_id: string }>(`/projects/${id}/render`, { method: 'POST' }),

  getStatus: (id: string) => request<ProjectStatus>(`/projects/${id}/status`),

  listVoices: () => request<Voice[]>('/voices'),
}
