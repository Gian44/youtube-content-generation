import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";

/**
 * Human-friendly view of the daily pipeline's progress.
 *
 * The worker stores a single coarse `status` string per batch (e.g.
 * "tts_complete"). This component translates that into plain language a
 * non-technical user can understand at a glance: what's happening right now,
 * how far along the run is, and which steps are done vs. still to come.
 */

const TOTAL_STEPS = 9;

interface StepMeta {
  /** Short label shown under each step in the stepper row. */
  label: string;
  emoji: string;
  /** Present-continuous phrase shown while this step is active. */
  doing: string;
  /** One plain sentence explaining the step. */
  description: string;
}

const STEPS: StepMeta[] = [
  { label: "Topic", emoji: "🎯", doing: "Choosing today's topic", description: "Deciding what today's videos will be about." },
  { label: "Stories", emoji: "✍️", doing: "Writing the stories", description: "Creating a fresh story for each video." },
  { label: "Safety", emoji: "🛡️", doing: "Running the safety check", description: "Making sure every story follows the content rules." },
  { label: "Voiceover", emoji: "🎙️", doing: "Recording the voiceovers", description: "Turning each story into natural-sounding speech." },
  { label: "Captions", emoji: "💬", doing: "Adding the captions", description: "Creating on-screen text that follows the voice." },
  { label: "Visuals", emoji: "🎞️", doing: "Gathering the visuals", description: "Finding background clips that match each story." },
  { label: "Render", emoji: "🎬", doing: "Rendering the videos", description: "Combining voice, captions, and visuals into finished videos." },
  { label: "Upload", emoji: "📤", doing: "Uploading to YouTube", description: "Publishing the finished videos to your channel." },
  { label: "Analytics", emoji: "📊", doing: "Tracking performance", description: "Watching how the published videos perform." },
];

// How far each backend status has progressed. Higher = further along.
const STATUS_PRIORITY: Record<string, number> = {
  pending: 0,
  topic_selected: 1,
  stories_generated: 2,
  policy_checked: 3,
  tts_complete: 4,
  captions_complete: 5,
  assets_collected: 6,
  rendering: 7,
  rendered: 8,
  uploading: 9,
  uploaded: 10,
  completed: 11,
  dry_run_completed: 11,
  failed: -1,
  cancelled: -1,
};

// The priority a run must reach for each step (by index) to count as "done".
const STEP_DONE_AT = [1, 2, 3, 4, 5, 6, 8, 10, 11];

type Phase = "idle" | "running" | "done" | "failed";
type StepState = "done" | "active" | "pending";

interface LatestBatch {
  category?: string;
  shortsCount?: number;
  longFormCount?: number;
}

interface PipelineStatusProps {
  status: string;
  latestBatch?: LatestBatch | null;
}

interface PipelineView {
  phase: Phase;
  isDryRun: boolean;
  doneCount: number;
  activeIndex: number;
  percent: number;
}

function describePipeline(status: string): PipelineView {
  const priority = STATUS_PRIORITY[status] ?? 0;
  const isFailed = status === "failed" || status === "cancelled";
  const isDryRun = status === "dry_run_completed";
  const isDone = status === "completed" || status === "uploaded" || isDryRun;

  const doneCount = STEP_DONE_AT.filter((threshold) => priority >= threshold).length;
  const activeIndex = Math.min(doneCount, TOTAL_STEPS - 1);

  let phase: Phase = "running";
  if (isFailed) phase = "failed";
  else if (isDone) phase = "done";
  else if (status === "pending") phase = "idle";

  const percent = phase === "done" ? 100 : Math.round((doneCount / TOTAL_STEPS) * 100);

  return { phase, isDryRun, doneCount, activeIndex, percent };
}

function getStepState(index: number, view: PipelineView): StepState {
  if (view.phase === "done") return "done";
  if (view.phase === "failed" || view.phase === "idle") return "pending";
  if (index < view.doneCount) return "done";
  if (index === view.activeIndex) return "active";
  return "pending";
}

interface Hero {
  emoji: string;
  title: string;
  description: string;
}

function getHero(status: string, view: PipelineView): Hero {
  if (view.phase === "failed") {
    return {
      emoji: "⚠️",
      title: "Run stopped",
      description: "The last run hit a problem and didn't finish. Try starting it again from Quick Actions.",
    };
  }
  if (view.phase === "idle") {
    return {
      emoji: "🕒",
      title: "Ready to start",
      description: "Today's run hasn't begun yet. It will kick off automatically on schedule.",
    };
  }
  if (view.phase === "done") {
    if (view.isDryRun) {
      return {
        emoji: "🧪",
        title: "Test run complete",
        description: "This was a practice run — nothing was published to YouTube.",
      };
    }
    if (status === "uploaded") {
      return { emoji: "🎉", title: "All done — videos are live", description: "Today's videos have been published to YouTube." };
    }
    return { emoji: "✅", title: "All done for today", description: "Every step finished — today's videos are ready." };
  }
  const step = STEPS[view.activeIndex];
  return { emoji: step.emoji, title: `${step.doing}…`, description: step.description };
}

