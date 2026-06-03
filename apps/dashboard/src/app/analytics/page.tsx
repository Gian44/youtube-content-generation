import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

// Mock fallbacks for beautiful display if DB has no analytics yet
const mockByCategory = [
  { category: "aita", videos: 42, views: 156000, likes: 11200, ctr: 8.2 },
  { category: "workplace_drama", videos: 38, views: 134000, likes: 9800, ctr: 7.9 },
  { category: "revenge", videos: 35, views: 128000, likes: 10100, ctr: 9.1 },
  { category: "cheating", videos: 28, views: 98000, likes: 7200, ctr: 7.4 },
  { category: "entitled_parents", videos: 24, views: 89000, likes: 6800, ctr: 8.5 },
  { category: "scary_stories", videos: 18, views: 82000, likes: 6100, ctr: 10.2 },
  { category: "family_drama", videos: 20, views: 67000, likes: 4900, ctr: 6.8 },
];

const mockByVoice = [
  { voice: "dramatic", videos: 62, avgViews: 3200, avgLikes: 240 },
  { voice: "sarcastic", videos: 48, avgViews: 2800, avgLikes: 210 },
  { voice: "calm", videos: 44, avgViews: 2600, avgLikes: 190 },
  { voice: "confession", videos: 28, avgViews: 3500, avgLikes: 280 },
  { voice: "horror", videos: 22, avgViews: 4100, avgLikes: 320 },
  { voice: "warm", videos: 30, avgViews: 2400, avgLikes: 180 },
];

const mockWeekly = [
  { week: "May 4", shorts: 42000, longForm: 18000 },
  { week: "May 11", shorts: 51000, longForm: 22000 },
  { week: "May 18", shorts: 48000, longForm: 19000 },
  { week: "May 25", shorts: 56000, longForm: 25000 },
];

