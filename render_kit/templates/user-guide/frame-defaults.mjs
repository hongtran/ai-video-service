// Shared source of truth for the user-guide template's scene shape — the set of
// valid frame types, their per-type fields, and structural validation.
// Consumed by populate.js (rendering).
//
// This template is unlike the others: a user-supplied screen recording plays as
// a root-level <video> under the ENTIRE video, and every frame here is a
// TRANSPARENT overlay composited on top of it. There is no `bg` and no
// PLACEHOLDER_IMAGE — a frame that painted its own background would hide the
// recording completely.

export const SHARED = { fg: '#FFFFFF', accent: '#38BDF8', eyebrow: '', headline: '', captions: [] };

// Horizontal only. Screen recordings are landscape; a 9:16 frame either shrinks
// the UI past legibility or crops out the thing being demonstrated.
export const ORIENTATIONS = ['horizontal'];

// Track indices for the root-level screencast <video> elements. Tracks are a
// TEMPORAL concept, not a visual-stacking one (visual layering is CSS z-index)
// — two clips on the same track must not overlap in time, and the blurred
// background copy and the real recording both span [0, totalDuration), so each
// needs its own track or the linter rejects the composition. Scene overlays
// start above both (see generateSceneClips in populate.js).
export const SCREENCAST_BG_TRACK_INDEX = 0;
export const SCREENCAST_TRACK_INDEX = 1;

export const FRAME_DEFAULTS = {
  // The default frame: scrim + karaoke captions + an optional short step title.
  // stepTitle is intentionally optional — a title band over every scene is
  // noise and covers the demo.
  screencast: { ...SHARED, stepTitle: '' },
  // Opening. The recording keeps playing behind the title.
  'title-card': { ...SHARED, subheadline: '' },
  // Closing CTA over a heavier scrim.
  outro: { ...SHARED, accent: '#4ADE80', subheadline: '' },
};

export const VALID_TYPES = Object.keys(FRAME_DEFAULTS);

// Fields that distinguish a type from the shared base (fg/accent/eyebrow/
// headline/captions).
export function typeSpecificFields(type) {
  return Object.keys(FRAME_DEFAULTS[type] ?? {}).filter((k) => !(k in SHARED));
}

// Every {{token}} each frames/*.html actually substitutes, beyond the shared
// base (fg/accent/eyebrow/headline/captions/id/width/height/duration/
// orientation/captionTiming) — the authoritative field list per type.
export const TYPE_CONTENT_FIELDS = {
  screencast: ['stepTitle'],
  'title-card': ['subheadline'],
  outro: ['subheadline'],
};

export const SUPPORTED_ORIENTATIONS = Object.fromEntries(
  VALID_TYPES.map((t) => [t, [...ORIENTATIONS]])
);

// Which of a type's content fields actually break/degrade visibly if left
// empty. `screencast` has none: it is legitimate for a scene to be captions
// only, and forcing a title onto every scene would bury the recording.
export const REQUIRED_CONTENT_FIELDS = {
  screencast: [],
  'title-card': ['headline'],
  outro: ['headline', 'subheadline'],
};

export function requiredContentFields(type) {
  return REQUIRED_CONTENT_FIELDS[type] ?? [];
}

export function validateData(data) {
  const errors = [];
  if (!data.config?.slug) errors.push('config.slug is required');
  if (!data.config?.totalDuration) errors.push('config.totalDuration is required');
  if (data.config?.orientation && !ORIENTATIONS.includes(data.config.orientation))
    errors.push(`config.orientation "${data.config.orientation}" is invalid — must be one of: ${ORIENTATIONS.join(', ')}`);
  // The whole point of this template. Without a screencast every frame is a
  // transparent overlay on black, which renders as captions floating in a void
  // — a failure that otherwise reaches the final mp4 unremarked.
  if (!data.config?.screencast)
    errors.push('config.screencast is required — this template composites every frame over a screen recording');
  if (!Array.isArray(data.scenes) || data.scenes.length === 0)
    errors.push('scenes[] must be a non-empty array');
  for (const scene of data.scenes ?? []) {
    if (!scene.id) errors.push('a scene is missing id');
    if (!VALID_TYPES.includes(scene.type))
      errors.push(`scene "${scene.id}" has invalid type "${scene.type}" — must be one of: ${VALID_TYPES.join(', ')}`);
    if (scene.start === undefined) errors.push(`scene "${scene.id}" missing start`);
    if (!scene.duration) errors.push(`scene "${scene.id}" missing duration`);
    for (const field of requiredContentFields(scene.type ?? '')) {
      const v = scene[field];
      const empty = v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
      if (empty) errors.push(`scene "${scene.id}" (${scene.type}) is missing required field "${field}"`);
    }
  }
  return errors;
}