function getBatchLine(view: PipelineView, latestBatch?: LatestBatch | null): string | null {
  if (!latestBatch || view.phase === "idle") return null;
  const videoCount = (latestBatch.shortsCount ?? 0) + (latestBatch.longFormCount ?? 0);
  if (videoCount <= 0) return null;
  const category = latestBatch.category ? latestBatch.category.replace(/_/g, " ") : null;
  const videos = `${videoCount} video${videoCount === 1 ? "" : "s"} today`;
  return category ? `${videos} · ${category}` : videos;
}

interface PhaseBadge {
  label: string;
  className: string;
  showDot: boolean;
}

function getPhaseBadge(view: PipelineView): PhaseBadge {
  switch (view.phase) {
    case "running":
      return { label: "In progress", className: "border-primary/30 bg-primary/15 text-primary", showDot: true };
    case "done":
      return view.isDryRun
        ? { label: "Test run", className: "border-purple-500/30 bg-purple-500/15 text-purple-400", showDot: false }
        : { label: "Completed", className: "border-emerald-500/30 bg-emerald-500/15 text-emerald-400", showDot: false };
    case "failed":
      return { label: "Stopped", className: "border-red-500/30 bg-red-500/15 text-red-400", showDot: false };
    default:
      return { label: "Idle", className: "border-border bg-muted/60 text-muted-foreground", showDot: false };
  }
}

function getProgressLabel(view: PipelineView): string {
  switch (view.phase) {
    case "failed":
      return "Run stopped";
    case "idle":
      return "Not started yet";
    case "done":
      return "All 9 steps complete";
    default:
      return `Step ${view.activeIndex + 1} of ${TOTAL_STEPS} · ${STEPS[view.activeIndex].label}`;
  }
}

export function PipelineStatus({ status, latestBatch }: PipelineStatusProps) {
  const view = describePipeline(status);
  const hero = getHero(status, view);
  const batchLine = getBatchLine(view, latestBatch);
  const badge = getPhaseBadge(view);

  return (
    <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-lg">Pipeline Status</CardTitle>
            <CardDescription>Today&apos;s video production, step by step</CardDescription>
          </div>
          <Badge variant="outline" className={badge.className}>
            {badge.showDot && (
              <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-primary animate-pulse" aria-hidden />
            )}
            {badge.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* What's happening right now, in plain language */}
        <div className="flex items-start gap-4 rounded-xl border border-border/40 bg-background/40 p-4">
          <div className="text-3xl leading-none" aria-hidden>{hero.emoji}</div>
          <div className="min-w-0 flex-1">
            <p className="font-semibold">{hero.title}</p>
            <p className="text-sm text-muted-foreground">{hero.description}</p>
            {batchLine && <p className="mt-1.5 text-xs text-muted-foreground/80">{batchLine}</p>}
          </div>
        </div>

        {/* Overall progress */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-foreground/80">{getProgressLabel(view)}</span>
            {view.phase !== "failed" && (
              <span className="tabular-nums text-muted-foreground">{view.percent}%</span>
            )}
          </div>
          <Progress
            value={view.phase === "failed" ? 100 : view.percent}
            className={
              "[&_[data-slot=progress-track]]:h-2 " +
              (view.phase === "failed"
                ? "[&_[data-slot=progress-indicator]]:bg-red-500/60"
                : view.phase === "done"
                  ? "[&_[data-slot=progress-indicator]]:bg-emerald-500"
                  : "[&_[data-slot=progress-indicator]]:bg-primary")
            }
          />
        </div>

        {/* Step-by-step breakdown */}
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-5 lg:grid-cols-9">
          {STEPS.map((step, i) => {
            const state = getStepState(i, view);
            return (
              <div key={step.label} className="flex flex-col items-center gap-1.5 text-center">
                <div
                  className={`flex h-10 w-10 items-center justify-center rounded-full border text-sm ${
                    state === "done"
                      ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-400"
                      : state === "active"
                        ? "border-primary/40 bg-primary/15 text-primary ring-2 ring-primary/30 animate-pulse"
                        : "border-border bg-muted/60 text-muted-foreground"
                  }`}
                >
                  {state === "done" ? "✓" : state === "active" ? <span aria-hidden>{step.emoji}</span> : i + 1}
                </div>
                <span
                  className={`text-xs leading-tight ${
                    state === "active"
                      ? "font-medium text-primary"
                      : state === "done"
                        ? "text-foreground/80"
                        : "text-muted-foreground"
                  }`}
                >
                  {step.label}
                </span>
                {state === "active" && <span className="text-[10px] font-medium text-primary">Now</span>}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
