import { z } from 'zod';

// ============================================
// Topic & Category Types
// ============================================

export const STORY_CATEGORIES = [
  'aita',
  'relationships',
  'cheating',
  'revenge',
  'workplace_drama',
  'entitled_parents',
  'family_drama',
  'confessions',
  'scary_stories',
  'creepy_encounters',
  'wholesome',
  'mysteries',
  'customer_service_drama',
] as const;

export type StoryCategory = (typeof STORY_CATEGORIES)[number];

export const CATEGORY_LABELS: Record<StoryCategory, string> = {
  aita: 'Am I The A**hole',
  relationships: 'Relationship Drama',
  cheating: 'Cheating Stories',
  revenge: 'Revenge Stories',
  workplace_drama: 'Workplace Drama',
  entitled_parents: 'Entitled Parents',
  family_drama: 'Family Drama',
  confessions: 'Confessions',
  scary_stories: 'Scary Stories',
  creepy_encounters: 'Creepy Encounters',
  wholesome: 'Wholesome Stories',
  mysteries: 'Unsolved Mysteries',
  customer_service_drama: 'Customer Service Drama',
};

export const DEFAULT_CATEGORY_WEIGHTS: Record<StoryCategory, number> = {
  aita: 15,
  relationships: 12,
  cheating: 10,
  revenge: 12,
  workplace_drama: 10,
  entitled_parents: 8,
  family_drama: 8,
  confessions: 5,
  scary_stories: 5,
  creepy_encounters: 3,
  wholesome: 5,
  mysteries: 3,
  customer_service_drama: 4,
};

// ============================================
// Voice & Persona Types
// ============================================

export const VOICE_PERSONAS = [
  'calm',
  'dramatic',
  'sarcastic',
  'confession',
  'horror',
  'warm',
] as const;

export type VoicePersona = (typeof VOICE_PERSONAS)[number];

export const TTS_PROVIDERS = ['openai', 'gemini'] as const;
export type TTSProvider = (typeof TTS_PROVIDERS)[number];

export const OPENAI_VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'] as const;
export type OpenAIVoice = (typeof OPENAI_VOICES)[number];

export const PERSONA_VOICE_MAP: Record<VoicePersona, OpenAIVoice[]> = {
  calm: ['alloy', 'nova'],
  dramatic: ['onyx', 'echo'],
  sarcastic: ['fable', 'shimmer'],
  confession: ['alloy', 'echo'],
  horror: ['onyx', 'echo'],
  warm: ['nova', 'shimmer'],
};

// ============================================
// Caption Styles
// ============================================

export const CAPTION_STYLES = ['word_highlight', 'sentence', 'karaoke', 'minimal'] as const;
export type CaptionStyle = (typeof CAPTION_STYLES)[number];

// ============================================
// Batch & Job Status
// ============================================

export const BATCH_STATUSES = [
  'pending',
  'topic_selected',
  'stories_generated',
  'policy_checked',
  'tts_complete',
  'captions_complete',
  'assets_collected',
  'rendering',
  'rendered',
  'uploading',
  'uploaded',
  'completed',
  'failed',
  'cancelled',
] as const;

export type BatchStatus = (typeof BATCH_STATUSES)[number];

export const JOB_STATUSES = [
  'queued',
  'processing',
  'completed',
  'failed',
  'retrying',
  'cancelled',
] as const;

export type JobStatus = (typeof JOB_STATUSES)[number];

export const RENDER_TYPES = ['short', 'long_form'] as const;
export type RenderType = (typeof RENDER_TYPES)[number];

export const VIDEO_FORMATS = {
  short: { width: 1080, height: 1920, fps: 30, codec: 'h264', audio: 'aac' },
  long_form: { width: 1920, height: 1080, fps: 30, codec: 'h264', audio: 'aac' },
} as const;

// ============================================
// Upload Privacy
// ============================================

export const UPLOAD_PRIVACY_MODES = ['public', 'unlisted', 'private'] as const;
export type UploadPrivacyMode = (typeof UPLOAD_PRIVACY_MODES)[number];

// ============================================
// Policy Types
// ============================================

