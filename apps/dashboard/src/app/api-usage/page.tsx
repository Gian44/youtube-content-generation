import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

const providerColors: Record<string, string> = {
  openai: "bg-green-500/20 text-green-400 border-green-500/30",
  gemini: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  youtube: "bg-red-500/20 text-red-400 border-red-500/30",
  pexels: "bg-teal-500/20 text-teal-400 border-teal-500/30",
  pixabay: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
};

const mockUsage = [
  { provider: "openai", endpoint: "chat.completions/gpt-4o", calls: 30, tokens: 16409, cost: 0.08 },
  { provider: "pexels", endpoint: "videos/search", calls: 20, tokens: 0, cost: 0 },
  { provider: "openai", endpoint: "audio.speech/tts-1", calls: 10, tokens: 0, cost: 0.27 },
  { provider: "openai", endpoint: "audio.transcriptions/whisper-1", calls: 10, tokens: 0, cost: 0.06 },
  { provider: "youtube", endpoint: "videos.insert", calls: 9, tokens: 14400, cost: 0 },
  { provider: "youtube", endpoint: "thumbnails.set", calls: 2, tokens: 100, cost: 0 },
  { provider: "youtube", endpoint: "videos.list", calls: 1, tokens: 15, cost: 0 },
];

export default async function ApiUsagePage() {
  const todayDateStr = new Date().toISOString().slice(0, 10);
  const channelId = await getActiveChannelId();

  // Query live API stats for today (active channel)
  const statsRes = await query(`
    SELECT
      COUNT(*) as total_calls,
      COALESCE(SUM(tokens_used), 0) as total_tokens,
      COALESCE(SUM(cost_estimate), 0) as total_cost,
      COALESCE(SUM(CASE WHEN provider = 'youtube' THEN tokens_used ELSE 0 END), 0) as youtube_quota
    FROM api_usage_logs
    WHERE date(created_at) = $1 AND channel_id = $2
  `, [todayDateStr, channelId]);

  const liveCalls = statsRes[0]?.totalCalls || 0;
  const liveTokens = statsRes[0]?.totalTokens || 0;
  const liveCost = statsRes[0]?.totalCost || 0;
  const liveYtQuota = statsRes[0]?.youtubeQuota || 0;

  // If no logs exist, we fall back to demo mode
  const totalCallsRes = await query(
    "SELECT COUNT(*) as count FROM api_usage_logs WHERE channel_id = $1",
    [channelId]
  );
  const hasLogs = (totalCallsRes[0]?.count || 0) > 0;
  const isDemoMode = !hasLogs;

  // Query grouped API usage
  const dbUsageRes = await query(`
    SELECT 
      provider,
      endpoint,
      COUNT(*) as calls,
      SUM(COALESCE(tokens_used, 0)) as tokens,
      SUM(COALESCE(cost_estimate, 0)) as cost
    FROM api_usage_logs
    WHERE date(created_at) = $1 AND channel_id = $2
    GROUP BY provider, endpoint
    ORDER BY calls DESC
  `, [todayDateStr, channelId]);

  // Map to list
  const usageList = isDemoMode ? mockUsage : dbUsageRes.map((r: any) => ({
    provider: r.provider,
    endpoint: r.endpoint,
    calls: Number(r.calls),
    tokens: Number(r.tokens),
    cost: Number(r.cost),
  }));

  const displayCalls = isDemoMode ? 82 : liveCalls;
  const displayTokens = isDemoMode ? 31000 : liveTokens;
  const displayCost = isDemoMode ? 0.41 : liveCost;
  const displayYtQuota = isDemoMode ? 14515 : liveYtQuota;

  const ytQuotaPercentage = Math.round((displayYtQuota / 10000) * 100);
  const uploadsRemaining = Math.max(0, Math.floor((10000 - displayYtQuota) / 1600));

  // Compute provider specific lists and stats
  const ytList = usageList.filter((item) => item.provider === "youtube");
  const ytCalls = ytList.reduce((acc, item) => acc + item.calls, 0);
  const ytUnits = ytList.reduce((acc, item) => acc + item.tokens, 0);

  const openaiList = usageList.filter((item) => item.provider === "openai");
  const openaiCalls = openaiList.reduce((acc, item) => acc + item.calls, 0);
  const openaiTokens = openaiList.reduce((acc, item) => acc + item.tokens, 0);
  const openaiCost = openaiList.reduce((acc, item) => acc + item.cost, 0);

  const geminiList = usageList.filter((item) => item.provider === "gemini");
  const geminiCalls = geminiList.reduce((acc, item) => acc + item.calls, 0);
  const geminiTokens = geminiList.reduce((acc, item) => acc + item.tokens, 0);
  const geminiCost = geminiList.reduce((acc, item) => acc + item.cost, 0);

  const othersList = usageList.filter(
    (item) => item.provider !== "youtube" && item.provider !== "openai" && item.provider !== "gemini"
  );
  const othersCalls = othersList.reduce((acc, item) => acc + item.calls, 0);

  function formatCost(cost: number, provider: string): string {
    if (provider === "pexels" || provider === "pixabay") return "Free";
    if (cost === 0) return "—";
    return `$${cost.toFixed(2)}`;
  }

  function formatTokens(tokens: number, provider: string): string {
    if (provider === "youtube") return `${tokens.toLocaleString()} units`;
    if (tokens === 0) return "—";
    return tokens.toLocaleString();
  }

  function renderYoutubeQuotaCard() {
    return (
      <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
        <CardHeader>
          <CardTitle className="text-lg">YouTube API Quota</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>{displayYtQuota.toLocaleString()} / 10,000 units used</span>
              <span className="text-muted-foreground">Resets at midnight PT</span>
            </div>
            <Progress value={ytQuotaPercentage} className="h-3" />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{uploadsRemaining > 0 ? `~${uploadsRemaining} more uploads possible today` : "~0 more uploads possible today"}</span>
              <span>{displayYtQuota > 10000 ? `-${(displayYtQuota - 10000).toLocaleString()} units remaining` : `${(10000 - displayYtQuota).toLocaleString()} units remaining`}</span>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  function renderTable(filteredList: typeof usageList, title: string) {
    return (
      <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
        <CardHeader>
          <CardTitle className="text-lg">{title}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {filteredList.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent border-border/50">
                  <TableHead>Provider</TableHead>
                  <TableHead>Endpoint</TableHead>
                  <TableHead>Calls</TableHead>
                  <TableHead>Tokens/Units</TableHead>
                  <TableHead>Est. Cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredList.map((row, i) => (
                  <TableRow key={i} className="border-border/30 hover:bg-accent/20">
                    <TableCell>
                      <Badge variant="outline" className={`text-[10px] capitalize ${providerColors[row.provider] || "bg-gray-500/20 text-gray-400"}`}>
                        {row.provider}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.endpoint}</TableCell>
                    <TableCell>{row.calls}</TableCell>
                    <TableCell>{formatTokens(row.tokens, row.provider)}</TableCell>
                    <TableCell className="font-medium">{formatCost(row.cost, row.provider)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="p-8 text-center text-muted-foreground text-sm">
              No API calls logged today.
            </div>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold gradient-text">API Usage</h1>
            <p className="mt-1 text-muted-foreground">Track API calls, token usage, and costs</p>
          </div>
          {isDemoMode && (
            <Badge variant="outline" className="bg-yellow-500/20 text-yellow-400 border-yellow-500/30">
              💡 Demo Mode: Mock Data Shown
            </Badge>
          )}
        </div>

        {/* Global summary stats */}
        <div className="grid gap-4 sm:grid-cols-4">
          {[
            { label: "Total Calls Today", value: String(displayCalls), icon: "📡" },
            { label: "Tokens/Units Used", value: displayTokens >= 1000 ? `${(displayTokens / 1000).toFixed(0)}K` : String(displayTokens), icon: "🔤" },
            { label: "Est. Cost Today", value: `$${displayCost.toFixed(2)}`, icon: "💰" },
            { label: "YT Quota Used", value: `${ytQuotaPercentage}%`, icon: "📊" },
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

        {/* Interactive Tabs */}
        <Tabs defaultValue="all" className="w-full space-y-6">
          <TabsList className="bg-muted/50 border border-border/40 p-1">
            <TabsTrigger value="all">📊 All APIs</TabsTrigger>
            <TabsTrigger value="youtube">🟥 YouTube</TabsTrigger>
            <TabsTrigger value="openai">🟩 OpenAI</TabsTrigger>
            <TabsTrigger value="gemini">🟦 Gemini</TabsTrigger>
            <TabsTrigger value="others">🔌 Others</TabsTrigger>
          </TabsList>

          <TabsContent value="all" className="space-y-6 mt-0">
            {renderYoutubeQuotaCard()}
            {renderTable(usageList, isDemoMode ? "Sample API Calls" : "Today's API Calls")}
          </TabsContent>

          <TabsContent value="youtube" className="space-y-6 mt-0">
            {renderYoutubeQuotaCard()}
            {renderTable(ytList, "YouTube API Calls Detail")}
          </TabsContent>

          <TabsContent value="openai" className="space-y-6 mt-0">
            <div className="grid gap-4 sm:grid-cols-3">
              {[
                { label: "OpenAI Total Calls", value: String(openaiCalls), icon: "📡" },
                { label: "OpenAI Tokens Used", value: openaiTokens.toLocaleString(), icon: "🔤" },
                { label: "OpenAI Est. Cost Today", value: formatCost(openaiCost, "openai"), icon: "💰" },
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
            {renderTable(openaiList, "OpenAI API Calls Detail")}
          </TabsContent>

          <TabsContent value="gemini" className="space-y-6 mt-0">
            <div className="grid gap-4 sm:grid-cols-3">
              {[
                { label: "Gemini Total Calls", value: String(geminiCalls), icon: "📡" },
                { label: "Gemini Tokens Used", value: geminiTokens.toLocaleString(), icon: "🔤" },
                { label: "Gemini Est. Cost Today", value: formatCost(geminiCost, "gemini"), icon: "💰" },
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
            {renderTable(geminiList, "Gemini API Calls Detail")}
          </TabsContent>

          <TabsContent value="others" className="space-y-6 mt-0">
            <div className="grid gap-4 sm:grid-cols-2">
              {[
                { label: "Stock APIs Total Calls", value: String(othersCalls), icon: "📡" },
                { label: "Total Stock API Cost", value: "Free", icon: "💰" },
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
            {renderTable(othersList, "Other API Calls Detail")}
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}
