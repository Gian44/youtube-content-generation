"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { QuickActions } from "@/components/quick-actions";
import { PipelineStatus } from "@/components/pipeline-status";

const ALL_DASHBOARD_SLICES = ["metrics", "recentBatches", "pipelineStatus", "analytics"] as const;
type DashboardSlice = (typeof ALL_DASHBOARD_SLICES)[number];

const categoryColors: Record<string, string> = {
  aita: "bg-violet-500/20 text-violet-400",
  revenge: "bg-red-500/20 text-red-400",
  workplace_drama: "bg-blue-500/20 text-blue-400",
  cheating: "bg-orange-500/20 text-orange-400",
  entitled_parents: "bg-yellow-500/20 text-yellow-400",
  relationships: "bg-pink-500/20 text-pink-400",
  family_drama: "bg-amber-500/20 text-amber-400",
  confessions: "bg-indigo-500/20 text-indigo-400",
  scary_stories: "bg-emerald-500/20 text-emerald-400",
  wholesome: "bg-green-500/20 text-green-400",
  customer_service_drama: "bg-cyan-500/20 text-cyan-400",
  mysteries: "bg-purple-500/20 text-purple-400",
  creepy_encounters: "bg-teal-500/20 text-teal-400",
};

const statusColors: Record<string, string> = {
  completed: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  uploaded: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  rendering: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  failed: "bg-red-500/20 text-red-400 border-red-500/30",
  pending: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  dry_run_completed: "bg-purple-500/20 text-purple-400 border-purple-500/30",
};

interface DashboardContentProps {
  initialData: {
    metrics: {
      totalBatches: number;
      totalStories: number;
      totalRenders: number;
      totalUploads: number;
      todayStories: number;
      todayRenders: number;
      todayUploads: number;
    };
    recentBatches: any[];
    latestStatus: string;
    topPerformers: any[];
  };
}

type DashboardData = DashboardContentProps["initialData"];

type StoryFactorySchedulerEvent = {
  dashboardSlices?: string[];
};

type StoryFactoryBridge = {
  onSchedulerUpdate?: (callback: (event: StoryFactorySchedulerEvent) => void) => (() => void) | void;
};

function normalizeDashboardSlices(slices: readonly string[]): DashboardSlice[] {
  const allowedSlices = new Set<string>(ALL_DASHBOARD_SLICES);
  const normalized = slices.filter((slice): slice is DashboardSlice => allowedSlices.has(slice));
  return normalized.length > 0 ? [...new Set(normalized)] : [...ALL_DASHBOARD_SLICES];
}

function mergeDashboardData(current: DashboardData, next: Partial<DashboardData>): DashboardData {
  return {
    metrics: next.metrics || current.metrics,
    recentBatches: next.recentBatches || current.recentBatches,
    latestStatus: next.latestStatus || current.latestStatus,
    topPerformers: next.topPerformers || current.topPerformers,
  };
}