export default async function AnalyticsPage() {
  const channelId = await getActiveChannelId();

  // Query summary stats from snapshots (scoped to the active channel's uploads)
  const summaryRes = await query(`
    SELECT
      COALESCE(SUM(views), 0) as total_views,
      COALESCE(SUM(likes), 0) as total_likes,
      COALESCE(SUM(comments), 0) as total_comments,
      COALESCE(SUM(subscribers_gained), 0) as total_subs,
      COALESCE(AVG(ctr), 0) as avg_ctr
    FROM (
      SELECT a.youtube_upload_id,
             MAX(a.views) as views, MAX(a.likes) as likes, MAX(a.comments) as comments,
             MAX(a.subscribers_gained) as subscribers_gained, MAX(a.ctr) as ctr
      FROM analytics_snapshots a
      JOIN youtube_uploads u ON a.youtube_upload_id = u.id
      WHERE u.channel_id = $1
      GROUP BY a.youtube_upload_id
    ) v
  `, [channelId]);

  const dbViews = summaryRes[0]?.totalViews || 0;
  const dbLikes = summaryRes[0]?.totalLikes || 0;
  const dbComments = summaryRes[0]?.totalComments || 0;
  const dbSubs = summaryRes[0]?.totalSubs || 0;
  const dbCtr = summaryRes[0]?.avgCtr || 0;

  const isDemoMode = dbViews === 0;

  // Query performance by category
  const dbCategoryRes = await query(`
    SELECT 
      b.category,
      COUNT(DISTINCT u.id) as videos,
      COALESCE(SUM(v.views), 0) as views,
      COALESCE(SUM(v.likes), 0) as likes,
      COALESCE(AVG(v.ctr), 0) as ctr
    FROM daily_batches b
    JOIN render_jobs r ON r.batch_id = b.id
    JOIN youtube_uploads u ON u.render_job_id = r.id
    LEFT JOIN (
      SELECT youtube_upload_id, MAX(views) as views, MAX(likes) as likes, MAX(ctr) as ctr
      FROM analytics_snapshots
      GROUP BY youtube_upload_id
    ) v ON v.youtube_upload_id = u.id
    WHERE u.status IN ('completed', 'uploaded') AND u.channel_id = $1
    GROUP BY b.category
    ORDER BY views DESC
  `, [channelId]);

  // Query performance by voice
  const dbVoiceRes = await query(`
    SELECT 
      s.voice_persona as voice,
      COUNT(DISTINCT u.id) as videos,
      COALESCE(AVG(v.views), 0) as avg_views,
      COALESCE(AVG(v.likes), 0) as avg_likes
    FROM stories s
    JOIN render_jobs r ON r.story_ids LIKE '%' || s.id || '%'
    JOIN youtube_uploads u ON u.render_job_id = r.id
    LEFT JOIN (
      SELECT youtube_upload_id, MAX(views) as views, MAX(likes) as likes
      FROM analytics_snapshots
      GROUP BY youtube_upload_id
    ) v ON v.youtube_upload_id = u.id
    WHERE u.status IN ('completed', 'uploaded') AND u.channel_id = $1
    GROUP BY s.voice_persona
    ORDER BY avg_views DESC
  `, [channelId]);

  // Map database results or fall back to mock
  const categoryData = isDemoMode ? mockByCategory : dbCategoryRes.map((c: any) => ({
    category: c.category || "unknown",
    videos: Number(c.videos),
    views: Number(c.views),
    likes: Number(c.likes),
    ctr: Number(c.ctr),
  }));

  const voiceData = isDemoMode ? mockByVoice : dbVoiceRes.map((v: any) => ({
    voice: v.voice || "unknown",
    videos: Number(v.videos),
    avgViews: Number(v.avgViews),
    avgLikes: Number(v.avgLikes),
  }));

  const maxCategoryViews = Math.max(...categoryData.map(c => c.views), 1);

  function formatValue(val: number): string {
    if (val >= 1000000) return `${(val / 1000000).toFixed(1)}M`;
    if (val >= 1000) return `${(val / 1000).toFixed(0)}K`;
    return String(val);
  }

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold gradient-text">Analytics</h1>
            <p className="mt-1 text-muted-foreground">Content performance insights and trends</p>
          </div>
          {isDemoMode && (
            <Badge variant="outline" className="bg-yellow-500/20 text-yellow-400 border-yellow-500/30">
              💡 Demo Mode: Mock Data Shown
            </Badge>
          )}
        </div>

        {/* Summary Stats */}
        <div className="grid gap-4 sm:grid-cols-5">
          {[
            { label: "Total Views", value: formatValue(isDemoMode ? 754000 : dbViews), icon: "👁", change: isDemoMode ? "+12%" : "Live views" },
            { label: "Total Likes", value: formatValue(isDemoMode ? 56000 : dbLikes), icon: "❤️", change: isDemoMode ? "+8%" : "Live likes" },
            { label: "Comments", value: formatValue(isDemoMode ? 12000 : dbComments), icon: "💬", change: isDemoMode ? "+15%" : "Live comments" },
            { label: "Subscribers", value: isDemoMode ? "+1.2K" : `+${dbSubs}`, icon: "👤", change: isDemoMode ? "+23%" : "Live subscribers" },
            { label: "Avg CTR", value: `${(isDemoMode ? 8.1 : dbCtr).toFixed(1)}%`, icon: "🎯", change: isDemoMode ? "+1.2%" : "Click-through rate" },
          ].map((s) => (
            <Card key={s.label} className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardContent className="p-4 text-center">
                <span className="text-lg">{s.icon}</span>
                <p className="text-2xl font-bold mt-1">{s.value}</p>
                <p className="text-xs text-muted-foreground">{s.label}</p>
                <p className="text-xs text-emerald-400 mt-1">{s.change}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        <Tabs defaultValue="category">
          <TabsList className="bg-muted/50">
            <TabsTrigger value="category">By Category</TabsTrigger>
            <TabsTrigger value="voice">By Voice</TabsTrigger>
            <TabsTrigger value="weekly">Weekly Trend</TabsTrigger>
          </TabsList>

          <TabsContent value="category" className="mt-6">
            <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="text-lg">Performance by Category</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {categoryData.map((cat) => (
                    <div key={cat.category} className="flex items-center gap-4">
                      <div className="w-36">
                        <Badge variant="outline" className="text-xs">
                          {cat.category.replace("_", " ")}
                        </Badge>
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <div className="h-3 rounded-full bg-gradient-to-r from-primary/60 to-primary" style={{ width: `${(cat.views / maxCategoryViews) * 100}%` }} />
                        </div>
                      </div>
                      <div className="flex items-center gap-6 text-xs shrink-0">
                        <span className="w-16 text-right">{formatValue(cat.views)} views</span>
                        <span className="w-14 text-right text-muted-foreground">{formatValue(cat.likes)} ❤️</span>
                        <span className="w-12 text-right text-muted-foreground">{cat.ctr.toFixed(1)}% CTR</span>
                        <span className="w-10 text-right text-muted-foreground">{cat.videos} vids</span>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="voice" className="mt-6">
            <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="text-lg">Performance by Voice Persona</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 sm:grid-cols-3">
                  {voiceData.map((v) => (
                    <Card key={v.voice} className="border-border/30 bg-background/50">
                      <CardContent className="p-4">
                        <div className="flex items-center gap-2 mb-3">
                          <span className="text-lg">🎙</span>
                          <span className="font-medium capitalize">{v.voice}</span>
                        </div>
                        <div className="space-y-1 text-xs text-muted-foreground">
                          <div className="flex justify-between"><span>Videos</span><span className="text-foreground">{v.videos}</span></div>
                          <div className="flex justify-between"><span>Avg Views</span><span className="text-foreground">{v.avgViews.toLocaleString()}</span></div>
                          <div className="flex justify-between"><span>Avg Likes</span><span className="text-foreground">{v.avgLikes.toFixed(0)}</span></div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="weekly" className="mt-6">
            <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="text-lg">Weekly View Trends</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-6">
                  {mockWeekly.map((week) => (
                    <div key={week.week} className="space-y-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium">{week.week}</span>
                        <span className="text-muted-foreground">{((week.shorts + week.longForm) / 1000).toFixed(0)}K total</span>
                      </div>
                      <div className="flex gap-1 h-6">
                        <div className="h-full rounded-l bg-primary/60" style={{ width: `${(week.shorts / 80000) * 100}%` }} />
                        <div className="h-full rounded-r bg-chart-2/60" style={{ width: `${(week.longForm / 80000) * 100}%` }} />
                      </div>
                      <div className="flex gap-4 text-xs text-muted-foreground">
                        <span>📱 Shorts: {formatValue(week.shorts)}</span>
                        <span>🎬 Long-form: {formatValue(week.longForm)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}
