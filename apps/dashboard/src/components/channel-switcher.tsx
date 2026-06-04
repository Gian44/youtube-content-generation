"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

export interface ChannelSwitcherOption {
  id: string;
  slug: string;
  name: string;
  status: string;
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function resolveActiveId(
  channels: ChannelSwitcherOption[],
  preferredId?: string
): string {
  if (channels.length === 0) return "";

  if (preferredId) {
    const match = channels.find((c) => c.id === preferredId);
    if (match) return match.id;
  }

  const cookie = readCookie("sf_channel");
  const fromCookie = channels.find((c) => c.id === cookie || c.slug === cookie);
  if (fromCookie) return fromCookie.id;

  const def = channels.find((c) => c.slug === "default");
  return (def || channels[0]).id;
}

function formatChannelLabel(channel: ChannelSwitcherOption): string {
  return channel.status !== "active" ? `${channel.name} (paused)` : channel.name;
}

interface ChannelSwitcherProps {
  channels: ChannelSwitcherOption[];
  activeChannelId: string;
}

/**
 * Global active-channel selector. Persists the choice in the `sf_channel`
 * cookie and refreshes server components so all data views re-scope.
 */
export function ChannelSwitcher({ channels, activeChannelId }: ChannelSwitcherProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [channelList, setChannelList] = useState(channels);
  const [active, setActive] = useState(() => resolveActiveId(channels, activeChannelId));

  useEffect(() => {
    setChannelList(channels);
    setActive(resolveActiveId(channels, activeChannelId));
  }, [channels, activeChannelId]);

  useEffect(() => {
    let cancelled = false;

    fetch("/api/channels")
      .then((r) => r.json())
      .then((d) => {
        if (cancelled || !d.success) return;
        const chs: ChannelSwitcherOption[] = d.channels || [];
        setChannelList(chs);
        setActive(resolveActiveId(chs, readCookie("sf_channel") || activeChannelId || undefined));
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [pathname, activeChannelId]);

  const handleChange = (id: string) => {
    setActive(id);
    document.cookie = `sf_channel=${encodeURIComponent(id)}; path=/; max-age=31536000; samesite=lax`;
    router.refresh();
  };

  if (channelList.length <= 0) return null;

  const activeChannel =
    channelList.find((c) => c.id === active) ||
    channelList.find((c) => c.id === activeChannelId);

  return (
    <div className="border-b border-border px-4 py-3">
      <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Active Channel
      </label>
      <select
        value={activeChannel?.id ?? ""}
        onChange={(e) => handleChange(e.target.value)}
        className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        aria-label={`Select active channel. Current channel: ${activeChannel?.name ?? "none"}`}
      >
        {channelList.map((c) => (
          <option key={c.id} value={c.id}>
            {formatChannelLabel(c)}
          </option>
        ))}
      </select>
    </div>
  );
}
