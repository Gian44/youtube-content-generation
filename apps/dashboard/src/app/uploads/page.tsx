import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { UploadButton } from "@/components/upload-button";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

const mockUploads = [
  { id: "u-001", title: "AITA for refusing to share my lottery winnings?", youtubeVideoId: "abc123def", type: "short", requestedPrivacy: "public", actualPrivacy: "public", status: "completed", views: 12400, likes: 890, date: "2026-05-28" },
  { id: "u-002", title: "My neighbor threw a party so loud...", youtubeVideoId: "ghi456jkl", type: "short", requestedPrivacy: "public", actualPrivacy: "public", status: "completed", views: 8900, likes: 620, date: "2026-05-28" },
  { id: "u-003", title: "AITA for walking out of my own birthday?", youtubeVideoId: null, type: "short", requestedPrivacy: "public", actualPrivacy: null, status: "queued", views: 0, likes: 0, date: "2026-05-28" },
  { id: "u-004", title: "Reddit Stories That Will Keep You Up | AITA", youtubeVideoId: "mno789pqr", type: "long_form", requestedPrivacy: "public", actualPrivacy: "private", status: "completed", views: 2300, likes: 180, date: "2026-05-28" },
  { id: "u-005", title: "The Revenge Was Perfectly Calculated", youtubeVideoId: "stu012vwx", type: "short", requestedPrivacy: "public", actualPrivacy: "public", status: "completed", views: 15200, likes: 1100, date: "2026-05-27" },
];

export default async function UploadsPage() {
  const todayDateStr = new Date().toISOString().slice(0, 10);
  const channelId = await getActiveChannelId();

  // Query stats for the active channel
  const uploadStats = await query(`
    SELECT
      COALESCE(SUM(CASE WHEN status IN ('completed', 'uploaded') THEN 1 ELSE 0 END), 0) as uploaded_count,
      COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0) as queued_count
    FROM youtube_uploads
    WHERE channel_id = $1
  `, [channelId]);

  const quotaStats = await query(`
    SELECT
      COALESCE(SUM(tokens_used), 0) as youtube_quota
    FROM api_usage_logs
    WHERE provider = 'youtube' AND date(created_at) = $1 AND channel_id = $2
  `, [todayDateStr, channelId]);

  const uploaded = uploadStats[0]?.uploadedCount || 0;
  const queued = uploadStats[0]?.queuedCount || 0;
  const quotaUsed = quotaStats[0]?.youtubeQuota || 0;

  const totalUploads = uploaded + queued;
  const isDemoMode = totalUploads === 0;

  // Query uploads list
  const dbUploads = await query(`
    SELECT 
      u.id,
      u.title,
      u.youtube_video_id,
      u.requested_privacy,
      u.actual_privacy,
      u.status,
      u.created_at,
      r.type,
      COALESCE(v.views, 0) as views,
      COALESCE(v.likes, 0) as likes
    FROM youtube_uploads u
    LEFT JOIN render_jobs r ON u.render_job_id = r.id
    LEFT JOIN (
      SELECT youtube_upload_id, MAX(views) as views, MAX(likes) as likes
      FROM analytics_snapshots
      GROUP BY youtube_upload_id
    ) v ON v.youtube_upload_id = u.id
    WHERE u.channel_id = $1
    ORDER BY u.created_at DESC
  `, [channelId]);

  const uploadsList = isDemoMode ? mockUploads : dbUploads.map((u: any) => ({
    id: u.id,
    title: u.title,
    youtubeVideoId: u.youtubeVideoId,
    type: u.type || "short",
    requestedPrivacy: u.requestedPrivacy,
    actualPrivacy: u.actualPrivacy,
    status: u.status,
    views: Number(u.views),
    likes: Number(u.likes),
    date: new Date(u.createdAt).toISOString().slice(0, 10),
  }));

  const displayUploaded = isDemoMode ? 156 : uploaded;
  const displayQueued = isDemoMode ? 2 : queued;
  const displayQuotaUsed = isDemoMode ? 4800 : quotaUsed;
  const displayQuotaRemaining = 10000 - displayQuotaUsed;

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold gradient-text">Upload Queue</h1>
            <p className="mt-1 text-muted-foreground">YouTube upload status and history</p>
          </div>
          <UploadButton />
        </div>

        <div className="grid gap-4 sm:grid-cols-4">
          {[
            { label: "Uploaded", value: String(displayUploaded), icon: "✅" },
            { label: "Queued", value: String(displayQueued), icon: "⏳" },
            { label: "YT Quota Used", value: displayQuotaUsed.toLocaleString(), icon: "📊" },
            { label: "YT Quota Remaining", value: displayQuotaRemaining.toLocaleString(), icon: "🎯" },
          ].map((s) => (
            <Card key={s.label} className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardContent className="flex items-center gap-3 p-4">
                <span className="text-xl">{s.icon}</span>
                <div>
                  <p className="text-xl font-bold">{s.value}</p>
                  <p className="text-xs text-muted-foreground">{s.label}</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        <div className="space-y-3">
          {uploadsList.length > 0 ? (
            uploadsList.map((upload) => (
              <Card key={upload.id} className="border-border/30 bg-card/30 backdrop-blur-sm transition-all hover:border-primary/20">
                <CardContent className="p-5">
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex items-center gap-4 flex-1">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-red-500/20 text-lg">
                        ▶️
                      </div>
                      <div>
                        <p className="font-medium text-sm line-clamp-1">{upload.title}</p>
                        <div className="flex items-center gap-2 mt-1">
                          {upload.youtubeVideoId && (
                            <span className="text-xs text-muted-foreground font-mono">{upload.youtubeVideoId}</span>
                          )}
                          <Badge variant="outline" className="text-[10px]">
                            {upload.type === "short" ? "📱 Short" : "🎬 Long-form"}
                          </Badge>
                          <span className="text-xs text-muted-foreground font-mono">{upload.date}</span>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-6 shrink-0">
                      {upload.views > 0 && (
                        <div className="text-right text-xs">
                          <div className="font-medium">{upload.views.toLocaleString()} views</div>
                          <div className="text-muted-foreground">{upload.likes.toLocaleString()} likes</div>
                        </div>
                      )}
                      <div className="text-right text-xs">
                        <div className="text-muted-foreground">Requested: {upload.requestedPrivacy}</div>
                        {upload.actualPrivacy && upload.actualPrivacy !== upload.requestedPrivacy && (
                          <div className="text-yellow-400">Actual: {upload.actualPrivacy}</div>
                        )}
                      </div>
                      <Badge variant="outline" className={`${
                        upload.status === "completed" || upload.status === "uploaded" ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" :
                        upload.status === "queued" ? "bg-gray-500/20 text-gray-400 border-gray-500/30" :
                        "bg-red-500/20 text-red-400 border-red-500/30"
                      }`}>
                        {upload.status}
                      </Badge>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))
          ) : (
            <div className="text-center py-6 text-muted-foreground text-sm">
              No uploads in history.
            </div>
          )}
        </div>
      </div>
    </DashboardLayout>
  );
}
