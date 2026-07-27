import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, createVideo, uploadScreencastVideo } from '../api/client'
import {
  LANGUAGES,
  MAX_DOCX_BYTES,
  MAX_GIF_BYTES,
  MAX_QUERY_LENGTH,
  MAX_SCRIPT_LENGTH,
  SUBJECTS,
  SUBJECT_LABELS,
  type InputMode,
  type Language,
  type Orientation,
  type Subject,
} from '../api/types'

// The API has no short/long field — vertical is the short single-pass flow
// (45-90s) and horizontal the long-form sectioned one (5-10 min).
const VIDEO_TYPES: { label: string; hint: string; orientation: Orientation }[] = [
  { label: 'Short', hint: 'vertical · 45–90s', orientation: 'vertical' },
  { label: 'Long', hint: 'horizontal · 5–10 min', orientation: 'horizontal' },
]

const INPUT_MODES: { label: string; hint: string; mode: InputMode }[] = [
  { label: 'Topic', hint: 'AI writes the script', mode: 'topic' },
  { label: 'Script', hint: 'use your own narration', mode: 'script' },
]

function formatMb(bytes: number): string {
  return (bytes / (1024 * 1024)).toFixed(1)
}

export default function CreateVideoForm() {
  const navigate = useNavigate()
  const [inputMode, setInputMode] = useState<InputMode>('topic')
  const [subject, setSubject] = useState<Subject>('tech')
  const [orientation, setOrientation] = useState<Orientation>('vertical')
  const [language, setLanguage] = useState<Language>('en')
  const [query, setQuery] = useState('')
  const [script, setScript] = useState('')
  const [docxFile, setDocxFile] = useState<File | null>(null)
  const [gifFiles, setGifFiles] = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // user-guide has no topic/script choice (narration comes from the uploaded
  // guide) and no vertical option (screen recordings are landscape) — both
  // toggles are hidden for it below.
  const isUserGuide = subject === 'user-guide'

  const scriptCap = MAX_SCRIPT_LENGTH[orientation]
  const activeCap = inputMode === 'topic' ? MAX_QUERY_LENGTH : scriptCap
  const active = inputMode === 'topic' ? query : script
  const isOverLimit = !isUserGuide && active.length > activeCap
  const isNearLimit = !isOverLimit && active.length >= activeCap * 0.9

  const isDocxOverLimit = docxFile != null && docxFile.size > MAX_DOCX_BYTES
  const oversizedGifs = gifFiles.filter((f) => f.size > MAX_GIF_BYTES)
  const canSubmit = isUserGuide
    ? docxFile != null &&
      gifFiles.length > 0 &&
      !isDocxOverLimit &&
      oversizedGifs.length === 0 &&
      !submitting
    : active.trim().length > 0 && !isOverLimit && !submitting

  // Long→Short shrinks the script cap; trim so the counter and submit stay valid.
  const changeOrientation = (next: Orientation) => {
    setOrientation(next)
    setScript((s) => s.slice(0, MAX_SCRIPT_LENGTH[next]))
  }

  // Lab Management content is authored in Vietnamese, so picking it auto-selects
  // the Vietnamese language (the user can still change language afterwards).
  // user-guide forces horizontal — screen recordings are landscape.
  const changeSubject = (next: Subject) => {
    setSubject(next)
    if (next === 'lab-management') setLanguage('vi')
    if (next === 'user-guide') changeOrientation('horizontal')
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const job = isUserGuide
        ? await uploadScreencastVideo({ document: docxFile!, files: gifFiles, language })
        : await createVideo(
            inputMode === 'topic'
              ? { input_mode: 'topic', query: query.trim(), subject, orientation, language }
              : { input_mode: 'script', script: script.trim(), subject, orientation, language },
          )
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      // 400 = the subject guard rejected an off-topic query (topic mode), a
      // step/GIF filename mismatch (user-guide), or an over-cap/empty input.
      setError(err instanceof ApiError ? err.message : 'Request failed — is the backend running?')
      setSubmitting(false)
    }
  }

  return (
    <form className="card create-form" onSubmit={submit}>
      <h2>Create a video</h2>

      {!isUserGuide && (
        <div className="field">
          <span className="field-label">Input</span>
          <div className="toggle-group">
            {INPUT_MODES.map((m) => (
              <button
                key={m.mode}
                type="button"
                className={`toggle ${inputMode === m.mode ? 'active' : ''}`}
                onClick={() => setInputMode(m.mode)}
                title={m.hint}
              >
                {m.label}
                <small>{m.hint}</small>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="form-row">
        <label className="field">
          <span className="field-label">Subject</span>
          <select value={subject} onChange={(e) => changeSubject(e.target.value as Subject)}>
            {SUBJECTS.map((s) => (
              <option key={s} value={s}>
                {SUBJECT_LABELS[s]}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field-label">Language</span>
          <select value={language} onChange={(e) => setLanguage(e.target.value as Language)}>
            {LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </label>

        {!isUserGuide && (
          <div className="field">
            <span className="field-label">Video type</span>
            <div className="toggle-group">
              {VIDEO_TYPES.map((t) => (
                <button
                  key={t.orientation}
                  type="button"
                  className={`toggle ${orientation === t.orientation ? 'active' : ''}`}
                  onClick={() => changeOrientation(t.orientation)}
                  title={t.hint}
                >
                  {t.label}
                  <small>{t.hint}</small>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {isUserGuide ? (
        <>
          <label className="field">
            <span className="field-label">User guide (.docx)</span>
            <input
              type="file"
              accept=".docx"
              onChange={(e) => setDocxFile(e.target.files?.[0] ?? null)}
            />
            <span className="field-hint">
              One Heading-styled section per step; each step names its own GIF in a caption
              line, e.g. "(my-step.gif)". Headings and captions are never spoken.
            </span>
            {docxFile && (
              <span className={`char-counter ${isDocxOverLimit ? 'danger' : ''}`}>
                {formatMb(docxFile.size)} MB / {formatMb(MAX_DOCX_BYTES)} MB
              </span>
            )}
          </label>

          <label className="field">
            <span className="field-label">Step screen recordings (GIFs)</span>
            <input
              type="file"
              accept="image/gif"
              multiple
              onChange={(e) => setGifFiles(Array.from(e.target.files ?? []))}
            />
            <span className="field-hint">
              Any order — each is matched to its step by the filename named in the guide.
            </span>
            {gifFiles.length > 0 && (
              <ul className="file-list">
                {gifFiles.map((f, i) => (
                  <li
                    key={`${f.name}-${i}`}
                    className={f.size > MAX_GIF_BYTES ? 'danger' : undefined}
                  >
                    {f.name} ({formatMb(f.size)} MB)
                  </li>
                ))}
              </ul>
            )}
          </label>
        </>
      ) : inputMode === 'topic' ? (
        <label className="field">
          <span className="field-label">Topic</span>
          <textarea
            value={query}
            rows={3}
            placeholder={`e.g. How to "FIX" your AI from hallucination`}
            onChange={(e) => setQuery(e.target.value)}
            aria-invalid={isOverLimit}
          />
          <span className={`char-counter ${isOverLimit ? 'danger' : isNearLimit ? 'warning' : ''}`}>
            {query.length}/{MAX_QUERY_LENGTH}
          </span>
        </label>
      ) : (
        <label className="field">
          <span className="field-label">Script</span>
          <textarea
            value={script}
            rows={10}
            placeholder="Paste your narration — headings, bullets and markdown are cleaned up automatically."
            onChange={(e) => setScript(e.target.value)}
            aria-invalid={isOverLimit}
          />
          <span className={`char-counter ${isOverLimit ? 'danger' : isNearLimit ? 'warning' : ''}`}>
            {script.length}/{scriptCap}
          </span>
        </label>
      )}

      {isOverLimit && (
        <p className="warning-text">
          {active.length - activeCap} characters over the limit — shorten your{' '}
          {inputMode === 'topic' ? 'topic' : 'script'} to enable Generate video.
        </p>
      )}

      {isDocxOverLimit && (
        <p className="warning-text">
          Guide exceeds the {formatMb(MAX_DOCX_BYTES)} MB limit — choose a smaller file to
          enable Generate video.
        </p>
      )}

      {oversizedGifs.length > 0 && (
        <p className="warning-text">
          {oversizedGifs.length} GIF(s) exceed the {formatMb(MAX_GIF_BYTES)} MB limit —
          replace them to enable Generate video.
        </p>
      )}

      {error && <p className="error-text">{error}</p>}

      <button type="submit" className="primary" disabled={!canSubmit}>
        {submitting ? 'Creating…' : 'Generate video'}
      </button>
    </form>
  )
}