export const POLICY_FLAGS = [
  'explicit_sexual',
  'minor_sexual',
  'graphic_violence',
  'hate_speech',
  'real_person_accusation',
  'private_information',
  'doxxing',
  'self_harm',
  'crime_instructions',
  'copyrighted_character',
  'copyrighted_plot',
  'excessive_profanity',
  'medical_advice',
  'legal_advice',
  'financial_advice',
  'defamation',
] as const;

export type PolicyFlag = (typeof POLICY_FLAGS)[number];

export const POLICY_SEVERITY = ['block', 'rewrite', 'warn', 'info'] as const;
export type PolicySeverity = (typeof POLICY_SEVERITY)[number];

// ============================================
// Asset Types
// ============================================

export const ASSET_PROVIDERS = ['pexels', 'pixabay', 'local', 'mixkit'] as const;
export type AssetProvider = (typeof ASSET_PROVIDERS)[number];

export const ASSET_TYPES = ['video', 'image', 'audio'] as const;
export type AssetType = (typeof ASSET_TYPES)[number];

export const ASSET_LICENSE_STATUSES = ['verified', 'pending', 'rejected', 'unknown'] as const;
export type AssetLicenseStatus = (typeof ASSET_LICENSE_STATUSES)[number];

export const BACKGROUND_QUERIES = [
  'endless runner',
  'mobile game background',
  '3D obstacle course',
  'parkour POV',
  'voxel parkour',
  'block world parkour',
  'platformer gameplay',
  'satisfying gameplay',
  'abstract arcade',
  'neon tunnel',
  'subway tunnel',
  'train tracks',
  'nature timelapse',
  'ocean waves',
  'city nightscape',
  'aurora borealis',
  'rainy window',
  'fireplace cozy',
  'space nebula',
  'underwater coral',
] as const;

// ============================================
// Zod Schemas
// ============================================

export const StorySchema = z.object({
  id: z.string().uuid(),
  batchId: z.string().uuid(),
  channelId: z.string().uuid().optional(),
  category: z.enum(STORY_CATEGORIES),
  type: z.enum(['short', 'long_form_extra']),
  title: z.string().min(1).max(200),
  hook: z.string().min(1).max(300),
  body: z.string().min(50),
  commentBait: z.string().optional(),
  wordCount: z.number().int().positive(),
  estimatedDurationSeconds: z.number().positive(),
  voicePersona: z.enum(VOICE_PERSONAS),
  originalityHash: z.string(),
  noveltyScore: z.number().min(0).max(1),
  promptUsed: z.string(),
  rawOutput: z.string(),
  policyStatus: z.enum(['clean', 'flagged', 'rewritten', 'blocked']),
  policyFlags: z.array(z.enum(POLICY_FLAGS)),
  order: z.number().int().min(0),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});

export type Story = z.infer<typeof StorySchema>;

export const DailyBatchSchema = z.object({
  id: z.string().uuid(),
  channelId: z.string().uuid().optional(),
  date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  category: z.enum(STORY_CATEGORIES),
  status: z.enum(BATCH_STATUSES),
  shortsCount: z.number().int().min(0),
  longFormCount: z.number().int().min(0),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
  completedAt: z.string().datetime().optional(),
  error: z.string().optional(),
});

export type DailyBatch = z.infer<typeof DailyBatchSchema>;

export const AssetSchema = z.object({
  id: z.string().uuid(),
  channelId: z.string().uuid().optional(),
  provider: z.enum(ASSET_PROVIDERS),
  type: z.enum(ASSET_TYPES),
  originalUrl: z.string().url(),
  creator: z.string(),
  license: z.string(),
  attribution: z.string(),
  sourceQuery: z.string(),
  checksum: z.string(),
  durationSeconds: z.number().optional(),
  width: z.number().int().optional(),
  height: z.number().int().optional(),
  localPath: z.string(),
  usageCount: z.number().int().min(0),
  policyStatus: z.enum(ASSET_LICENSE_STATUSES),
  createdAt: z.string().datetime(),
});

export type Asset = z.infer<typeof AssetSchema>;

