"use client";

import { Sidebar } from "@/components/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ChannelSwitcherOption } from "@/components/channel-switcher";

interface DashboardFrameProps {
  children: React.ReactNode;
  channels: ChannelSwitcherOption[];
  activeChannelId: string;
}

export function DashboardFrame({ children, channels, activeChannelId }: DashboardFrameProps) {
  return (
    <TooltipProvider>
      <div className="flex min-h-screen">
        <Sidebar channels={channels} activeChannelId={activeChannelId} />
        <main className="flex-1 pl-64">
          <div className="p-6 lg:p-8">{children}</div>
        </main>
      </div>
    </TooltipProvider>
  );
}
