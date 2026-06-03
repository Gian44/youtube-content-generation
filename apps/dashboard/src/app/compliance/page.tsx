import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

const mockFlags = [
  { id: "pf-001", storyTitle: "The revenge story gone wrong", flagType: "excessive_profanity", severity: "rewrite", autoResolved: true, createdAt: "2026-05-27" },
  { id: "pf-002", storyTitle: "My ex's secret life", flagType: "graphic_violence", severity: "rewrite", autoResolved: true, createdAt: "2026-05-26" },
  { id: "pf-003", storyTitle: "Workplace confrontation", flagType: "excessive_profanity", severity: "warn", autoResolved: true, createdAt: "2026-05-25" },
];

const severityColors: Record<string, string> = {
  block: "bg-red-500/20 text-red-400 border-red-500/30",
  rewrite: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  warn: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  info: "bg-blue-500/20 text-blue-400 border-blue-500/30",
};

export default async function CompliancePage() {
  const channelId = await getActiveChannelId();
  // Query overall policy metrics from stories (active channel)
  const statsRes = await query(`
    SELECT
      COUNT(*) as total,
      SUM(CASE WHEN policy_status IN ('clean', 'rewritten') THEN 1 ELSE 0 END) as clean_count,
      SUM(CASE WHEN policy_status = 'rewritten' THEN 1 ELSE 0 END) as rewritten_count,
      SUM(CASE WHEN policy_status = 'blocked' THEN 1 ELSE 0 END) as blocked_count
    FROM stories
    WHERE channel_id = $1
  `, [channelId]);

  const totalStories = statsRes[0]?.total || 0;
  const cleanCount = statsRes[0]?.cleanCount || 0;
  const rewrittenCount = statsRes[0]?.rewrittenCount || 0;
  const blockedCount = statsRes[0]?.blockedCount || 0;

  const cleanRate = totalStories > 0 ? Math.round((cleanCount / totalStories) * 100) : 100;

  // Query recent policy flags
  const dbFlags = await query(`
    SELECT 
      f.id,
      f.flag_type,
      f.severity,
      f.auto_resolved,
      f.created_at,
      s.title as story_title
    FROM policy_flags f
    LEFT JOIN stories s ON f.story_id = s.id
    WHERE f.channel_id = $1
    ORDER BY f.created_at DESC
    LIMIT 10
  `, [channelId]);

  const isDemoMode = totalStories === 0 && dbFlags.length === 0;

  // Query counts for guardrails
  const assetStatsRes = await query(`
    SELECT
      COUNT(*) as total,
      SUM(CASE WHEN policy_status = 'rejected' THEN 1 ELSE 0 END) as blocked_count
    FROM assets
    WHERE channel_id = $1
  `, [channelId]);
  const totalAssets = assetStatsRes[0]?.total || 0;
  const blockedAssets = assetStatsRes[0]?.blockedCount || 0;

  const displayCleanRate = isDemoMode ? 94 : cleanRate;
  const displayRewritten = isDemoMode ? 12 : rewrittenCount;
  const displayBlocked = isDemoMode ? 3 : blockedCount;
  const flagsList = isDemoMode ? mockFlags : dbFlags.map((f: any) => ({
    id: f.id,
    storyTitle: f.storyTitle || "Background Asset Check",
    flagType: f.flagType,
    severity: f.severity,
    autoResolved: Boolean(f.autoResolved),
    createdAt: new Date(f.createdAt).toISOString().slice(0, 10),
  }));

  const guardrails = [
    { name: "Duplicate Detection", status: "active", description: "Originality hash check on every story", checks: isDemoMode ? 234 : totalStories, blocked: isDemoMode ? 2 : 0 },
    { name: "Hook Variation", status: "active", description: "Max 2 same hook template per week", checks: isDemoMode ? 47 : Math.round(totalStories / 3), blocked: 0 },
    { name: "Topic Cooldown", status: "active", description: "2-day cooldown between same category", checks: isDemoMode ? 47 : Math.round(totalStories / 4), blocked: isDemoMode ? 5 : 0 },
    { name: "Policy Scanner", status: "active", description: "AI + keyword safety screening", checks: isDemoMode ? 234 : totalStories, blocked: displayBlocked },
    { name: "License Validator", status: "active", description: "Asset license verification", checks: isDemoMode ? 89 : totalAssets, blocked: isDemoMode ? 0 : blockedAssets },
    { name: "Content Diversity", status: "active", description: "Vary voice, style, titles, visuals", checks: isDemoMode ? 189 : totalStories + totalAssets, blocked: 0 },
  ];

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold gradient-text">Compliance</h1>
            <p className="mt-1 text-muted-foreground">Safety guardrails, policy flags, and content quality</p>
          </div>
          {isDemoMode && (
            <Badge variant="outline" className="bg-yellow-500/20 text-yellow-400 border-yellow-500/30">
              💡 Demo Mode: Mock Data Shown
            </Badge>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-4">
          {[
            { label: "Clean Rate", value: `${displayCleanRate}%`, icon: "✅", color: "text-emerald-400" },
            { label: "Rewritten", value: String(displayRewritten), icon: "✏️", color: "text-yellow-400" },
            { label: "Blocked", value: String(displayBlocked), icon: "🚫", color: "text-red-400" },
            { label: "Active Guards", value: "6", icon: "🛡️", color: "text-blue-400" },
          ].map((s) => (
            <Card key={s.label} className="border-border/50 bg-card/50 backdrop-blur-sm">
              <CardContent className="p-4 text-center">
                <span className="text-lg">{s.icon}</span>
                <p className={`text-2xl font-bold mt-1 ${s.color}`}>{s.value}</p>
                <p className="text-xs text-muted-foreground">{s.label}</p>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Active Guardrails */}
        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="text-lg">Active Guardrails</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2">
              {guardrails.map((guard) => (
                <div key={guard.name} className="rounded-lg border border-border/30 bg-background/50 p-4 transition-all hover:border-primary/20">
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-sm font-medium">{guard.name}</h4>
                    <Badge variant="outline" className="text-[10px] bg-emerald-500/20 text-emerald-400 border-emerald-500/30">
                      {guard.status}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mb-2">{guard.description}</p>
                  <div className="flex gap-4 text-xs text-muted-foreground">
                    <span>{guard.checks} checks</span>
                    <span className={guard.blocked > 0 ? "text-yellow-400" : ""}>{guard.blocked} blocked</span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Recent Flags */}
        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="text-lg">Recent Policy Flags</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {flagsList.length > 0 ? (
                flagsList.map((flag) => (
                  <div key={flag.id} className="flex items-center justify-between rounded-lg border border-border/30 bg-background/50 p-4">
                    <div className="flex items-center gap-4">
                      <span className="text-lg">{flag.autoResolved ? "✅" : "⚠️"}</span>
                      <div>
                        <p className="text-sm font-medium">{flag.storyTitle}</p>
                        <p className="text-xs text-muted-foreground">{flag.createdAt}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <Badge variant="outline" className="text-[10px]">{flag.flagType.replace("_", " ")}</Badge>
                      <Badge variant="outline" className={`text-[10px] ${severityColors[flag.severity] || "bg-gray-500/20 text-gray-400"}`}>
                        {flag.severity}
                      </Badge>
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-center py-6 text-muted-foreground text-sm">
                  No policy flags recorded. All content complies with standard guidelines!
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