export const RenderJobSchema = z.object({
  id: z.string().uuid(),
  batchId: z.string().uuid(),
  channelId: z.string().uuid().optional(),
  type: z.enum(RENDER_TYPES),
  status: z.enum(JOB_STATUSES),
  storyIds: z.array(z.string().uuid()),
  assetIds: z.array(z.string().uuid()),
  outputPath: z.string().optional(),
  thumbnailPath: z.string().optional(),
  durationSeconds: z.number().optional(),
  renderStartedAt: z.string().datetime().optional(),
  renderCompletedAt: z.string().datetime().optional(),
  error: z.string().optional(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});

export type RenderJob = z.infer<typeof RenderJobSchema>;

export const YouTubeUploadSchema = z.object({
  id: z.string().uuid(),
  videoId: z.string().optional(),
  channelId: z.string().uuid().optional(),
  renderJobId: z.string().uuid(),
  youtubeVideoId: z.string().optional(),
  title: z.string(),
  description: z.string(),
  tags: z.array(z.string()),
  categoryId: z.string().default('24'),
  requestedPrivacy: z.enum(UPLOAD_PRIVACY_MODES),
  actualPrivacy: z.enum(UPLOAD_PRIVACY_MODES).optional(),
  thumbnailUrl: z.string().optional(),
  playlistId: z.string().optional(),
  madeForKids: z.boolean().default(false),
  containsSyntheticMedia: z.boolean().default(true),
  status: z.enum(JOB_STATUSES),
  quotaUsed: z.number().int().min(0).default(0),
  uploadedAt: z.string().datetime().optional(),
  error: z.string().optional(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});

export type YouTubeUpload = z.infer<typeof YouTubeUploadSchema>;

export const AnalyticsSnapshotSchema = z.object({
  id: z.string().uuid(),
  youtubeUploadId: z.string().uuid(),
  youtubeVideoId: z.string(),
  snapshotDate: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  views: z.number().int().min(0),
  likes: z.number().int().min(0),
  comments: z.number().int().min(0),
  watchTimeMinutes: z.number().min(0).optional(),
  avgViewDurationSeconds: z.number().min(0).optional(),
  subscribersGained: z.number().int().min(0).optional(),
  impressions: z.number().int().min(0).optional(),
  ctr: z.number().min(0).max(100).optional(),
  createdAt: z.string().datetime(),
});

export type AnalyticsSnapshot = z.infer<typeof AnalyticsSnapshotSchema>;

export const ApiUsageLogSchema = z.object({
  id: z.string().uuid(),
  channelId: z.string().uuid().optional(),
  provider: z.string(),
  endpoint: z.string(),
  tokensUsed: z.number().int().min(0).optional(),
  costEstimate: z.number().min(0).optional(),
  statusCode: z.number().int().optional(),
  error: z.string().optional(),
  createdAt: z.string().datetime(),
});

export type ApiUsageLog = z.infer<typeof ApiUsageLogSchema>;

// ============================================
// Config Schema
// ============================================

export const AppConfigSchema = z.object({
  autoMode: z.boolean().default(true),
  requireApproval: z.boolean().default(false),
  failSafeOnPolicyFlag: z.boolean().default(true),
  failSafeOnLicenseUnknown: z.boolean().default(true),
  uploadPrivacyMode: z.enum(UPLOAD_PRIVACY_MODES).default('public'),
  allowPrivateFallback: z.boolean().default(true),
  shortsPerDayMin: z.number().int().min(1).max(10).default(3),
  shortsPerDayMax: z.number().int().min(1).max(10).default(5),
  longFormPerDay: z.number().int().min(0).max(5).default(1),
  longFormTargetMinutes: z.number().min(5).max(30).default(10),
  dailyTopicMode: z.enum(['weighted_random', 'round_robin', 'manual']).default('weighted_random'),
  ttsOpenaiRatio: z.number().min(0).max(1).default(0.8),
  ttsGeminiRatio: z.number().min(0).max(1).default(0.2),
  categoryWeights: z.record(z.enum(STORY_CATEGORIES), z.number()).optional(),
  topicCooldownDays: z.number().int().min(0).default(2),
  maxSameHookPerWeek: z.number().int().min(1).default(2),
  disclosureLine: z.string().default('These are original fictional stories created for entertainment.'),
  dryRun: z.boolean().default(false),
});

export type AppConfig = z.infer<typeof AppConfigSchema>;

// ============================================
// Channels (multi-channel)
// ============================================

export const CHANNEL_STATUSES = ['active', 'paused'] as const;
export type ChannelStatus = (typeof CHANNEL_STATUSES)[number];

export const INTEGRATION_KINDS = ['text', 'tts', 'assets', 'youtube', 'storage'] as const;
export type IntegrationKind = (typeof INTEGRATION_KINDS)[number];

export const INTEGRATION_PROVIDERS = [
  'text.openai',
  'text.gemini',
  'tts.openai',
  'tts.gemini',
  'assets.pexels',
  'assets.pixabay',
  'youtube',
  'storage.local',
  'storage.r2',
] as const;
export type IntegrationProvider = (typeof INTEGRATION_PROVIDERS)[number];

export const INTEGRATION_STATUSES = ['unknown', 'ok', 'error', 'missing'] as const;
export type IntegrationStatus = (typeof INTEGRATION_STATUSES)[number];

// Where a provider's credentials live: 'app' = shared across all channels
// (OpenAI/Gemini/Pexels/Pixabay/TTS/storage); 'channel' = per-channel (YouTube).
export const INTEGRATION_SCOPES = ['app', 'channel'] as const;
export type IntegrationScope = (typeof INTEGRATION_SCOPES)[number];

/** Providers whose credentials are configured once, app-wide. */
export const APP_SCOPED_PROVIDERS: readonly IntegrationProvider[] = [
  'text.openai',
  'text.gemini',
  'tts.openai',
  'tts.gemini',
  'assets.pexels',
  'assets.pixabay',
  'storage.local',
  'storage.r2',
] as const;

/** Providers configured per-channel (a different account/token per channel). */
export const CHANNEL_SCOPED_PROVIDERS: readonly IntegrationProvider[] = ['youtube'] as const;

/**
 * Per-channel content config overrides. Keys mirror the worker's canonical
 * snake_case representation (stored as a JSON blob); any field omitted inherits
 * the app-level default. All fields are optional because a channel only stores
 * the values it overrides.
 */
// Pipeline mode = the channel's content engine. "fiction" is the default
// Reddit-style drama; "recap_shorts" cuts many Shorts from user-supplied video
// files; "sleep_facts" produces one calm, single-topic long-form video.
export const PIPELINE_MODES = ['fiction', 'recap_shorts', 'sleep_facts'] as const;
export type PipelineMode = (typeof PIPELINE_MODES)[number];

export const RECAP_FOOTAGE_MODES = ['with_source_video', 'stock_metaphor'] as const;
export type RecapFootageMode = (typeof RECAP_FOOTAGE_MODES)[number];

/** CinybeShorts (recap_shorts) per-channel config (channel.config.recap). */
export const RecapConfigSchema = z
  .object({
    source_mode: z.literal('user_supplied'),
    inbox_path: z.string(),
    active_series_slug: z.string().nullable(),
    target_short_seconds: z.number().min(10).max(180),
    min_short_seconds: z.number().min(5).max(180),
    overlap_seconds: z.number().min(0).max(60),
    min_shorts_per_episode: z.number().int().min(1).max(200),
    max_shorts_per_episode: z.number().int().min(1).max(500),
    max_shorts_per_run: z.number().int().min(1).max(50),
    footage_mode: z.enum(RECAP_FOOTAGE_MODES),
    voice_persona: z.enum(VOICE_PERSONAS),
  })
  .partial();

export type RecapConfig = z.infer<typeof RecapConfigSchema>;

/** Sleep On Facts (sleep_facts) per-channel config (channel.config.sleep_facts).
 *  v2: ~3-hour themed fact-compilation videos — one calm voice (onyx) over a
 *  slideshow of 100+ stock images. See
 *  docs/superpowers/specs/2026-06-07-sleep-facts-3hour-slideshow-design.md */
export const SleepFactsConfigSchema = z
  .object({
    topic_rotation: z.array(z.string()),
    long_form_target_minutes: z.number().min(1).max(240), // up to ~4h of narration
    narration_wpm: z.number().int().min(60).max(220),
    voice_persona: z.enum(VOICE_PERSONAS),
    enable_wikipedia_grounding: z.boolean(),
    // Segmented generation (avoids the single-call token ceiling).
    gen_num_movements: z.number().int().min(1).max(60),
    gen_segment_max_tokens: z.number().int().min(256).max(8000),
    // Single narration voice.
    tts_voice: z.string(),
    tts_model: z.string(),
    tts_speed: z.number().min(0.25).max(4),
    tts_instructions: z.string().nullable(),
    // Image slideshow.
    asset_type: z.enum(['image', 'video']),
    images_target: z.number().int().min(1).max(400),
    slideshow_dwell_seconds: z.number().min(2).max(120),
    crossfade_seconds: z.number().min(0).max(10),
    slideshow_fps: z.number().int().min(10).max(60),
    ken_burns: z.boolean(),
    captions_enabled: z.boolean(),
    music_enabled: z.boolean(),
    // Legacy alias retained for back-compat.
    assets_per_video: z.number().int().min(1).max(400),
  })
  .partial();

export type SleepFactsConfig = z.infer<typeof SleepFactsConfigSchema>;

export const ChannelConfigSchema = z
  .object({
    // The content engine for this channel (see PIPELINE_MODES).
    pipeline_mode: z.enum(PIPELINE_MODES),
    // Nested, mode-specific config blocks (only meaningful for their mode).
    recap: RecapConfigSchema,
    sleep_facts: SleepFactsConfigSchema,
    // Content outputs (composable per-channel shape). A channel produces Shorts,
    // a long-form video, or both. At least one must stay enabled.
    enable_shorts: z.boolean(),
    enable_long_form: z.boolean(),
    // Shorts are turned off via enable_shorts=false, so when present the count
    // is at least 1 (a 0 quota with shorts enabled would silently make nothing).
    shorts_per_day_min: z.number().int().min(1).max(10),
    shorts_per_day_max: z.number().int().min(1).max(10),
    long_form_per_day: z.number().int().min(0).max(5),
    long_form_target_minutes: z.number().min(1).max(240),
    // Number of long-form stories composed into one long-form video.
    long_form_segments_min: z.number().int().min(1).max(20),
    long_form_segments_max: z.number().int().min(1).max(20),
    // Whether approved Shorts are also folded into the long-form compilation.
    long_form_includes_shorts: z.boolean(),
    daily_topic_mode: z.enum(['weighted_random', 'round_robin', 'manual']),
    topic_cooldown_days: z.number().int().min(0),
    max_same_hook_per_week: z.number().int().min(1),
    tts_openai_ratio: z.number().min(0).max(1),
    tts_gemini_ratio: z.number().min(0).max(1),
    upload_privacy_mode: z.enum(UPLOAD_PRIVACY_MODES),
    allow_private_fallback: z.boolean(),
    fail_safe_on_policy_flag: z.boolean(),
    fail_safe_on_license_unknown: z.boolean(),
    caption_style: z.enum(CAPTION_STYLES),
    disclosure_line: z.string(),
    category_weights: z.record(z.enum(STORY_CATEGORIES), z.number()),
    category_hints: z.record(z.enum(STORY_CATEGORIES), z.string()),
    voice_personas: z.array(z.enum(VOICE_PERSONAS)),
  })
  .partial()
  .superRefine((cfg, ctx) => {
    if (cfg.enable_shorts === false && cfg.enable_long_form === false) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'A channel must produce at least one output (Shorts or long-form).',
        path: ['enable_shorts'],
      });
    }
    if (
      cfg.shorts_per_day_min != null &&
      cfg.shorts_per_day_max != null &&
      cfg.shorts_per_day_min > cfg.shorts_per_day_max
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'shorts_per_day_min cannot exceed shorts_per_day_max.',
        path: ['shorts_per_day_min'],
      });
    }
    if (
      cfg.long_form_segments_min != null &&
      cfg.long_form_segments_max != null &&
      cfg.long_form_segments_min > cfg.long_form_segments_max
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'long_form_segments_min cannot exceed long_form_segments_max.',
        path: ['long_form_segments_min'],
      });
    }
    // The content engine must match the enabled outputs.
    if (
      cfg.pipeline_mode === 'recap_shorts' &&
      (cfg.enable_shorts === false || cfg.enable_long_form === true)
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'recap_shorts channels produce Shorts only (enable_shorts true, enable_long_form false).',
        path: ['pipeline_mode'],
      });
    }
    if (
      cfg.pipeline_mode === 'sleep_facts' &&
      (cfg.enable_long_form === false || cfg.enable_shorts === true)
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'sleep_facts channels produce long-form only (enable_long_form true, enable_shorts false).',
        path: ['pipeline_mode'],
      });
    }
  });

