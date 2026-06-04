import { DashboardFrame } from "@/components/dashboard-frame";
import { getActiveChannelId, getChannels } from "@/lib/channels";

export async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const channels = await getChannels();
  const activeChannelId = (await getActiveChannelId()) ?? "";

  const channelOptions = channels.map((c) => ({
    id: c.id,
    slug: c.slug,
    name: c.name,
    status: c.status,
  }));

  return (
    <DashboardFrame channels={channelOptions} activeChannelId={activeChannelId}>
      {children}
    </DashboardFrame>
  );
}
