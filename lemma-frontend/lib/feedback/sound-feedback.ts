// Lightweight, dependency-free Web Audio synthesis for interaction feedback.
// Previously delegated to the `cuelume` npm package, which was a sole-maintainer
// package (BP-SUP-001) — reimplemented here against the native Web Audio API
// so the frontend does not pull a single point of supply-chain failure into
// every gogett-webrnds.com tenant build.

export type SoundFeedbackPreference = "important" | "all" | "off";

export type SoundFeedbackEvent =
  | "work-start"
  | "work-complete"
  | "work-fail"
  | "work-waiting"
  | "toggle-change"
  | "action-success"
  | "agent-open"
  | "load-failure";

type SoundFeedbackLevel = Exclude<SoundFeedbackPreference, "off">;

export type SoundName = "loading" | "bloom" | "error" | "chime" | "toggle" | "release" | "ready";

export type SoundFeedbackEventConfig = {
  sound: SoundName;
  level: SoundFeedbackLevel;
  minGapMs: number;
};

export const SOUND_FEEDBACK_STORAGE_KEY = "lemma:sound-feedback";
export const DEFAULT_SOUND_FEEDBACK_PREFERENCE: SoundFeedbackPreference = "important";

export const SOUND_FEEDBACK_EVENTS: Record<SoundFeedbackEvent, SoundFeedbackEventConfig> = {
  "work-start": { sound: "loading", level: "important", minGapMs: 450 },
  "work-complete": { sound: "bloom", level: "important", minGapMs: 750 },
  "work-fail": { sound: "error", level: "important", minGapMs: 600 },
  "work-waiting": { sound: "chime", level: "important", minGapMs: 900 },
  "toggle-change": { sound: "toggle", level: "all", minGapMs: 100 },
  "action-success": { sound: "release", level: "all", minGapMs: 180 },
  "agent-open": { sound: "ready", level: "important", minGapMs: 600 },
  "load-failure": { sound: "error", level: "important", minGapMs: 600 },
};

const SOURCE_STOP_PADDING = 0.05;
const CLEANUP_MARGIN = 0.05;
const INAUDIBLE_GAIN = 0.0001;
let sharedContext: AudioContext | null = null;
let audioEnabled = true;

type ToneLayer = {
  kind: "tone";
  waveform: OscillatorType;
  frequency: number;
  detuneCents?: number;
  glideTo?: number;
  glideSeconds?: number;
  offset: number;
  attack: number;
  decay: number;
  peak: number;
};

type NoiseLayer = {
  kind: "noise";
  filterType: BiquadFilterType;
  filterFrequency: number;
  filterQ?: number;
  offset: number;
  attack: number;
  decay: number;
  peak: number;
};

type Layer = ToneLayer | NoiseLayer;

type Recipe = {
  masterGain: number;
  layers: Layer[];
};

