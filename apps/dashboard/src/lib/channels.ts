import { cookies } from 'next/headers';
import { query } from '@/lib/db';

/**
 * Server-side channel helpers. The active channel is stored in the `sf_channel`
 * cookie (holds the channel id). All data pages scope their queries to it.
 *
 * Secrets never appear here — `secrets_encrypted` is deliberately never
 * selected; only enabled/configured status is exposed.
 */

export const ACTIVE_CHANNEL_COOKIE = 'sf_channel';

export interface ChannelRow {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  status: string;
  niche: string | null;
  contentStyle: string | null;
  config: Record<string, unknown>;
}

export interface ChannelIntegrationRow {
  providerKey: string;
  enabled: boolean;
  hasSecret: boolean;
  config: Record<string, unknown>;
  status: string;
}

export async function getChannels(): Promise<ChannelRow[]> {
  const rows = await query<ChannelRow>(
    `SELECT id, slug, name, description, status, niche, content_style, config
     FROM channels ORDER BY created_at ASC`
  );
  return rows.map((r) => ({ ...r, config: (r.config as Record<string, unknown>) || {} }));
}

export async function getChannel(id: string): Promise<ChannelRow | null> {
  const rows = await query<ChannelRow>(
    `SELECT id, slug, name, description, status, niche, content_style, config
     FROM channels WHERE id = $1 OR slug = $1 LIMIT 1`,
    [id]
  );
  return rows[0] ? { ...rows[0], config: (rows[0].config as Record<string, unknown>) || {} } : null;
}

/**
 * Resolve the active channel id from the cookie, falling back to the default
 * channel (slug "default") and then the first channel.
 */
export async function getActiveChannelId(): Promise<string | null> {
  const channels = await getChannels();
  if (channels.length === 0) return null;

  const cookieStore = await cookies();
  const cookieVal = cookieStore.get(ACTIVE_CHANNEL_COOKIE)?.value;

  if (cookieVal) {
    const match = channels.find((c) => c.id === cookieVal || c.slug === cookieVal);
    if (match) return match.id;
  }
  const def = channels.find((c) => c.slug === 'default');
  return (def || channels[0]).id;
}

export async function getActiveChannel(): Promise<ChannelRow | null> {
  const id = await getActiveChannelId();
  if (!id) return null;
  const channels = await getChannels();
  return channels.find((c) => c.id === id) || null;
}

/**
 * Integration status for a channel, read directly from the DB (no secrets).
 * `hasSecret` reflects whether an encrypted blob exists; "configured" for the
 * UI is approximated as enabled && (hasSecret || storage.local). The worker
 * performs authoritative validation at run time.
 */
export async function getChannelIntegrations(
  channelId: string
): Promise<ChannelIntegrationRow[]> {
  const rows = await query<{
    providerKey: string;
    enabled: number | boolean;
    hasSecret: number | boolean;
    config: Record<string, unknown>;
    status: string;
  }>(
    `SELECT provider_key,
            enabled,
            (secrets_encrypted IS NOT NULL AND secrets_encrypted != '') AS has_secret,
            config,
            status
     FROM channel_integrations WHERE channel_id = $1`,
    [channelId]
  );
  return rows.map((r) => ({
    providerKey: r.providerKey,
    enabled: Boolean(r.enabled),
    hasSecret: Boolean(r.hasSecret),
    config: (r.config as Record<string, unknown>) || {},
    status: r.status,
  }));
}
