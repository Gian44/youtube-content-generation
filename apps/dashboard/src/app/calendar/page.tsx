import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

const statusColor: Record<string, string> = {
  completed: "border-emerald-500/50 bg-emerald-500/5",
  uploaded: "border-blue-500/50 bg-blue-500/5",
  rendering: "border-yellow-500/50 bg-yellow-500/5 animate-pulse",
  failed: "border-red-500/50 bg-red-500/5",
  scheduled: "border-border/30 bg-muted/20",
};

export default async function CalendarPage() {
  const channelId = await getActiveChannelId();
  // Determine current week dates (Mon to Sun)
  const today = new Date();
  const dayOfWeek = today.getDay(); // 0 is Sunday, 1 is Monday, etc.
  const distanceToMonday = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
  const monday = new Date(today);
  monday.setDate(today.getDate() + distanceToMonday);

  const days: string[] = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    days.push(d.toISOString().slice(0, 10));
  }

  // Query batches for the current week
  const dbBatches = await query(`
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
    WHERE b.date IN ($1, $2, $3, $4, $5, $6, $7) AND b.channel_id = $8
    GROUP BY b.id
  `, [...days, channelId]);

  const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const calendarData = days.map((dateStr, i) => {
    const batch = dbBatches.find((b: any) => b.date === dateStr);
    return {
      date: dateStr,
      day: dayNames[i],
      category: batch ? batch.category : null,
      shorts: batch ? batch.shortsCount : 0,
      longForm: batch ? batch.longFormCount : 0,
      status: batch ? batch.status : "scheduled",
      views: batch ? batch.views : 0,
    };
  });

  // Query monthly summary
  const currentMonthStr = today.toISOString().slice(0, 7); // YYYY-MM
  const summaryRes = await query(`
    SELECT 
      COUNT(DISTINCT b.id) as total_batches,
      COALESCE(SUM(b.shorts_count), 0) as total_shorts,
      COALESCE(SUM(b.long_form_count), 0) as total_longform,
      COALESCE(SUM(v.views), 0) as total_views
    FROM daily_batches b
    LEFT JOIN render_jobs r ON r.batch_id = b.id
    LEFT JOIN youtube_uploads u ON u.render_job_id = r.id
    LEFT JOIN (
      SELECT youtube_upload_id, MAX(views) as views
      FROM analytics_snapshots
      GROUP BY youtube_upload_id
    ) v ON v.youtube_upload_id = u.id
    WHERE b.date LIKE $1 AND b.channel_id = $2
  `, [`${currentMonthStr}%`, channelId]);

  const totalBatches = summaryRes[0]?.totalBatches || 0;
  const totalShorts = summaryRes[0]?.totalShorts || 0;
  const totalLongform = summaryRes[0]?.totalLongform || 0;
  const totalViews = summaryRes[0]?.totalViews || 0;

  const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const currentMonthName = monthNames[today.getMonth()];
  const currentYear = today.getFullYear();

  function formatViews(views: number): string {
    if (views >= 1000000) return `${(views / 1000000).toFixed(1)}M`;
    if (views >= 1000) return `${(views / 1000).toFixed(0)}K`;
    return String(views);
  }

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div>
          <h1 className="text-3xl font-bold gradient-text">Content Calendar</h1>
          <p className="mt-1 text-muted-foreground">Weekly content schedule and production status</p>
        </div>

        {/* Week View */}
        <div className="grid gap-4 grid-cols-2 md:grid-cols-7">
          {calendarData.map((day) => (
            <Card key={day.date} className={`border ${statusColor[day.status]} backdrop-blur-sm transition-all hover:shadow-lg`}>
              <CardHeader className="p-4 pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xs font-medium text-muted-foreground">{day.day}</CardTitle>
                  <span className="text-xs font-mono text-muted-foreground">{day.date.slice(5)}</span>
                </div>
              </CardHeader>
              <CardContent className="p-4 pt-0">
                {day.category ? (
                  <div className="space-y-3">
                    <Badge variant="outline" className="text-[10px] truncate max-w-full">
                      {day.category.replace("_", " ")}
                    </Badge>
                    <div className="space-y-1 text-xs text-muted-foreground">
                      <div className="flex justify-between">
                        <span>Shorts</span>
                        <span className="font-medium text-foreground">{day.shorts}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Long-form</span>
                        <span className="font-medium text-foreground">{day.longForm}</span>
                      </div>
                      {day.views > 0 && (
                        <div className="flex justify-between pt-1 border-t border-border/30">
                          <span>Views</span>
                          <span className="font-medium text-foreground">{day.views.toLocaleString()}</span>
                        </div>
                      )}
                    </div>
                    <Badge variant="outline" className={`text-[10px] ${
                      day.status === "completed" ? "bg-emerald-500/20 text-emerald-400" :
                      day.status === "uploaded" ? "bg-blue-500/20 text-blue-400" :
                      day.status === "rendering" ? "bg-yellow-500/20 text-yellow-400" : ""
                    }`}>
                      {day.status}
                    </Badge>
                  </div>
                ) : (
                  <div className="flex h-20 items-center justify-center text-xs text-muted-foreground italic">
                    Scheduled
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Monthly Overview */}
        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="text-lg">{currentMonthName} {currentYear} Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-6 sm:grid-cols-4">
              {[
                { label: "Total Batches", value: String(totalBatches), icon: "📦" },
                { label: "Shorts Created", value: String(totalShorts), icon: "📱" },
                { label: "Long-form Videos", value: String(totalLongform), icon: "🎬" },
                { label: "Total Views", value: formatViews(totalViews), icon: "👁" },
              ].map((stat) => (
                <div key={stat.label} className="text-center">
                  <span className="text-2xl">{stat.icon}</span>
                  <p className="mt-2 text-2xl font-bold">{stat.value}</p>
                  <p className="text-xs text-muted-foreground">{stat.label}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
