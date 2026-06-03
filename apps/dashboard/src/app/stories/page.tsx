import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

const policyColors: Record<string, string> = {
  clean: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  rewritten: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  flagged: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  blocked: "bg-red-500/20 text-red-400 border-red-500/30",
};

const categoryColors: Record<string, string> = {
  aita: "bg-violet-500/20 text-violet-400",
  revenge: "bg-red-500/20 text-red-400",
  workplace_drama: "bg-blue-500/20 text-blue-400",
  cheating: "bg-orange-500/20 text-orange-400",
  entitled_parents: "bg-yellow-500/20 text-yellow-400",
  relationships: "bg-pink-500/20 text-pink-400",
  family_drama: "bg-amber-500/20 text-amber-400",
  confessions: "bg-indigo-500/20 text-indigo-400",
  scary_stories: "bg-emerald-500/20 text-emerald-400",
  wholesome: "bg-green-500/20 text-green-400",
  customer_service_drama: "bg-cyan-500/20 text-cyan-400",
  mysteries: "bg-purple-500/20 text-purple-400",
  creepy_encounters: "bg-teal-500/20 text-teal-400",
};

function formatDuration(seconds: number): string {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

export default async function StoriesPage() {
  const channelId = await getActiveChannelId();
  // Query all stories for the active channel
  const stories = await query(`
    SELECT
      s.id,
      s.title,
      s.category,
      s.type,
      s.word_count,
      s.estimated_duration_seconds,
      s.voice_persona,
      s.policy_status,
      s.novelty_score,
      b.date
    FROM stories s
    JOIN daily_batches b ON s.batch_id = b.id
    WHERE s.channel_id = $1
    ORDER BY b.date DESC, s.created_at DESC
  `, [channelId]);

  // Query stats
  const statsRes = await query(`
    SELECT
      COUNT(*) as total,
      SUM(CASE WHEN policy_status IN ('clean', 'rewritten') THEN 1 ELSE 0 END) as safe_count,
      AVG(novelty_score) as avg_novelty
    FROM stories
    WHERE channel_id = $1
  `, [channelId]);

  const totalStories = statsRes[0]?.total || 0;
  const safeCount = statsRes[0]?.safeCount || 0;
  const avgNovelty = statsRes[0]?.avgNovelty || 0;

  const cleanRate = totalStories > 0 ? Math.round((safeCount / totalStories) * 100) : 100;

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div>
          <h1 className="text-3xl font-bold gradient-text">Story Library</h1>
          <p className="mt-1 text-muted-foreground">Browse and manage all generated stories</p>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          {[
            { label: "Total Stories", value: String(totalStories), icon: "📖" },
            { label: "Clean Rate", value: `${cleanRate}%`, icon: "✅" },
            { label: "Avg Novelty", value: avgNovelty ? Number(avgNovelty).toFixed(2) : "0.00", icon: "✨" },
          ].map((s) => (
            <Card key={s.label} className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardContent className="flex items-center gap-4 p-5">
                <span className="text-2xl">{s.icon}</span>
                <div>
                  <p className="text-2xl font-bold">{s.value}</p>
                  <p className="text-xs text-muted-foreground">{s.label}</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        <Tabs defaultValue="all">
          <TabsList className="bg-muted/50">
            <TabsTrigger value="all">All Stories</TabsTrigger>
            <TabsTrigger value="shorts">Shorts</TabsTrigger>
            <TabsTrigger value="longform">Long-form</TabsTrigger>
            <TabsTrigger value="flagged">Flagged / Blocked</TabsTrigger>
          </TabsList>

          <TabsContent value="all" className="mt-6">
            <div className="space-y-3">
              {stories.length > 0 ? (
                stories.map((story: any) => (
                  <Card key={story.id} className="border-border/30 bg-card/30 backdrop-blur-sm transition-all hover:border-primary/20 hover:bg-accent/20">
                    <CardContent className="p-5">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 space-y-2">
                          <h3 className="font-medium leading-tight">{story.title}</h3>
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="outline" className={`text-[10px] ${categoryColors[story.category] || ""}`}>
                              {story.category.replace("_", " ")}
                            </Badge>
                            <Badge variant="outline" className="text-[10px]">
                              {story.type === "short" ? "📱 Short" : "🎬 Long-form"}
                            </Badge>
                            <Badge variant="outline" className="text-[10px]">
                              🎙 {story.voicePersona}
                            </Badge>
                            <Badge variant="outline" className={`text-[10px] ${policyColors[story.policyStatus]}`}>
                              {story.policyStatus}
                            </Badge>
                          </div>
                        </div>
                        <div className="shrink-0 text-right text-xs text-muted-foreground space-y-1">
                          <div>{story.wordCount} words</div>
                          <div>{formatDuration(story.estimatedDurationSeconds)}</div>
                          <div>Novelty: {Number(story.noveltyScore).toFixed(2)}</div>
                          <div className="font-mono">{story.date}</div>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))
              ) : (
                <Card className="border-border/30 bg-card/30 p-8 text-center text-muted-foreground">
                  No stories found. Run the content pipeline first!
                </Card>
              )}
            </div>
          </TabsContent>

          <TabsContent value="shorts" className="mt-6">
            <div className="space-y-3">
              {stories.filter((s: any) => s.type === "short").length > 0 ? (
                stories.filter((s: any) => s.type === "short").map((story: any) => (
                  <Card key={story.id} className="border-border/30 bg-card/30 backdrop-blur-sm p-5 hover:border-primary/20">
                    <h3 className="font-medium">{story.title}</h3>
                    <div className="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
                      <Badge variant="outline" className={categoryColors[story.category]}>{story.category.replace("_", " ")}</Badge>
                      <span>{story.wordCount} words</span>
                      <span>·</span>
                      <span>{formatDuration(story.estimatedDurationSeconds)}</span>
                      <span>·</span>
                      <span>🎙 {story.voicePersona}</span>
                    </div>
                  </Card>
                ))
              ) : (
                <Card className="border-border/30 bg-card/30 p-8 text-center text-muted-foreground">
                  No short stories found.
                </Card>
              )}
            </div>
          </TabsContent>

          <TabsContent value="longform" className="mt-6">
            <div className="space-y-3">
              {stories.filter((s: any) => s.type !== "short").length > 0 ? (
                stories.filter((s: any) => s.type !== "short").map((story: any) => (
                  <Card key={story.id} className="border-border/30 bg-card/30 backdrop-blur-sm p-5 hover:border-primary/20">
                    <h3 className="font-medium">{story.title}</h3>
                    <div className="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
                      <Badge variant="outline" className={categoryColors[story.category]}>{story.category.replace("_", " ")}</Badge>
                      <span>{story.wordCount} words</span>
                      <span>·</span>
                      <span>{formatDuration(story.estimatedDurationSeconds)}</span>
                      <span>·</span>
                      <span>🎙 {story.voicePersona}</span>
                    </div>
                  </Card>
                ))
              ) : (
                <Card className="border-border/30 bg-card/30 p-8 text-center text-muted-foreground">
                  No long-form stories found.
                </Card>
              )}
            </div>
          </TabsContent>

          <TabsContent value="flagged" className="mt-6">
            <div className="space-y-3">
              {stories.filter((s: any) => s.policyStatus === "flagged" || s.policyStatus === "blocked" || s.policyStatus === "rewritten").length > 0 ? (
                stories.filter((s: any) => s.policyStatus === "flagged" || s.policyStatus === "blocked" || s.policyStatus === "rewritten").map((story: any) => (
                  <Card key={story.id} className="border-border/30 bg-card/30 backdrop-blur-sm p-5 border-yellow-500/20">
                    <div className="flex justify-between items-start">
                      <h3 className="font-medium">{story.title}</h3>
                      <Badge variant="outline" className={policyColors[story.policyStatus]}>{story.policyStatus}</Badge>
                    </div>
                    <div className="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
                      <Badge variant="outline" className={categoryColors[story.category]}>{story.category.replace("_", " ")}</Badge>
                      <span>{story.wordCount} words</span>
                      <span>·</span>
                      <span>{formatDuration(story.estimatedDurationSeconds)}</span>
                    </div>
                  </Card>
                ))
              ) : (
                <Card className="border-border/30 bg-card/30 p-8 text-center">
                  <p className="text-muted-foreground">No flagged or blocked stories at this time ✨</p>
                </Card>
              )}
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}
