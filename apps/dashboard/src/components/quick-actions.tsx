"use client";

import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";

interface ActionItem {
  label: string;
  desc: string;
  icon: string;
  action: string;
}

const actions: ActionItem[] = [
  { label: "Full Automation", desc: "Generate, render & upload", icon: "🤖", action: "worker:full" },
  { label: "Run Daily Pipeline", desc: "Generate today's content", icon: "🚀", action: "worker:daily" },
  { label: "Process Renders", desc: "Render pending videos", icon: "🎬", action: "worker:render" },
  { label: "Upload to YouTube", desc: "Upload rendered videos", icon: "📤", action: "worker:upload" },
  { label: "Fetch Analytics", desc: "Update video stats", icon: "📊", action: "worker:analytics" },
];

export function QuickActions() {
  const [running, setRunning] = useState<string | null>(null);

  const handleAction = async (action: string, label: string) => {
    if (running) return;
    setRunning(action);
    try {
      // Check if running inside Electron desktop app
      const sf = typeof window !== 'undefined' && (window as any).storyfactory;
      if (sf) {
        if (action === "worker:full") {
          const res = await sf.runPipelineNow();
          if (res.success) {
            alert(`Successfully triggered: ${label} in desktop scheduler`);
          } else {
            alert(`Failed: ${res.message}`);
          }
          return;
        }
        if (action === "worker:analytics") {
          const res = await sf.runAnalyticsNow();
          if (res.success) {
            alert(`Successfully triggered: ${label} in desktop scheduler`);
          } else {
            alert(`Failed: ${res.message}`);
          }
          return;
        }
      }

      const res = await fetch("/api/run-action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await res.json();
      if (data.success) {
        alert(`Successfully triggered: ${label} in background`);
      } else {
        alert(`Failed to trigger: ${data.error}`);
      }
    } catch (err: any) {
      alert(`Error triggering action: ${err.message}`);
    } finally {
      setRunning(null);
    }
  };

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
      {actions.map((act) => (
        <Card
          key={act.label}
          onClick={() => handleAction(act.action, act.label)}
          className={`cursor-pointer border-border/50 bg-card/50 backdrop-blur-sm transition-all duration-300 hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5 hover:-translate-y-0.5 ${
            running === act.action ? "opacity-50 cursor-wait animate-pulse border-primary/50" : ""
          }`}
        >
          <CardContent className="p-5">
            <div className="flex items-center gap-3">
              <span className="text-2xl">{act.icon}</span>
              <div>
                <p className="text-sm font-medium">{act.label}</p>
                <p className="text-xs text-muted-foreground">{act.desc}</p>
              </div>
            </div>
            <p className="mt-3 text-[10px] font-mono text-muted-foreground">
              {running === act.action ? "Running process..." : `npm run ${act.action}`}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
