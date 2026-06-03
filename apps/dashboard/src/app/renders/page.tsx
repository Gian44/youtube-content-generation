import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

const statusColors: Record<string, string> = {
  completed: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  processing: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  queued: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  failed: "bg-red-500/20 text-red-400 border-red-500/30",
  retrying: "bg-orange-500/20 text-orange-400 border-orange-500/30",
};

const mockJobs = [
  { id: "r-001", type: "short", batch: "f8e2a1b3", story: "AITA for refusing lottery...", status: "completed", duration: "45s", size: "12MB", time: "2m 14s", date: "2026-05-28" },
  { id: "r-002", type: "short", batch: "f8e2a1b3", story: "Neighbor party car alarms...", status: "completed", duration: "42s", size: "11MB", time: "1m 58s", date: "2026-05-28" },
  { id: "r-003", type: "short", batch: "f8e2a1b3", story: "Walking out of birthday...", status: "processing", duration: "46s", size: "—", time: "—", date: "2026-05-28" },
  { id: "r-004", type: "long_form", batch: "f8e2a1b3", story: "Compilation (7 stories)", status: "queued", duration: "~12m", size: "—", time: "—", date: "2026-05-28" },
  { id: "r-005", type: "short", batch: "a3c4d5e6", story: "Revenge perfectly calculated...", status: "completed", duration: "48s", size: "13MB", time: "2m 05s", date: "2026-05-27" },
];

export default async function RendersPage() {
  const channelId = await getActiveChannelId();
  // Query status counts for the active channel
  const countRes = await query(`
    SELECT
      COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0) as queued_count,
      COALESCE(SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END), 0) as processing_count,
      COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) as completed_count,
      COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) as failed_count
    FROM render_jobs
    WHERE channel_id = $1
  `, [channelId]);

  const queued = countRes[0]?.queuedCount || 0;
  const processing = countRes[0]?.processingCount || 0;
  const completed = countRes[0]?.completedCount || 0;
  const failed = countRes[0]?.failedCount || 0;

  const totalRenders = queued + processing + completed + failed;
  const isDemoMode = totalRenders === 0;

  // Query live jobs for the active channel
  const dbJobs = await query(`
    SELECT r.*, b.date, b.category
    FROM render_jobs r
    JOIN daily_batches b ON r.batch_id = b.id
    WHERE r.channel_id = $1
    ORDER BY r.created_at DESC
    LIMIT 30
  `, [channelId]);

  const dbStories = await query("SELECT id, title FROM stories WHERE channel_id = $1", [channelId]);

  const jobsList = isDemoMode ? mockJobs : dbJobs.map((job: any) => {
    const storyIds = job.storyIds || [];
    let storyTitle = "";
    if (job.type === "short") {
      const match = dbStories.find((s: any) => s.id === storyIds[0]);
      storyTitle = match ? match.title : "Short Video";
    } else {
      storyTitle = `Compilation (${storyIds.length} stories)`;
    }

    let timeStr = "—";
    if (job.renderStartedAt && job.renderCompletedAt) {
      const diff = new Date(job.renderCompletedAt).getTime() - new Date(job.renderStartedAt).getTime();
      const secs = Math.round(diff / 1000);
      const m = Math.floor(secs / 60);
      const s = secs % 60;
      timeStr = m === 0 ? `${s}s` : `${m}m ${s}s`;
    }

    return {
      id: job.id,
      type: job.type,
      batch: job.batchId.slice(0, 8),
      story: storyTitle,
      status: job.status,
      duration: job.durationSeconds ? `${Math.round(job.durationSeconds)}s` : "—",
      size: "—",
      time: timeStr,
      date: job.date,
    };
  });

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold gradient-text">Render Queue</h1>
            <p className="mt-1 text-muted-foreground">Video rendering jobs and progress</p>
          </div>
          {isDemoMode && (
            <Badge variant="outline" className="bg-yellow-500/20 text-yellow-400 border-yellow-500/30">
              💡 Demo Mode: Mock Data Shown
            </Badge>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-4">
          {[
            { label: "Queued", value: String(isDemoMode ? 1 : queued), color: "text-gray-400" },
            { label: "Processing", value: String(isDemoMode ? 1 : processing), color: "text-yellow-400" },
            { label: "Completed", value: String(isDemoMode ? 142 : completed), color: "text-emerald-400" },
            { label: "Failed", value: String(isDemoMode ? 2 : failed), color: "text-red-400" },
          ].map((s) => (
            <Card key={s.label} className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardContent className="p-4 text-center">
                <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
                <p className="text-xs text-muted-foreground">{s.label}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        <div className="space-y-3">
          {jobsList.length > 0 ? (
            jobsList.map((job) => (
              <Card key={job.id} className="border-border/30 bg-card/30 backdrop-blur-sm transition-all hover:border-primary/20">
                <CardContent className="p-5">
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex items-center gap-4 flex-1">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted text-lg">
                        {job.type === "short" ? "📱" : "🎬"}
                      </div>
                      <div>
                        <p className="font-medium text-sm line-clamp-1">{job.story}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="text-xs text-muted-foreground font-mono">{job.id.slice(0, 8)}</span>
                          <Badge variant="outline" className="text-[10px]">{job.type === "short" ? "Short" : "Long-form"}</Badge>
                          <span className="text-xs text-muted-foreground">duration: {job.duration}</span>
                          <span className="text-[10px] text-muted-foreground font-mono">batch: {job.batch}</span>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-6 shrink-0">
                      {job.status === "processing" && (
                        <div className="w-32">
                          <Progress value={67} className="h-2" />
                          <p className="text-[10px] text-muted-foreground mt-1 text-right">67%</p>
                        </div>
                      )}
                      <div className="text-right text-xs text-muted-foreground">
                        {job.size !== "—" && <div>{job.size}</div>}
                        {job.time !== "—" && <div>render time: {job.time}</div>}
                      </div>
                      <Badge variant="outline" className={`${statusColors[job.status] || "bg-gray-500/20 text-gray-400"}`}>
                        {job.status}
                      </Badge>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))
          ) : (
            <div className="text-center py-6 text-muted-foreground text-sm">
              No rendering jobs in history.
            </div>
          )}
        </div>
      </div>
    </DashboardLayout>
  );
}
