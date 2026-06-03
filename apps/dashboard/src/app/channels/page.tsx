import { DashboardLayout } from "@/components/dashboard-layout";
import { ChannelsManager } from "@/components/channels-manager";
import { getChannels, getChannelIntegrations } from "@/lib/channels";

export const dynamic = "force-dynamic";

interface PageProps {
  searchParams:
    | Promise<{ [key: string]: string | string[] | undefined }>
    | { [key: string]: string | string[] | undefined };
}

export default async function ChannelsPage({ searchParams }: PageProps) {
  const resolved = searchParams instanceof Promise ? await searchParams : searchParams;
  const oauthStatus = resolved?.status as string | undefined;
  const oauthMessage = resolved?.message as string | undefined;

  const channels = await getChannels();
  const withIntegrations = await Promise.all(
    channels.map(async (c) => ({
      id: c.id,
      slug: c.slug,
      name: c.name,
      description: c.description,
      status: c.status,
      niche: c.niche,
      contentStyle: c.contentStyle,
      config: c.config,
      integrations: await getChannelIntegrations(c.id),
    }))
  );

  const youtubeClientId = process.env.YOUTUBE_CLIENT_ID || "";
  const redirectUri =
    process.env.YOUTUBE_REDIRECT_URI || "http://localhost:3000/api/auth/youtube/callback";

  return (
    <DashboardLayout>
      <div className="space-y-8 animate-fade-in-up">
        <div>
          <h1 className="text-3xl font-bold gradient-text">Channels</h1>
          <p className="mt-1 text-muted-foreground">
            Create channels, wire per-channel integrations, and connect a YouTube account to each.
          </p>
        </div>

        {channels.length === 0 ? (
          <div className="rounded-lg border border-border/40 bg-card/50 p-6 text-sm text-muted-foreground">
            No channels yet. Run <code className="font-mono">npm run db:migrate</code> to create the
            default channel from your <code className="font-mono">.env</code>, then create more here.
          </div>
        ) : (
          <ChannelsManager
            channels={withIntegrations}
            youtubeClientId={youtubeClientId}
            redirectUri={redirectUri}
            oauthStatus={oauthStatus}
            oauthMessage={oauthMessage}
          />
        )}
      </div>
    </DashboardLayout>
  );
}
