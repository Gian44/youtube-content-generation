import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { query } from "@/lib/db";
import { getActiveChannelId } from "@/lib/channels";

export const dynamic = 'force-dynamic';

const providerColors: Record<string, string> = {
  pexels: "bg-green-500/20 text-green-400 border-green-500/30",
  pixabay: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  local: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  mixkit: "bg-orange-500/20 text-orange-400 border-orange-500/30",
};

const mockAssets = [
  { id: "a-001", provider: "pexels", type: "video", query: "neon tunnel", creator: "cottonbro studio", license: "Pexels License", durationSeconds: 32, width: 1920, height: 1080, usageCount: 8, policyStatus: "verified" },
  { id: "a-002", provider: "pexels", type: "video", query: "ocean waves", creator: "Taryn Elliott", license: "Pexels License", durationSeconds: 45, width: 3840, height: 2160, usageCount: 12, policyStatus: "verified" },
  { id: "a-003", provider: "pixabay", type: "video", query: "space nebula", creator: "SpaceX", license: "Pixabay License", durationSeconds: 28, width: 1920, height: 1080, usageCount: 5, policyStatus: "verified" },
  { id: "a-004", provider: "local", type: "video", query: "abstract arcade", creator: "Local Upload", license: "User-provided", durationSeconds: 60, width: 1080, height: 1920, usageCount: 15, policyStatus: "verified" },
  { id: "a-005", provider: "pexels", type: "video", query: "train tracks", creator: "Vova Kras", license: "Pexels License", durationSeconds: 38, width: 1920, height: 1080, usageCount: 3, policyStatus: "verified" },
  { id: "a-006", provider: "pixabay", type: "video", query: "aurora borealis", creator: "NordArt", license: "Pixabay License", durationSeconds: 52, width: 3840, height: 2160, usageCount: 7, policyStatus: "verified" },
];

export default async function AssetsPage() {
  const channelId = await getActiveChannelId();
  // Query asset stats for the active channel
  const statsRes = await query(`
    SELECT
      COUNT(*) as total_assets,
      SUM(CASE WHEN policy_status = 'verified' THEN 1 ELSE 0 END) as verified_count,
      COUNT(DISTINCT provider) as providers_count,
      SUM(COALESCE(usage_count, 0)) as total_usage
    FROM assets
    WHERE channel_id = $1
  `, [channelId]);

  const totalAssets = statsRes[0]?.totalAssets || 0;
  const verified = statsRes[0]?.verifiedCount || 0;
  const providers = statsRes[0]?.providersCount || 0;
  const totalUsage = statsRes[0]?.totalUsage || 0;

  const isDemoMode = totalAssets === 0;

  // Query assets list
  const dbAssets = await query(`
    SELECT 
      id,
      provider,
      type,
      source_query as "query",
      creator,
      license,
      duration_seconds,
      width,
      height,
      usage_count,
      policy_status
    FROM assets
    WHERE channel_id = $1
    ORDER BY created_at DESC
  `, [channelId]);

  const assetsList = isDemoMode ? mockAssets : dbAssets.map((a: any) => ({
    id: a.id,
    provider: a.provider,
    type: a.type,
    query: a.query || "n/a",
    creator: a.creator || "unknown",
    license: a.license || "unknown",
    durationSeconds: Number(a.durationSeconds || 0),
    width: Number(a.width || 0),
    height: Number(a.height || 0),
    usageCount: Number(a.usageCount || 0),
    policyStatus: a.policyStatus,
  }));

  const displayTotal = isDemoMode ? 89 : totalAssets;
  const displayVerified = isDemoMode ? 84 : verified;
  const displayProviders = isDemoMode ? 3 : providers;
  const displayUsage = isDemoMode ? 347 : totalUsage;

  function formatDuration(seconds: number): string {
    if (!seconds) return "—";
    return `${Math.round(seconds)}s`;
  }

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold gradient-text">Asset Library</h1>
            <p className="mt-1 text-muted-foreground">Licensed background footage and media assets</p>
          </div>
          {isDemoMode && (
            <Badge variant="outline" className="bg-yellow-500/20 text-yellow-400 border-yellow-500/30">
              💡 Demo Mode: Mock Data Shown
            </Badge>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-4">
          {[
            { label: "Total Assets", value: String(displayTotal), icon: "🎬" },
            { label: "Verified", value: String(displayVerified), icon: "✅" },
            { label: "Providers", value: String(displayProviders), icon: "🌐" },
            { label: "Total Usage", value: String(displayUsage), icon: "🔄" },
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

        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent border-border/50">
                  <TableHead>ID</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Search Query</TableHead>
                  <TableHead>Creator</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Resolution</TableHead>
                  <TableHead>Usage</TableHead>
                  <TableHead>License</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {assetsList.length > 0 ? (
                  assetsList.map((asset) => (
                    <TableRow key={asset.id} className="border-border/30 hover:bg-accent/20">
                      <TableCell className="font-mono text-xs text-muted-foreground">{asset.id.slice(0, 8)}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`text-[10px] capitalize ${providerColors[asset.provider] || "bg-gray-500/20 text-gray-400"}`}>
                          {asset.provider}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm truncate max-w-[120px]">{asset.query}</TableCell>
                      <TableCell className="text-sm text-muted-foreground max-w-[120px] truncate">{asset.creator}</TableCell>
                      <TableCell className="text-sm">{formatDuration(asset.durationSeconds)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {asset.width && asset.height ? `${asset.width}x${asset.height}` : "—"}
                      </TableCell>
                      <TableCell className="text-sm">{asset.usageCount}×</TableCell>
                      <TableCell className="text-xs text-muted-foreground max-w-[120px] truncate">{asset.license}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`text-[10px] ${
                          asset.policyStatus === "verified" ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" :
                          asset.policyStatus === "rejected" ? "bg-red-500/20 text-red-400 border-red-500/30" :
                          "bg-yellow-500/20 text-yellow-400 border-yellow-500/30"
                        }`}>
                          {asset.policyStatus}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={9} className="text-center py-8 text-muted-foreground">
                      No assets collected yet. Run the content generation pipeline to download background footage!
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
