"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

interface ChannelOption {
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

/**
 * Global active-channel selector. Persists the choice in the `sf_channel`
 * cookie and refreshes server components so all data views re-scope.
 */
export function ChannelSwitcher() {
  const router = useRouter();
  const [channels, setChannels] = useState<ChannelOption[]>([]);
  const [active, setActive] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    fetch("/api/channels")
      .then((r) => r.json())
      .then((d) => {
        if (cancelled || !d.success) return;
        const chs: ChannelOption[] = d.channels || [];
        setChannels(chs);
        const cookie = readCookie("sf_channel");
        const match = chs.find((c) => c.id === cookie || c.slug === cookie);
        const def = chs.find((c) => c.slug === "default");
        setActive((match || def || chs[0])?.id ?? "");
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const handleChange = (id: string) => {
    setActive(id);
    document.cookie = `sf_channel=${encodeURIComponent(id)}; path=/; max-age=31536000; samesite=lax`;
    router.refresh();
  };

  if (channels.length <= 0) return null;

  return (
    <div className="border-b border-border px-4 py-3">
      <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Active Channel
      </label>
      <select
        value={active}
        onChange={(e) => handleChange(e.target.value)}
        className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
        aria-label="Select active channel"
      >
        {channels.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
            {c.status !== "active" ? " (paused)" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
