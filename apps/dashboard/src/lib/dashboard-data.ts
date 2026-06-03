import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const ALL_DASHBOARD_SLICES = [
  "metrics",
  "recentBatches",
  "pipelineStatus",
  "analytics",
] as const;

export type DashboardSlice = (typeof ALL_DASHBOARD_SLICES)[number];

export interface DashboardMetrics {
  totalBatches: number;
  totalStories: number;
  totalRenders: number;
  totalUploads: number;
  todayStories: number;
  todayRenders: number;
  todayUploads: number;
}

export interface DashboardData {
  metrics: DashboardMetrics;
  recentBatches: any[];
  latestStatus: string;
  topPerformers: any[];
}

type SlicePayload = Partial<DashboardData>;

interface CacheEntry {
  payload: SlicePayload;
  refreshedAt: number;
}

const defaultMetrics: DashboardMetrics = {
  totalBatches: 0,
  totalStories: 0,
  totalRenders: 0,
  totalUploads: 0,
  todayStories: 0,
  todayRenders: 0,
  todayUploads: 0,
};

const defaultDashboardData: DashboardData = {
  metrics: defaultMetrics,
  recentBatches: [],
  latestStatus: "pending",
  topPerformers: [],
};

// Cache is keyed by channel so switching channels never shows stale cross-channel data.
const sliceCache: Record<string, CacheEntry> = {};
const sliceSet = new Set<string>(ALL_DASHBOARD_SLICES);

function cacheKey(channelId: string, slice: DashboardSlice): string {
  return `${channelId}:${slice}`;
}

function isDashboardSlice(value: string): value is DashboardSlice {
  return sliceSet.has(value);
}

export function parseDashboardSlices(rawSlices: string | null): DashboardSlice[] {
  if (!rawSlices) return [...ALL_DASHBOARD_SLICES];

  const slices = rawSlices
    .split(",")
    .map((slice) => slice.trim())
    .filter(isDashboardSlice);

  return slices.length > 0 ? [...new Set(slices)] : [...ALL_DASHBOARD_SLICES];
}

function toNumber(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "string") return Number(value) || 0;
  return 0;
}

async function loadMetrics(channelId: string): Promise<DashboardMetrics> {
  const todayDateStr = new Date().toISOString().slice(0, 10);
  const rows = await query<DashboardMetrics>(
    `
      SELECT
        (SELECT COUNT(*) FROM daily_batches
           WHERE status != 'dry_run_completed' AND channel_id = $1) as total_batches,
        (SELECT COUNT(*) FROM stories s
           JOIN daily_batches b ON s.batch_id = b.id
           WHERE b.status != 'dry_run_completed' AND s.channel_id = $1) as total_stories,
        (SELECT COUNT(*) FROM render_jobs
           WHERE status = 'completed' AND channel_id = $1) as total_renders,
        (SELECT COUNT(*) FROM youtube_uploads
           WHERE status IN ('completed', 'uploaded') AND channel_id = $1) as total_uploads,
        (SELECT COUNT(*) FROM stories s
           JOIN daily_batches b ON s.batch_id = b.id
           WHERE b.status != 'dry_run_completed' AND s.channel_id = $1
             AND date(s.created_at) = $2) as today_stories,
        (SELECT COUNT(*) FROM render_jobs
           WHERE status = 'completed' AND channel_id = $1
             AND date(created_at) = $2) as today_renders,
        (SELECT COUNT(*) FROM youtube_uploads
           WHERE status IN ('completed', 'uploaded') AND channel_id = $1
             AND date(uploaded_at) = $2) as today_uploads
    `,
    [channelId, todayDateStr]
  );

  const row = rows[0] || defaultMetrics;
  return {
    totalBatches: toNumber(row.totalBatches),
    totalStories: toNumber(row.totalStories),
    totalRenders: toNumber(row.totalRenders),
    totalUploads: toNumber(row.totalUploads),
    todayStories: toNumber(row.todayStories),
    todayRenders: toNumber(row.todayRenders),
    todayUploads: toNumber(row.todayUploads),
  };
}

