// Mirrors app/api/schemas.py and app/domain/models.py.

export type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'
export type UploadStatus = 'PENDING' | 'UPLOADING' | 'COMPLETED' | 'FAILED'

export const PIPELINE_STEPS = [
  'narration',
  'segment',
  'tts',
  'transcription',
  'authoring',
  'image_gen',
  'alignment',
  // Only runs for subject 'user-guide' (transcodes the uploaded GIF into one
  // clip per scene); see StepProgress, which filters it out for every other
  // subject. Runs after alignment because it paces each scene's clip to that
  // scene's own aligned duration.
  'screencast',
  'compose',
  'layout_gate',
  'render',
] as const
export type PipelineStep = (typeof PIPELINE_STEPS)[number]

export type Subject = 'lab-management' | 'tech' | 'user-guide'
export type Orientation = 'vertical' | 'horizontal'
export const SUBJECTS: Subject[] = ['tech', 'lab-management', 'user-guide']
export const SUBJECT_LABELS: Record<Subject, string> = {
  'lab-management': 'Laboratory Management (ISO/IEC 17025)',
  tech: 'Tech',
  'user-guide': 'Software User Guide (screen recording)',
}

// 'topic' → LLM writes the narration; 'script' → user supplies it verbatim
// (the narration pipeline step is skipped).
export type InputMode = 'topic' | 'script'

// Mirrors SUPPORTED_LANGUAGES in app/languages.py.
export type Language = 'en' | 'vi'
export const LANGUAGES: { value: Language; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'vi', label: 'Tiếng Việt' },
]

export interface LoginResponse {
  token: string
  token_type: string
  expires_in: number
}

export interface CreateVideoRequest {
  input_mode: InputMode
  query?: string
  script?: string
  subject: Subject
  orientation: Orientation
  language: Language
}

export interface CreateVideoResponse {
  id: string
  input_mode: InputMode
  subject: string
  orientation: string
  language: string
  status: JobStatus
}

export interface JobSummary {
  id: string
  input_mode: InputMode
  query: string
  subject: string
  orientation: string
  language: string
  status: JobStatus
  current_step: PipelineStep | null
  created_at: string
}

export interface JobDetail extends JobSummary {
  error_message: string | null
  video_path: string | null
  updated_at: string
  artifacts: string[]
}

// meta.json sidecar (compose.build_meta) — the YouTube upload defaults.
export interface JobMeta {
  id?: string
  name?: string
  description?: string
  hashtags?: string[]
  tags?: string[]
  createdAt?: string
}

export interface CreateYouTubeUploadRequest {
  access_token: string
  title?: string
  description?: string
  tags?: string[]
  hashtags?: string[]
  privacy_status?: 'public' | 'unlisted' | 'private'
  category_id?: string
  playlist_id?: string
}

export interface CreateYouTubeUploadResponse {
  upload_id: string
  job_id: string
  status: UploadStatus
}

export interface YouTubeUploadDetail {
  id: string
  job_id: string
  status: UploadStatus
  title: string
  description: string
  tags: string[]
  privacy_status: string
  category_id: string
  playlist_id: string | null
  bytes_total: number
  bytes_sent: number
  video_id: string | null
  video_url: string | null
  playlist_added: boolean | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

// Router-enforced caps (settings.max_query_length / max_script_length_*).
// Topic is capped at 300 for both video types; script caps differ by orientation.
export const MAX_QUERY_LENGTH = 300
export const MAX_SCRIPT_LENGTH: Record<Orientation, number> = {
  vertical: 1200,
  horizontal: 9000,
}

// Mirrors settings.max_docx_bytes / max_gif_bytes (app/config.py).
export const MAX_DOCX_BYTES = 10 * 1024 * 1024
export const MAX_GIF_BYTES = 60 * 1024 * 1024

// document: a .docx guide — one Heading-styled section per step, each naming
// its own GIF in a caption line. files: those step GIFs, in ANY order — the
// server matches each to its step by filename, not upload order.
export interface CreateScreencastVideoRequest {
  document: File
  files: File[]
  language: Language
}