export type ChannelConfig = z.infer<typeof ChannelConfigSchema>;

export const ChannelSchema = z.object({
  id: z.string().uuid(),
  slug: z.string().min(1),
  name: z.string().min(1),
  description: z.string().optional(),
  status: z.enum(CHANNEL_STATUSES),
  niche: z.string().optional(),
  contentStyle: z.string().optional(),
  config: ChannelConfigSchema.default({}),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});

export type Channel = z.infer<typeof ChannelSchema>;

export const ChannelIntegrationSchema = z.object({
  id: z.string().uuid(),
  channelId: z.string().uuid(),
  providerKey: z.enum(INTEGRATION_PROVIDERS),
  enabled: z.boolean(),
  // Non-secret config only (model, voice map, bucket...). Secrets are never
  // sent to the client — only masked/"configured" status is exposed.
  config: z.record(z.string(), z.unknown()).default({}),
  status: z.enum(INTEGRATION_STATUSES),
  statusDetail: z.string().optional(),
  lastCheckedAt: z.string().datetime().optional(),
  updatedAt: z.string().datetime(),
});

export type ChannelIntegration = z.infer<typeof ChannelIntegrationSchema>;

/** App-level (shared) integration. Mirrors ChannelIntegration without channelId. */
export const AppIntegrationSchema = z.object({
  id: z.string().uuid(),
  providerKey: z.enum(INTEGRATION_PROVIDERS),
  enabled: z.boolean(),
  // Non-secret config only. Secrets are never sent to the client — only a
  // masked/"configured" status is exposed.
  config: z.record(z.string(), z.unknown()).default({}),
  status: z.enum(INTEGRATION_STATUSES),
  statusDetail: z.string().optional(),
  lastCheckedAt: z.string().datetime().optional(),
  updatedAt: z.string().datetime(),
});