const RECIPES: Record<SoundName, Recipe> = {
  chime: {
    masterGain: 0.5,
    layers: [
      {
        kind: "tone",
        waveform: "sine",
        frequency: 1046.5,
        offset: 0,
        attack: 0.006,
        decay: 0.22,
        peak: 0.09,
      },
      {
        kind: "tone",
        waveform: "sine",
        frequency: 1568,
        offset: 0.09,
        attack: 0.006,
        decay: 0.26,
        peak: 0.08,
      },
    ],
  },
  bloom: {
    masterGain: 0.5,
    layers: [
      {
        kind: "tone",
        waveform: "sine",
        frequency: 528,
        offset: 0,
        attack: 0.06,
        decay: 0.32,
        peak: 0.06,
      },
      {
        kind: "tone",
        waveform: "sine",
        frequency: 528,
        detuneCents: 12,
        offset: 0,
        attack: 0.06,
        decay: 0.34,
        peak: 0.05,
      },
    ],
  },
  error: {
    masterGain: 0.42,
    layers: [
      {
        kind: "noise",
        filterType: "bandpass",
        filterFrequency: 850,
        filterQ: 1.1,
        offset: 0,
        attack: 0.001,
        decay: 0.035,
        peak: 0.13,
      },
      {
        kind: "tone",
        waveform: "triangle",
        frequency: 440,
        offset: 0.025,
        attack: 0.004,
        decay: 0.09,
        peak: 0.045,
      },
      {
        kind: "tone",
        waveform: "triangle",
        frequency: 349.23,
        offset: 0.1,
        attack: 0.004,
        decay: 0.14,
        peak: 0.04,
      },
    ],
  },
  loading: {
    masterGain: 0.42,
    layers: [
      {
        kind: "noise",
        filterType: "lowpass",
        filterFrequency: 1400,
        filterQ: 0.6,
        offset: 0,
        attack: 0.035,
        decay: 0.14,
        peak: 0.035,
      },
      {
        kind: "tone",
        waveform: "sine",
        frequency: 420,
        glideTo: 630,
        glideSeconds: 0.18,
        offset: 0,
        attack: 0.025,
        decay: 0.18,
        peak: 0.05,
      },
    ],
  },
  ready: {
    masterGain: 0.45,
    layers: [
      {
        kind: "noise",
        filterType: "bandpass",
        filterFrequency: 3200,
        filterQ: 1.7,
        offset: 0,
        attack: 0.001,
        decay: 0.018,
        peak: 0.1,
      },
      {
        kind: "tone",
        waveform: "sine",
        frequency: 659.25,
        offset: 0.025,
        attack: 0.012,
        decay: 0.2,
        peak: 0.05,
      },
      {
        kind: "tone",
        waveform: "sine",
        frequency: 987.77,
        offset: 0.025,
        attack: 0.012,
        decay: 0.22,
        peak: 0.035,
      },
    ],
  },
  release: {
    masterGain: 0.4,
    layers: [
      {
        kind: "noise",
        filterType: "bandpass",
        filterFrequency: 4600,
        filterQ: 1.8,
        offset: 0,
        attack: 0.001,
        decay: 0.016,
        peak: 0.12,
      },
      {
        kind: "tone",
        waveform: "sine",
        frequency: 3200,
        offset: 0.006,
        attack: 0.001,
        decay: 0.05,
        peak: 0.02,
      },
    ],
  },
  toggle: {
    masterGain: 0.4,
    layers: [
      {
        kind: "noise",
        filterType: "bandpass",
        filterFrequency: 2200,
        filterQ: 1.6,
        offset: 0,
        attack: 0.001,
        decay: 0.016,
        peak: 0.12,
      },
      {
        kind: "noise",
        filterType: "bandpass",
        filterFrequency: 3800,
        filterQ: 1.6,
        offset: 0.024,
        attack: 0.001,
        decay: 0.02,
        peak: 0.1,
      },
    ],
  },
};

function getAudioContext(): AudioContext | null {
  if (sharedContext) return sharedContext;
  if (typeof window === "undefined") return null;
  type WindowWithWebkit = Window & {
    AudioContext?: typeof AudioContext;
    webkitAudioContext?: typeof AudioContext;
  };
  const win = window as WindowWithWebkit;
  const Ctor: typeof AudioContext | undefined = win.AudioContext ?? win.webkitAudioContext;
  if (!Ctor) return null;
  try {
    sharedContext = new Ctor();
  } catch {
    return null;
  }
  return sharedContext;
}

