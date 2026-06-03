import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { RunBatchButton } from "@/components/run-batch-button";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

const statusStyles: Record<string, string> = {
  completed: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  uploaded: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  rendering: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30 animate-pulse",
  failed: "bg-red-500/20 text-red-400 border-red-500/30",
  pending: "bg-gray-500/20 text-gray-400 border-gray-500/30",
};

function formatDuration(seconds: number): string {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m === 0) return `${s}s`;
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

export default async function BatchesPage() {
  const channelId = await getActiveChannelId();
  // Query all batches for the active channel
  const batches = await query(`
    SELECT
      b.id,
      b.date,
      b.category,
      b.status,
      b.shorts_count,
      b.long_form_count,
      COALESCE(SUM(r.duration_seconds), 0) as duration_seconds,
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
  `, [channelId]);

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold gradient-text">Daily Batches</h1>
            <p className="mt-1 text-muted-foreground">Content generation batch history and details</p>
          </div>
          <RunBatchButton />
        </div>

        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent border-border/50">
                  <TableHead>Date</TableHead>
                  <TableHead>Batch ID</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Stories</TableHead>
                  <TableHead>Shorts</TableHead>
                  <TableHead>Long-form</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Views</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {batches.length > 0 ? (
                  batches.map((batch: any) => (
                    <TableRow key={batch.id} className="cursor-pointer border-border/30 transition-colors hover:bg-accent/30">
                      <TableCell className="font-mono text-sm">{batch.date}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{batch.id}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {(batch.category || "n/a").replace("_", " ")}
                        </Badge>
                      </TableCell>
                      <TableCell>{batch.shortsCount + batch.longFormCount}</TableCell>
                      <TableCell>{batch.shortsCount}</TableCell>
                      <TableCell>{batch.longFormCount}</TableCell>
                      <TableCell className="text-muted-foreground">{formatDuration(batch.durationSeconds)}</TableCell>
                      <TableCell className="font-medium">{batch.views > 0 ? batch.views.toLocaleString() : "—"}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={statusStyles[batch.status] || ""}>
                          {batch.status}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={9} className="text-center py-8 text-muted-foreground">
                      No batches found. Run a new batch to start generating content!
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