export type AppIntegration = z.infer<typeof AppIntegrationSchema>;

/** Declarative description of a provider, mirrored from the worker registry. */
export interface IntegrationProviderSpec {
  key: IntegrationProvider;
  kind: IntegrationKind;
  label: string;
  scope: IntegrationScope;
  requiredSecrets: string[];
  optionalSecrets: string[];
  configFields: string[];
}

// ============================================
// API Response Types
// ============================================

export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  meta?: {
    page?: number;
    pageSize?: number;
    total?: number;
  };
}

export interface DashboardStats {
  totalBatches: number;
  totalStories: number;
  totalVideos: number;
  totalUploads: number;
  totalViews: number;
  totalLikes: number;
  activeBatch: DailyBatch | null;
  recentUploads: YouTubeUpload[];
  categoryBreakdown: { category: StoryCategory; count: number }[];
  weeklyViews: { date: string; views: number }[];
}

export interface ContentCalendarEntry {
  date: string;
  batch: DailyBatch | null;
  shortsCount: number;
  longFormCount: number;
  uploadsCount: number;
  totalViews: number;
}

// ============================================
// Prompt Template Types
// ============================================

export interface PromptTemplate {
  id: string;
  name: string;
  description: string;
  template: string;
  variables: string[];
  category: 'topic' | 'story' | 'hook' | 'metadata' | 'thumbnail' | 'comment' | 'policy' | 'rewrite' | 'title_variation';
  version: number;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}
