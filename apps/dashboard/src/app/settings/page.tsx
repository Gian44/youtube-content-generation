import Link from "next/link";
import { DashboardLayout } from "@/components/dashboard-layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { query } from "@/lib/db";

export const dynamic = 'force-dynamic';

export default async function SettingsPage() {
  // Query database settings (app-level defaults)
  const dbSettings = await query("SELECT key, value FROM settings");

  const getSettingVal = (key: string, defaultVal: string) => {
    const match = dbSettings.find(
      (s) => s.key.toLowerCase() === key.toLowerCase() || s.key === key
    );
    if (match) return match.value;
    return process.env[key] || defaultVal;
  };

  const checkKey = (envVar: string) => {
    const val = process.env[envVar];
    if (!val || val.includes("your-") || val === "") return "not_configured";
    return "connected";
  };

  const checkYouTubeSetup = () => {
    const hasClientId = checkKey("YOUTUBE_CLIENT_ID") === "connected";
    const hasSecret = checkKey("YOUTUBE_CLIENT_SECRET") === "connected";
    const hasToken = checkKey("YOUTUBE_REFRESH_TOKEN") === "connected";
    
    if (hasClientId && hasSecret && hasToken) return "connected";
    if (hasClientId && hasSecret) return "needs_setup";
    return "not_configured";
  };

  // App-level defaults from env. Per-channel keys/tokens are configured under /channels.
  const apiStatus = [
    { name: "OpenAI", status: checkKey("OPENAI_API_KEY"), key: "OPENAI_API_KEY" },
    { name: "Gemini", status: checkKey("GEMINI_API_KEY"), key: "GEMINI_API_KEY" },
    { name: "YouTube App", status: checkYouTubeSetup(), key: "YOUTUBE_CLIENT_ID" },
    { name: "Pexels", status: checkKey("PEXELS_API_KEY"), key: "PEXELS_API_KEY" },
    { name: "Pixabay", status: checkKey("PIXABAY_API_KEY"), key: "PIXABAY_API_KEY" },
  ];

  const settingGroups = [
    {
      title: "Automation",
      icon: "🤖",
      settings: [
        { key: "AUTO_MODE", value: getSettingVal("AUTO_MODE", "true"), label: "Fully Automated Mode", desc: "Run pipeline without manual intervention" },
        { key: "REQUIRE_APPROVAL", value: getSettingVal("REQUIRE_APPROVAL", "false"), label: "Require Approval", desc: "Pause for human review before upload" },
        { key: "DRY_RUN", value: getSettingVal("DRY_RUN", "false"), label: "Dry Run Mode", desc: "Test pipeline without API calls" },
      ],
    },
    {
      title: "Content",
      icon: "📝",
      settings: [
        { key: "SHORTS_PER_DAY_MIN", value: getSettingVal("shorts_per_day_min", "3"), label: "Min Shorts/Day", desc: "Minimum shorts per batch" },
        { key: "SHORTS_PER_DAY_MAX", value: getSettingVal("shorts_per_day_max", "5"), label: "Max Shorts/Day", desc: "Maximum shorts per batch" },
        { key: "LONG_FORM_PER_DAY", value: getSettingVal("long_form_per_day", "1"), label: "Long-form/Day", desc: "Long-form videos per batch" },
        { key: "LONG_FORM_TARGET_MINUTES", value: getSettingVal("long_form_target_minutes", "10"), label: "Long-form Target (Min)", desc: "Target duration in minutes" },
        { key: "DAILY_TOPIC_MODE", value: getSettingVal("DAILY_TOPIC_MODE", "weighted_random"), label: "Topic Selection", desc: "How daily topic is chosen" },
      ],
    },
    {
      title: "TTS Providers",
      icon: "🎙",
      settings: [
        { key: "TTS_OPENAI_RATIO", value: getSettingVal("TTS_OPENAI_RATIO", "0.8"), label: "OpenAI Ratio", desc: "Percentage of TTS via OpenAI" },
        { key: "TTS_GEMINI_RATIO", value: getSettingVal("TTS_GEMINI_RATIO", "0.2"), label: "Gemini Ratio", desc: "Percentage of TTS via Gemini" },
      ],
    },
    {
      title: "YouTube Upload",
      icon: "📺",
      settings: [
        { key: "UPLOAD_PRIVACY_MODE", value: getSettingVal("UPLOAD_PRIVACY_MODE", "public"), label: "Privacy Mode", desc: "Default upload visibility" },
        { key: "ALLOW_PRIVATE_FALLBACK", value: getSettingVal("ALLOW_PRIVATE_FALLBACK", "true"), label: "Private Fallback", desc: "Fall back to private if public fails" },
      ],
    },
    {
      title: "Safety",
      icon: "🛡️",
      settings: [
        { key: "FAIL_SAFE_ON_POLICY_FLAG", value: getSettingVal("FAIL_SAFE_ON_POLICY_FLAG", "true"), label: "Block Flagged Content", desc: "Block stories that fail policy checks" },
        { key: "FAIL_SAFE_ON_LICENSE_UNKNOWN", value: getSettingVal("FAIL_SAFE_ON_LICENSE_UNKNOWN", "true"), label: "Block Unknown Licenses", desc: "Reject assets with unverified licenses" },
      ],
    },
  ];

  const disclosureVal = getSettingVal("disclosure_line", "These are original fictional stories created for entertainment.");

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div>
          <h1 className="text-3xl font-bold gradient-text">App Settings</h1>
          <p className="mt-1 text-muted-foreground">
            Global defaults and infrastructure. Per-channel integrations, API keys, and YouTube
            connections live under{" "}
            <Link href="/channels" className="text-primary underline">Channels</Link>.
          </p>
        </div>

        {/* Per-channel pointer */}
        <Card className="border-primary/30 bg-primary/5">
          <CardContent className="flex items-center justify-between gap-4 py-4">
            <p className="text-sm text-muted-foreground">
              Looking to add API keys or connect a YouTube account? Those are now configured
              <strong> per channel</strong>.
            </p>
            <Link
              href="/channels"
              className="rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:opacity-90"
            >
              Manage Channels →
            </Link>
          </CardContent>
        </Card>

        {/* App-level API defaults */}
        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="text-lg">App-level API defaults</CardTitle>
            <CardDescription>
              Shared values loaded from <code>.env</code>. These act as fallback defaults; each channel
              can override them with its own keys.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-5">
              {apiStatus.map((api) => (
                <div key={api.name} className="rounded-lg border border-border/30 bg-background/50 p-4 text-center flex flex-col justify-between items-center">
                  <div className="w-full">
                    <p className="font-medium text-sm">{api.name}</p>
                    <Badge variant="outline" className={`mt-2 text-[10px] ${
                      api.status === "connected" ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" :
                      api.status === "needs_setup" ? "bg-yellow-500/20 text-yellow-400 border-yellow-500/30" :
                      "bg-muted text-muted-foreground"
                    }`}>
                      {api.status === "connected" ? "✓ Set" :
                       api.status === "needs_setup" ? "⚠ Partial" :
                       "— Not set"}
                    </Badge>
                  </div>
                  <p className="mt-2 text-[10px] font-mono text-muted-foreground truncate w-full">{api.key}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Configuration Groups */}
        {settingGroups.map((group) => (
          <Card key={group.title} className="border-border/50 bg-card/50 backdrop-blur-sm">
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2">
                <span>{group.icon}</span>
                {group.title}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {group.settings.map((setting, i) => (
                  <div key={setting.key}>
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium">{setting.label}</p>
                        <p className="text-xs text-muted-foreground">{setting.desc}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <code className="rounded bg-muted px-2 py-1 text-xs font-mono">{setting.value}</code>
                        <span className="text-[10px] text-muted-foreground font-mono">{setting.key}</span>
                      </div>
                    </div>
                    {i < group.settings.length - 1 && <Separator className="mt-4 bg-border/30" />}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ))}

        {/* Disclosure */}
        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="text-lg">📋 Content Disclosure</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg border border-border/30 bg-background/50 p-4">
              <p className="text-sm italic text-muted-foreground">
                &ldquo;{disclosureVal}&rdquo;
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                This line is automatically included in every video description. Configured via the `disclosure_line` key in settings or `.env`.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
