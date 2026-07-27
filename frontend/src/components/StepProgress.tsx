import {
  PIPELINE_STEPS,
  type InputMode,
  type JobStatus,
  type PipelineStep,
  type Subject,
} from '../api/types'

const STEP_LABELS: Record<PipelineStep, string> = {
  narration: 'Narration',
  segment: 'Scene split',
  tts: 'Text-to-speech',
  transcription: 'Transcription',
  screencast: 'Screencast',
  authoring: 'Scene authoring',
  image_gen: 'Image generation',
  alignment: 'Alignment',
  compose: 'Compose',
  layout_gate: 'Layout gate',
  render: 'Render',
}

type StepState = 'done' | 'current' | 'failed' | 'pending'

function stateFor(
  steps: readonly PipelineStep[],
  step: PipelineStep,
  current: PipelineStep | null,
  status: JobStatus,
): StepState {
  if (status === 'COMPLETED') return 'done'
  const index = steps.indexOf(step)
  const currentIndex = current ? steps.indexOf(current) : -1
  if (index < currentIndex) return 'done'
  if (index === currentIndex) return status === 'FAILED' ? 'failed' : 'current'
  return 'pending'
}

const ICONS: Record<StepState, string> = {
  done: '✓',
  current: '●',
  failed: '✗',
  pending: '○',
}

export default function StepProgress({
  currentStep,
  status,
  inputMode,
  subject,
}: {
  currentStep: PipelineStep | null
  status: JobStatus
  inputMode: InputMode
  subject: Subject
}) {
  // Script-mode jobs supply the narration, so the NARRATION step never runs.
  // SCREENCAST only runs for subject 'user-guide' — showing it for every
  // other subject would pre-check a step the job never performs, since its
  // index sits before the next step the backend actually reports.
  const steps = PIPELINE_STEPS.filter((s) => {
    if (s === 'narration') return inputMode !== 'script'
    if (s === 'screencast') return subject === 'user-guide'
    return true
  })
  return (
    <ol className="step-progress">
      {steps.map((step) => {
        const state = stateFor(steps, step, currentStep, status)
        return (
          <li key={step} className={`step ${state}`}>
            <span className="step-icon">{ICONS[state]}</span>
            <span className="step-label">{STEP_LABELS[step]}</span>
          </li>
        )
      })}
    </ol>
  )
}
