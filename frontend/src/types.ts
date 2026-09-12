export type ProjectFormat = 'horizontal_16_9' | 'shortform_9_16' | 'longform_9_16'
export type GraphicsStyle = 'neon' | 'ambar' | 'bloque' | 'cine'

export interface ScriptBlock {
  id: string
  order: number
  text: string
  estimated_seconds: number
}

export interface StoryboardItem {
  id: string
  order: number
  description: string
  shot_type: 'graphic' | 'stock' | 'ai_generated'
  source: 'library' | 'stock' | 'ai_generated'
  duration_seconds: number
}

export interface VisualStyleGuide {
  visual_style: string
  entities: Record<string, string>
}

export interface ReferenceAnalysis {
  summary: string
  avg_shot_seconds: number
  total_shots: number
  structure: { block: string; seconds: number }[]
  source_url: string
}

export interface ProjectSummary {
  id: string
  title: string
  format: ProjectFormat
  status: string
  target_duration_seconds: number
  actual_duration_seconds: number | null
  created_at: string
}

export interface Project extends ProjectSummary {
  user_id: string
  description: string
  reference_url: string | null
  reference_analysis: ReferenceAnalysis | null
  script: ScriptBlock[] | null
  voice_selection: { voice_id: string; speed: number; language: string } | null
  storyboard: StoryboardItem[] | null
  visual_style_guide: VisualStyleGuide | null
  graphics_style: GraphicsStyle
  final_video_url: string | null
  error_message: string | null
}

export interface Asset {
  id: string
  storyboard_item_id: string
  type: string
  source: string
  status: 'pending' | 'ready' | 'error'
  url: string | null
  duration_seconds: number | null
  provider: string | null
  error_message: string | null
}

export interface Voice {
  id: string
  provider: string
  name: string
  gender: string | null
  language: string | null
  tone: string | null
  sample_url: string | null
}

export interface ProjectStatus {
  status: string
  assets_ready: number
  assets_total: number
  assets_error: number
  final_video_url: string | null
  error_message: string | null
}