export function DashboardContent({ initialData }: DashboardContentProps) {
  const [data, setData] = useState(initialData);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const refreshInFlightRef = useRef(false);
  const queuedSlicesRef = useRef<Set<DashboardSlice>>(new Set());
  const queuedForceRef = useRef(false);

  const refreshDashboardSlices = useCallback(async (
    slices: readonly string[] = ALL_DASHBOARD_SLICES,
    options: { force?: boolean } = { force: true }
  ) => {
    for (const slice of normalizeDashboardSlices(slices)) {
      queuedSlicesRef.current.add(slice);
    }
    queuedForceRef.current = queuedForceRef.current || options.force !== false;

    if (refreshInFlightRef.current) return;

    refreshInFlightRef.current = true;
    setIsRefreshing(true);
    try {
      while (queuedSlicesRef.current.size > 0) {
        const nextSlices = Array.from(queuedSlicesRef.current);
        const force = queuedForceRef.current;
        queuedSlicesRef.current.clear();
        queuedForceRef.current = false;

        const params = new URLSearchParams({
          slices: nextSlices.join(","),
        });
        if (force) {
          params.set("force", "1");
        }

        const res = await fetch(`/api/dashboard-data?${params.toString()}`, { cache: "no-store" });
        const json = await res.json();
        if (json.success) {
          setData((current) => mergeDashboardData(current, json));
        }
      }
    } catch (err) {
      console.error("Failed to refresh dashboard data", err);
    } finally {
      refreshInFlightRef.current = false;
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const storyfactory = (window as Window & { storyfactory?: StoryFactoryBridge }).storyfactory;
    if (!storyfactory?.onSchedulerUpdate) return;

    const cleanup = storyfactory.onSchedulerUpdate((event) => {
      if (event.dashboardSlices?.length) {
        refreshDashboardSlices(event.dashboardSlices, { force: true });
      }
    });

    if (typeof cleanup === "function") {
      return cleanup;
    }
  }, [refreshDashboardSlices]);

  const handleRefreshDashboard = async () => {
    await refreshDashboardSlices(ALL_DASHBOARD_SLICES, { force: true });
  };

  const { metrics, recentBatches, latestStatus, topPerformers } = data;

  const stats = [
    { 
      label: "Total Batches", 
      value: String(metrics.totalBatches), 
      change: "Lifetime batches", 
      icon: "📦", 
      color: "from-violet-500/20 to-purple-500/20",
    },
    { 
      label: "Stories Generated", 
      value: String(metrics.totalStories), 
      change: `+${metrics.todayStories} today`, 
      icon: "📖", 
      color: "from-blue-500/20 to-cyan-500/20",
    },
    { 
      label: "Videos Rendered", 
      value: String(metrics.totalRenders), 
      change: `+${metrics.todayRenders} today`, 
      icon: "🎥", 
      color: "from-emerald-500/20 to-green-500/20",
    },
    { 
      label: "YouTube Uploads", 
      value: String(metrics.totalUploads), 
      change: `+${metrics.todayUploads} today`, 
      icon: "⬆️", 
      color: "from-rose-500/20 to-pink-500/20",
    },
  ];

  const maxViews = topPerformers.length > 0 ? Math.max(...topPerformers.map(p => p.views)) : 1;

  return (
    <div className="space-y-8 animate-fade-in-up">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold gradient-text">Dashboard</h1>
          <p className="mt-1 text-muted-foreground">
            Welcome back. Here&apos;s your content factory status.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleRefreshDashboard}
          disabled={isRefreshing}
          title="Refresh dashboard"
          className="w-fit"
        >
          <RefreshCw className={isRefreshing ? "animate-spin" : ""} />
          {isRefreshing ? "Refreshing" : "Refresh"}
        </Button>
      </div>

      {/* Stats Grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => (
          <Card 
            key={stat.label} 
            className="relative overflow-hidden border-border/50 bg-card/50 backdrop-blur-sm transition-all duration-500 hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5"
          >
            <div className={`absolute inset-0 bg-gradient-to-br ${stat.color} opacity-50`} />
            <CardContent className="relative p-6">
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">{stat.label}</p>
                <span className="text-2xl">{stat.icon}</span>
              </div>
              <p className="mt-2 text-3xl font-bold transition-all duration-300">{stat.value}</p>
              <p className="mt-1 text-xs text-muted-foreground">{stat.change}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Main Content Grid */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Recent Batches */}
        <Card className="lg:col-span-2 border-border/50 bg-card/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="text-lg">Recent Batches</CardTitle>
            <CardDescription>Daily content generation history</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {recentBatches.length > 0 ? (
                recentBatches.map((batch: any) => (
                  <div
                    key={batch.id}
                    className="flex items-center justify-between rounded-lg border border-border/30 bg-background/50 p-4 transition-all hover:border-primary/20 hover:bg-accent/30"
                  >
                    <div className="flex items-center gap-4">
                      <div className="text-sm font-mono text-muted-foreground">{batch.date}</div>
                      <Badge variant="outline" className={categoryColors[batch.category] || "bg-gray-500/20 text-gray-400"}>
                        {batch.category ? batch.category.replace("_", " ") : "unknown"}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-6">
                      <div className="text-right text-sm">
                        <span className="text-muted-foreground">{batch.shortsCount}S + {batch.longFormCount}L</span>
                      </div>
                      <div className="text-right text-sm font-medium">
                        {(batch.views || 0).toLocaleString()} views
                      </div>
                      <Badge variant="outline" className={statusColors[batch.status] || statusColors.pending}>
                        {batch.status ? batch.status.replace("_", " ") : "pending"}
                      </Badge>
                    </div>
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground text-center py-6">No batches found. Run the pipeline to start.</p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Top Performers */}
        <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
          <CardHeader>
            <CardTitle className="text-lg">Top Performers</CardTitle>
            <CardDescription>Best performing content this week</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {topPerformers.length > 0 ? (
                topPerformers.map((video: any, i: number) => (
                  <div key={i} className="space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium leading-tight line-clamp-2">{video.title}</p>
                      <Badge variant="outline" className={`shrink-0 ${categoryColors[video.category] || ""}`}>
                        {video.category ? video.category.replace("_", " ") : "unknown"}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-muted-foreground">
                      <span>👁 {((video.views || 0) / 1000).toFixed(1)}K</span>
                      <span>❤️ {((video.likes || 0) / 1000).toFixed(1)}K</span>
                    </div>
                    <Progress value={Math.min(100, ((video.views || 0) / maxViews) * 100)} className="h-1" />
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground text-center py-6">No published content yet.</p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Pipeline Status */}
      <PipelineStatus status={latestStatus} latestBatch={recentBatches[0] ?? null} />

      {/* Quick Actions */}
      <QuickActions />
    </div>
  );
}