async function loadRecentBatches(channelId: string): Promise<any[]> {
  return query(
    `
    SELECT
      b.id,
      b.date,
      b.category,
      b.status,
      b.shorts_count,
      b.long_form_count,
      COALESCE(v.views, 0) as views
    FROM daily_batches b
    LEFT JOIN render_jobs r ON r.batch_id = b.id
    LEFT JOIN youtube_uploads u ON u.render_job_id = r.id
    LEFT JOIN (
      SELECT youtube_upload_id, MAX(views) as views
      FROM analytics_snapshots
      GROUP BY youtube_upload_id
    ) v ON v.youtube_upload_id = u.id
    WHERE b.channel_id = $1
    GROUP BY b.id
    ORDER BY b.date DESC, b.created_at DESC
    LIMIT 5
  `,
    [channelId]
  );
}

async function loadPipelineStatus(channelId: string): Promise<string> {
  const rows = await query<{ status?: string }>(
    "SELECT status FROM daily_batches WHERE channel_id = $1 ORDER BY date DESC, created_at DESC LIMIT 1",
    [channelId]
  );
  return rows[0]?.status || "pending";
}

async function loadTopPerformers(channelId: string): Promise<any[]> {
  return query(
    `
    SELECT
      u.title,
      COALESCE(v.views, 0) as views,
      COALESCE(v.likes, 0) as likes,
      b.category
    FROM youtube_uploads u
    JOIN render_jobs r ON u.render_job_id = r.id
    JOIN daily_batches b ON r.batch_id = b.id
    LEFT JOIN (
      SELECT youtube_upload_id, MAX(views) as views, MAX(likes) as likes
      FROM analytics_snapshots
      GROUP BY youtube_upload_id
    ) v ON v.youtube_upload_id = u.id
    WHERE u.status IN ('completed', 'uploaded') AND u.channel_id = $1
    ORDER BY views DESC
    LIMIT 4
  `,
    [channelId]
  );
}

async function loadDashboardSlice(slice: DashboardSlice, channelId: string): Promise<SlicePayload> {
  switch (slice) {
    case "metrics":
      return { metrics: await loadMetrics(channelId) };
    case "recentBatches":
      return { recentBatches: await loadRecentBatches(channelId) };
    case "pipelineStatus":
      return { latestStatus: await loadPipelineStatus(channelId) };
    case "analytics":
      return { topPerformers: await loadTopPerformers(channelId) };
  }
}

export async function getDashboardData({
  slices = [...ALL_DASHBOARD_SLICES],
  force = false,
  channelId,
}: {
  slices?: DashboardSlice[];
  force?: boolean;
  channelId?: string;
} = {}): Promise<SlicePayload & { updatedSlices: DashboardSlice[] }> {
  const activeChannelId = channelId || (await getActiveChannelId()) || "none";
  const requestedSlices = slices.length > 0 ? slices : [...ALL_DASHBOARD_SLICES];
  const payload: SlicePayload & { updatedSlices: DashboardSlice[] } = {
    updatedSlices: requestedSlices,
  };

  for (const slice of requestedSlices) {
    const key = cacheKey(activeChannelId, slice);
    if (force || !sliceCache[key]) {
      sliceCache[key] = {
        payload: await loadDashboardSlice(slice, activeChannelId),
        refreshedAt: Date.now(),
      };
    }
    Object.assign(payload, sliceCache[key]?.payload);
  }

  return payload;
}

export async function getFullDashboardData({
  force = false,
  channelId,
}: {
  force?: boolean;
  channelId?: string;
} = {}): Promise<DashboardData> {
  const payload = await getDashboardData({
    slices: [...ALL_DASHBOARD_SLICES],
    force,
    channelId,
  });

  return {
    metrics: payload.metrics || defaultDashboardData.metrics,
    recentBatches: payload.recentBatches || defaultDashboardData.recentBatches,
    latestStatus: payload.latestStatus || defaultDashboardData.latestStatus,
    topPerformers: payload.topPerformers || defaultDashboardData.topPerformers,
  };
}