function renderTone(
  context: AudioContext,
  destination: AudioNode,
  layer: ToneLayer,
  startTime: number,
): { stopAt: number } {
  const oscillator = context.createOscillator();
  oscillator.type = layer.waveform;
  oscillator.frequency.setValueAtTime(layer.frequency, startTime);
  if (typeof layer.detuneCents === "number") {
    oscillator.detune.setValueAtTime(layer.detuneCents, startTime);
  }
  if (typeof layer.glideTo === "number") {
    const glideSeconds = layer.glideSeconds ?? layer.attack + layer.decay;
    oscillator.frequency.exponentialRampToValueAtTime(layer.glideTo, startTime + glideSeconds);
  }
  const gain = context.createGain();
  gain.gain.setValueAtTime(INAUDIBLE_GAIN, startTime);
  gain.gain.exponentialRampToValueAtTime(layer.peak, startTime + layer.attack);
  gain.gain.exponentialRampToValueAtTime(INAUDIBLE_GAIN, startTime + layer.attack + layer.decay);
  oscillator.connect(gain);
  gain.connect(destination);
  oscillator.start(startTime);
  const stopAt = startTime + layer.attack + layer.decay + SOURCE_STOP_PADDING;
  oscillator.stop(stopAt);
  return { stopAt };
}

function renderNoise(
  context: AudioContext,
  destination: AudioNode,
  layer: NoiseLayer,
  startTime: number,
): { stopAt: number } {
  const duration = layer.attack + layer.decay + SOURCE_STOP_PADDING;
  const length = Math.max(1, Math.floor(duration * context.sampleRate));
  const buffer = context.createBuffer(1, length, context.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < length; i++) {
    data[i] = 2 * Math.random() - 1;
  }
  const source = context.createBufferSource();
  source.buffer = buffer;
  const filter = context.createBiquadFilter();
  filter.type = layer.filterType;
  filter.frequency.setValueAtTime(layer.filterFrequency, startTime);
  if (typeof layer.filterQ === "number") {
    filter.Q.setValueAtTime(layer.filterQ, startTime);
  }
  const gain = context.createGain();
  gain.gain.setValueAtTime(INAUDIBLE_GAIN, startTime);
  gain.gain.exponentialRampToValueAtTime(layer.peak, startTime + layer.attack);
  gain.gain.exponentialRampToValueAtTime(INAUDIBLE_GAIN, startTime + layer.attack + layer.decay);
  source.connect(filter);
  filter.connect(gain);
  gain.connect(destination);
  source.start(startTime);
  const stopAt = startTime + duration;
  source.stop(stopAt);
  return { stopAt };
}

function recipeDuration(recipe: Recipe): number {
  return recipe.layers.reduce(
    (max, layer) => Math.max(max, layer.offset + layer.attack + layer.decay + SOURCE_STOP_PADDING),
    0,
  );
}

function renderRecipe(context: AudioContext, recipe: Recipe): void {
  const now = context.currentTime;
  const master = context.createGain();
  master.gain.setValueAtTime(recipe.masterGain, now);
  master.connect(context.destination);
  let latestStop = now;
  for (const layer of recipe.layers) {
    const startTime = now + layer.offset;
    const { stopAt } =
      layer.kind === "tone"
        ? renderTone(context, master, layer, startTime)
        : renderNoise(context, master, layer, startTime);
    if (stopAt > latestStop) latestStop = stopAt;
  }
  const cleanupAfterMs = (latestStop - now + CLEANUP_MARGIN) * 1000;
  setTimeout(() => {
    master.disconnect();
  }, cleanupAfterMs);
}

function isSoundName(value: unknown): value is SoundName {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(RECIPES, value);
}

function play(sound: SoundName = "chime") {
  if (!audioEnabled || !isSoundName(sound)) return;
  if (typeof navigator !== "undefined" && navigator.userActivation?.hasBeenActive === false) {
    return;
  }
  const context = getAudioContext();
  if (!context) return;
  const recipe = RECIPES[sound];
  if (context.state === "running") {
    renderRecipe(context, recipe);
    return;
  }
  try {
    void context.resume().then(
      () => {
        if (audioEnabled && context.state === "running") {
          renderRecipe(context, recipe);
        }
      },
      () => {},
    );
  } catch {
    // Some browsers throw synchronously when audio is blocked; treat as a no-op.
  }
}

export function getMaxRecipeDurationSeconds(): number {
  return Math.max(...Object.values(RECIPES).map(recipeDuration));
}

