"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { useEffect, useState } from "react";
import { ChannelSwitcher } from "@/components/channel-switcher";

const navigation = [
  {
    group: "Dashboard",
    items: [
      { name: "Overview", href: "/", icon: "📊" },
      { name: "Content Calendar", href: "/calendar", icon: "📅" },
    ],
  },
  {
    group: "Channels",
    items: [
      { name: "Channels", href: "/channels", icon: "📺" },
    ],
  },
  {
    group: "Content",
    items: [
      { name: "Daily Batches", href: "/batches", icon: "📦" },
      { name: "Story Library", href: "/stories", icon: "📖" },
      { name: "Asset Library", href: "/assets", icon: "🎬" },
      { name: "Prompt Lab", href: "/prompts", icon: "🧪" },
    ],
  },
  {
    group: "Production",
    items: [
      { name: "Render Queue", href: "/renders", icon: "🎥" },
      { name: "Upload Queue", href: "/uploads", icon: "⬆️" },
    ],
  },
  {
    group: "Insights",
    items: [
      { name: "Analytics", href: "/analytics", icon: "📈" },
      { name: "API Usage", href: "/api-usage", icon: "⚡" },
      { name: "Compliance", href: "/compliance", icon: "🛡️" },
    ],
  },
  {
    group: "System",
    items: [
      { name: "Settings", href: "/settings", icon: "⚙️" },
    ],
  },
];

function useSchedulerStatus() {
  const [status, setStatus] = useState<{
    isRunning: boolean;
    nextRun: string | null;
    lastRun: string | null;
    lastRunStatus: string | null;
  } | null>(null);

  useEffect(() => {
    // Check if running inside Electron desktop app
    const sf = (window as any).storyfactory;
    if (!sf) return;

    const fetchStatus = async () => {
      try {
        const s = await sf.getSchedulerStatus();
        setStatus(s);
      } catch {}
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 15000);

    // Listen for live updates
    sf.onSchedulerUpdate?.((data: any) => setStatus(data));

    return () => clearInterval(interval);
  }, []);

  return status;
}

export function Sidebar() {
  const pathname = usePathname();
  const scheduler = useSchedulerStatus();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const isDesktop = mounted && typeof window !== 'undefined' && !!(window as any).storyfactory;

  return (
    <aside className="fixed left-0 top-0 z-40 flex h-screen w-64 flex-col border-r border-border bg-sidebar">
      {/* Logo */}
      <div className="flex h-16 items-center gap-3 border-b border-border px-6">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-lg">
          🏭
        </div>
        <div>
          <h1 className="text-sm font-bold gradient-text">StoryFactory</h1>
          <p className="text-[10px] text-muted-foreground">
            {isDesktop ? "Desktop App" : "Content Engine"}
          </p>
        </div>
      </div>

      {/* Active channel switcher */}
      <ChannelSwitcher />

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        {navigation.map((group) => (
          <div key={group.group} className="mb-6">
            <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              {group.group}
            </p>
            <ul className="space-y-1">
              {group.items.map((item) => {
                const isActive = pathname === item.href;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={cn(
                        "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-all duration-200",
                        isActive
                          ? "bg-accent text-accent-foreground font-medium shadow-sm"
                          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                      )}
                    >
                      <span className="text-base">{item.icon}</span>
                      <span>{item.name}</span>
                      {isActive && (
                        <span className="ml-auto h-1.5 w-1.5 rounded-full bg-primary animate-pulse-glow" />
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-border p-4 space-y-3">
        {/* Scheduler Status (only in desktop mode) */}
        {scheduler && (
          <div className="glass-card rounded-lg p-3 space-y-2">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-base">⏰</span>
              <span className="font-medium text-foreground">Scheduler</span>
              {scheduler.isRunning ? (
                <span className="ml-auto h-2 w-2 rounded-full bg-yellow-500 animate-pulse" title="Pipeline running" />
              ) : (
                <span className="ml-auto h-2 w-2 rounded-full bg-green-500" title="Idle" />
              )}
            </div>
            {scheduler.isRunning && (
              <p className="text-[10px] text-yellow-400 font-medium">
                🔄 Pipeline running...
              </p>
            )}
            {scheduler.nextRun && (
              <p className="text-[10px] text-muted-foreground">
                Next: {scheduler.nextRun}
              </p>
            )}
            {scheduler.lastRun && (
              <p className="text-[10px] text-muted-foreground">
                Last: {scheduler.lastRun}{" "}
                {scheduler.lastRunStatus === "success" ? "✅" : scheduler.lastRunStatus === "error" ? "❌" : ""}
              </p>
            )}
          </div>
        )}

        {/* Worker Status */}
        <div className="glass-card rounded-lg p-3">
          <div className="flex items-center gap-2 text-xs">
            <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
            <span className="text-muted-foreground">Worker Online</span>
          </div>
          <p className="mt-1 text-[10px] text-muted-foreground">
            Last batch: Today
          </p>
        </div>
      </div>
    </aside>
  );
}