function setEnabled(value: boolean) {
  if (typeof value === "boolean") audioEnabled = value;
}

const preferenceListeners = new Set<() => void>();
const lastPlayedAtByEvent = new Map<SoundFeedbackEvent, number>();
const playedOnceKeys = new Map<string, number>();
let inMemoryPreference: SoundFeedbackPreference | null = null;

function isSoundFeedbackPreference(value: unknown): value is SoundFeedbackPreference {
  return value === "important" || value === "all" || value === "off";
}

function readStoredPreference(): SoundFeedbackPreference {
  if (typeof window === "undefined") return DEFAULT_SOUND_FEEDBACK_PREFERENCE;

  try {
    const stored = window.localStorage.getItem(SOUND_FEEDBACK_STORAGE_KEY);
    return isSoundFeedbackPreference(stored) ? stored : DEFAULT_SOUND_FEEDBACK_PREFERENCE;
  } catch {
    return DEFAULT_SOUND_FEEDBACK_PREFERENCE;
  }
}

export function getSoundFeedbackPreference(): SoundFeedbackPreference {
  if (inMemoryPreference === null) {
    inMemoryPreference = readStoredPreference();
  }
  return inMemoryPreference;
}

export function getServerSoundFeedbackPreference(): SoundFeedbackPreference {
  return DEFAULT_SOUND_FEEDBACK_PREFERENCE;
}

export function setSoundFeedbackPreference(preference: SoundFeedbackPreference) {
  inMemoryPreference = preference;
  setEnabled(preference !== "off");

  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(SOUND_FEEDBACK_STORAGE_KEY, preference);
    } catch {
      // Keep the in-memory preference when storage is unavailable.
    }
  }

  preferenceListeners.forEach((listener) => listener());
}

export function subscribeSoundFeedbackPreference(listener: () => void) {
  preferenceListeners.add(listener);

  const handleStorage = (event: StorageEvent) => {
    if (event.key !== SOUND_FEEDBACK_STORAGE_KEY) return;
    inMemoryPreference = readStoredPreference();
    setEnabled(inMemoryPreference !== "off");
    listener();
  };

  if (typeof window !== "undefined") {
    window.addEventListener("storage", handleStorage);
  }

  return () => {
    preferenceListeners.delete(listener);
    if (typeof window !== "undefined") {
      window.removeEventListener("storage", handleStorage);
    }
  };
}

export function isSoundFeedbackEventEnabled(
  event: SoundFeedbackEvent,
  preference: SoundFeedbackPreference,
) {
  if (preference === "off") return false;
  return preference === "all" || SOUND_FEEDBACK_EVENTS[event].level === "important";
}

export function playSoundFeedback(
  event: SoundFeedbackEvent,
  options: { onceKey?: string; now?: number } = {},
) {
  const preference = getSoundFeedbackPreference();
  if (!isSoundFeedbackEventEnabled(event, preference)) return false;

  const now = options.now ?? Date.now();
  const config = SOUND_FEEDBACK_EVENTS[event];
  const lastPlayedAt = lastPlayedAtByEvent.get(event) ?? 0;
  if (now - lastPlayedAt < config.minGapMs) return false;

  if (options.onceKey) {
    const key = `${event}:${options.onceKey}`;
    if (playedOnceKeys.has(key)) return false;
    playedOnceKeys.set(key, now);

    if (playedOnceKeys.size > 300) {
      const oldestKey = playedOnceKeys.keys().next().value;
      if (oldestKey) playedOnceKeys.delete(oldestKey);
    }
  }

  lastPlayedAtByEvent.set(event, now);

  try {
    play(config.sound);
    return true;
  } catch {
    // Sound is enhancement-only; it must never interrupt the user action.
    return false;
  }
}

export function resetSoundFeedbackForTests() {
  inMemoryPreference = null;
  lastPlayedAtByEvent.clear();
  playedOnceKeys.clear();
}
