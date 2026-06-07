"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface IntegrationStatus {
  providerKey: string;
  enabled: boolean;
  hasSecret: boolean;
  config: Record<string, unknown>;
}

interface RecapSeriesStatus {
  slug: string;
  title: string;
  type: string;
  isActive: boolean;
  episodesTotal: number;
  episodesCompleted: number;
  episodesPending: number;
}

interface RecapStatus {
  series: RecapSeriesStatus[];
  segmentsRendered: number;
}

interface Channel {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  status: string;
  niche: string | null;
  contentStyle: string | null;
  config: Record<string, unknown>;
  integrations: IntegrationStatus[];
  recapStatus?: RecapStatus | null;
  sleepCursor?: number | null;
}

// ---- One-click channel presets (client-side sugar; stored config is explicit
// flags, never a "preset name"). The user only connects YouTube afterwards. ----
const PIPELINE_LABELS: Record<string, string> = {
  fiction: "Fiction / Reddit",
  recap_shorts: "Recap Shorts",
  sleep_facts: "Sleep Facts",
};

const CINYBE_PRESET = {
  name: "CinybeShorts",
  slug: "cinybe-shorts",
  niche: "TV & movie recaps",
  contentStyle: "Fast, punchy recap Shorts cut from episodes and movies",
  config: {
    pipeline_mode: "recap_shorts",
    enable_shorts: true,
    enable_long_form: false,
    disclosure_line:
      "Recap/commentary for entertainment. All footage belongs to its respective owners.",
    recap: {
      source_mode: "user_supplied",
      inbox_path: "./data/sources/inbox/cinybe-shorts",
      target_short_seconds: 52,
      max_shorts_per_run: 5,
      footage_mode: "with_source_video",
    },
  },
} as const;

const SLEEP_PRESET = {
  name: "Sleep On Facts",
  slug: "sleep-on-facts",
  niche: "Calm facts to fall asleep to",
  contentStyle: "Calm, single-topic narration for sleep",
  config: {
    pipeline_mode: "sleep_facts",
    enable_shorts: false,
    enable_long_form: true,
    long_form_per_day: 1,
    long_form_target_minutes: 180,
    voice_personas: ["calm"],
    disclosure_line: "Educational facts narrated for relaxation.",
    sleep_facts: {
      topic_rotation: [
        "Ancient Egypt",
        "The Deep Sea",
        "Outer Space",
        "The Roman Empire",
        "Volcanoes",
        "The Human Brain",
        "Whales",
        "Antarctica",
        "The Solar System",
        "Dinosaurs",
      ],
      voice_persona: "calm",
      enable_wikipedia_grounding: true,
      // ~3-hour image-slideshow sleep videos (one calm onyx voice, 150 images).
      asset_type: "image",
      images_target: 150,
    },
  },
} as const;

interface ProviderField {
  name: string;
  label: string;
  placeholder?: string;
}

interface ProviderMeta {
  key: string;
  label: string;
  kind: string;
  secretFields: ProviderField[];
  configFields: ProviderField[];
  isYouTube?: boolean;
}

// Per-channel providers only. Shared providers (OpenAI, Gemini, Pexels,
// Pixabay, TTS, storage) are configured once under Settings → Integrations.
const YOUTUBE_META: ProviderMeta = {
  key: "youtube",
  label: "YouTube",
  kind: "youtube",
  secretFields: [],
  configFields: [],
  isYouTube: true,
};

const YOUTUBE_SCOPES = [
  "https://www.googleapis.com/auth/youtube.upload",
  "https://www.googleapis.com/auth/youtube",
  "https://www.googleapis.com/auth/youtube.readonly",
].join(" ");

interface Props {
  channels: Channel[];
  youtubeClientId: string;
  redirectUri: string;
  oauthStatus?: string;
  oauthMessage?: string;
}

export function ChannelsManager({ channels, youtubeClientId, redirectUri, oauthStatus, oauthMessage }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function call(url: string, method: string, body?: unknown): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        setError(data.error || `Request failed (${res.status})`);
        return false;
      }
      router.refresh();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      {oauthStatus === "success" && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-400">
          ✅ YouTube account connected for this channel.
        </div>
      )}
      {oauthStatus === "error" && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          ❌ {oauthMessage || "YouTube connection failed."}
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          {error}
        </div>
      )}

      <CreateChannelForm busy={busy} onCreate={(body) => call("/api/channels", "POST", body)} />

      {channels.map((channel) => (
        <ChannelCard
          key={channel.id}
          channel={channel}
          busy={busy}
          youtubeClientId={youtubeClientId}
          redirectUri={redirectUri}
          onUpdate={(patch) => call(`/api/channels/${channel.id}`, "PATCH", patch)}
          onDelete={() => call(`/api/channels/${channel.id}`, "DELETE")}
          onDuplicate={(name) => call(`/api/channels/${channel.id}/duplicate`, "POST", { name })}
          onSetSecret={(body) => call(`/api/channels/${channel.id}/secrets`, "POST", body)}
        />
      ))}
    </div>
  );
}

function CreateChannelForm({ busy, onCreate }: { busy: boolean; onCreate: (body: unknown) => Promise<boolean> }) {
  const [name, setName] = useState("");
  const [niche, setNiche] = useState("");
  const [importEnv, setImportEnv] = useState(false);

  return (
    <Card className="border-border/50 bg-card/50">
      <CardHeader>
        <CardTitle className="text-lg">➕ Create channel</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="mb-4 rounded-lg border border-border/30 bg-background/40 p-3">
          <div className="mb-2 text-xs font-medium text-muted-foreground">
            Quick start — one-click presets (connect YouTube afterwards)
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => onCreate({ ...CINYBE_PRESET })}
            >
              🎬 CinybeShorts (recap shorts)
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => onCreate({ ...SLEEP_PRESET })}
            >
              🌙 Sleep On Facts (sleep facts)
            </Button>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            CinybeShorts: shorts-only recap pipeline (drop episode files into its inbox).
            Sleep On Facts: long-form-only calm facts. Both are pre-named with their
            content profile filled in.
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[180px]">
            <label className="mb-1 block text-xs text-muted-foreground">Name</label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Horror Nights" />
          </div>
          <div className="flex-1 min-w-[180px]">
            <label className="mb-1 block text-xs text-muted-foreground">Niche</label>
            <Input value={niche} onChange={(e) => setNiche(e.target.value)} placeholder="scary stories" />
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={importEnv} onChange={(e) => setImportEnv(e.target.checked)} />
            Import keys from .env
          </label>
          <Button
            disabled={busy || !name.trim()}
            onClick={async () => {
              const ok = await onCreate({ name: name.trim(), niche: niche.trim() || undefined, importEnv });
              if (ok) {
                setName("");
                setNiche("");
                setImportEnv(false);
              }
            }}
          >
            Create
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ChannelCard({
  channel,
  busy,
  youtubeClientId,
  redirectUri,
  onUpdate,
  onDelete,
  onDuplicate,
  onSetSecret,
}: {
  channel: Channel;
  busy: boolean;
  youtubeClientId: string;
  redirectUri: string;
  onUpdate: (patch: unknown) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
  onDuplicate: (name: string) => Promise<boolean>;
  onSetSecret: (body: unknown) => Promise<boolean>;
}) {
  const byKey = new Map(channel.integrations.map((i) => [i.providerKey, i]));
  const isActive = channel.status === "active";
  const pipelineMode = (channel.config?.pipeline_mode as string) || "fiction";

  const connectUrl =
    `https://accounts.google.com/o/oauth2/auth?client_id=${encodeURIComponent(youtubeClientId)}` +
    `&redirect_uri=${encodeURIComponent(redirectUri)}&response_type=code` +
    `&scope=${encodeURIComponent(YOUTUBE_SCOPES)}&access_type=offline&prompt=consent` +
    `&state=${encodeURIComponent(channel.id)}`;

  return (
    <Card className="border-border/50 bg-card/50">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-3">
          <CardTitle className="text-lg">{channel.name}</CardTitle>
          <Badge variant="outline" className="font-mono text-[10px]">{channel.slug}</Badge>
          <Badge
            variant="outline"
            className={isActive
              ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
              : "bg-yellow-500/20 text-yellow-400 border-yellow-500/30"}
          >
            {channel.status}
          </Badge>
          <Badge variant="outline" className="bg-primary/15 text-primary border-primary/30 text-[10px]">
            {PIPELINE_LABELS[pipelineMode] || pipelineMode}
          </Badge>
          {channel.niche && <span className="text-xs text-muted-foreground">{channel.niche}</span>}
          <div className="ml-auto flex gap-2">
            <Button variant="outline" size="sm" disabled={busy}
              onClick={() => onUpdate({ status: isActive ? "paused" : "active" })}>
              {isActive ? "Pause" : "Activate"}
            </Button>
            <Button variant="outline" size="sm" disabled={busy}
              onClick={() => onDuplicate(`${channel.name} copy`)}>
              Duplicate
            </Button>
            <Button variant="outline" size="sm" disabled={busy}
              onClick={() => {
                if (confirm(`Delete channel "${channel.name}"? This only works if it has no history.`)) onDelete();
              }}>
              Delete
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <PipelinePanel channel={channel} busy={busy} onUpdate={onUpdate} />
        <ContentSettings channel={channel} busy={busy} onUpdate={onUpdate} />
        <div className="grid gap-3 md:grid-cols-2">
          <IntegrationRow
            meta={YOUTUBE_META}
            status={byKey.get("youtube")}
            busy={busy}
            connectUrl={connectUrl}
            onSave={(secrets, config, enabled) =>
              onSetSecret({ provider: "youtube", secrets, config, enabled })}
          />
          <div className="rounded-lg border border-border/30 bg-background/40 p-3 text-xs text-muted-foreground">
            Shared API keys (OpenAI, Gemini, Pexels, Pixabay, TTS, storage) are configured once under{" "}
            <a href="/settings" className="text-primary underline">Settings → Integrations</a>.
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function PipelinePanel({
  channel,
  busy,
  onUpdate,
}: {
  channel: Channel;
  busy: boolean;
  onUpdate: (patch: unknown) => Promise<boolean>;
}) {
  const mode = (channel.config?.pipeline_mode as string) || "fiction";
  if (mode === "recap_shorts") return <RecapPanel channel={channel} />;
  if (mode === "sleep_facts") return <SleepPanel channel={channel} busy={busy} onUpdate={onUpdate} />;
  return null;
}

function RecapPanel({ channel }: { channel: Channel }) {
  const recap = (channel.config?.recap as Record<string, unknown>) || {};
  const status = channel.recapStatus;
  const inbox = (recap.inbox_path as string) || "./data/sources/inbox";
  const footage = (recap.footage_mode as string) || "with_source_video";
  const target = (recap.target_short_seconds as number) ?? 52;
  const perRun = (recap.max_shorts_per_run as number) ?? 5;

  return (
    <div className="rounded-lg border border-border/30 bg-background/40 p-3 text-xs">
      <div className="mb-2 text-sm font-medium">🎬 Recap pipeline</div>
      <div className="text-muted-foreground">
        Inbox: <code className="font-mono">{inbox}</code> · footage:{" "}
        <span className="text-foreground">{footage}</span> · ~{target}s · {perRun}/run
      </div>
      <div className="mt-2 space-y-1">
        {status && status.series.length > 0 ? (
          status.series.map((s) => (
            <div key={s.slug} className="flex items-center gap-2">
              <span className={s.isActive ? "text-emerald-400" : "text-muted-foreground"}>
                {s.isActive ? "★" : "·"}
              </span>
              <span className="text-foreground">{s.title}</span>
              <span className="text-muted-foreground">
                ({s.episodesCompleted}/{s.episodesTotal} episodes done)
              </span>
            </div>
          ))
        ) : (
          <div className="text-muted-foreground">
            No series yet — drop episode files (e.g. <code>Show Name S01E03.mkv</code>) into the inbox.
          </div>
        )}
      </div>
      <div className="mt-2 text-muted-foreground">
        {status?.segmentsRendered ?? 0} Shorts rendered so far. Manage series/episodes with{" "}
        <code className="font-mono">npm run worker -- recap …</code> (see docs/multi-channel.md).
      </div>
    </div>
  );
}

function SleepPanel({
  channel,
  busy,
  onUpdate,
}: {
  channel: Channel;
  busy: boolean;
  onUpdate: (patch: unknown) => Promise<boolean>;
}) {
  const sleep = (channel.config?.sleep_facts as Record<string, unknown>) || {};
  const rotation = Array.isArray(sleep.topic_rotation) ? (sleep.topic_rotation as string[]) : [];
  // Target minutes is the top-level config value (what the Content Settings
  // control edits and the worker reads); fall back to the nested value, then 20.
  // Sleep length lives in the nested sleep_facts config (what the worker reads);
  // fall back to a legacy top-level value, then the ~3h default.
  const topLevelTarget = channel.config?.long_form_target_minutes;
  const initialTarget =
    typeof sleep.long_form_target_minutes === "number"
      ? sleep.long_form_target_minutes
      : typeof topLevelTarget === "number"
        ? topLevelTarget
        : 180;
  const cursor = channel.sleepCursor ?? 0;
  const nextTopic = rotation.length > 0 ? rotation[cursor % rotation.length] : "(add topics below)";

  const rotationKey = rotation.join("\n");
  const [topics, setTopics] = useState(rotationKey);
  const [targetMin, setTargetMin] = useState(String(initialTarget));
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  // Re-sync the textarea when the channel's rotation changes server-side (a worker
  // run or another tab's save), so a later save can't overwrite the newer value
  // with stale first-mount state.
  useEffect(() => {
    setTopics(rotationKey);
  }, [channel.id, rotationKey]);

  async function save() {
    setSavedMsg(null);
    const list = topics
      .split("\n")
      .map((t) => t.trim())
      .filter(Boolean);
    const minutes = Math.max(1, Math.min(240, Number(targetMin) || 180));
    const merged = { ...sleep, topic_rotation: list, long_form_target_minutes: minutes };
    const ok = await onUpdate({ config: { sleep_facts: merged } });
    if (ok) setSavedMsg(`Saved ${list.length} topic(s) · ~${minutes} min/video.`);
  }

  return (
    <div className="rounded-lg border border-border/30 bg-background/40 p-3 text-xs">
      <div className="mb-2 text-sm font-medium">🌙 Sleep pipeline</div>
      <div className="text-muted-foreground">
        Calm voice locked · image slideshow · ~{targetMin} min/video · next topic:{" "}
        <span className="text-foreground">{nextTopic}</span>
      </div>
      <label className="mt-2 mb-1 block text-[10px] text-muted-foreground">
        Target minutes per video (~180 = 3 hours)
      </label>
      <Input
        type="number"
        min={1}
        max={240}
        value={targetMin}
        onChange={(e) => setTargetMin(e.target.value)}
        className="w-28 text-xs"
      />
      <label className="mt-2 mb-1 block text-[10px] text-muted-foreground">
        Topic rotation (one per line — never mix domains in a video)
      </label>
      <Textarea
        value={topics}
        onChange={(e) => setTopics(e.target.value)}
        rows={6}
        className="font-mono text-xs"
        placeholder={"Ancient Egypt\nThe Deep Sea\nOuter Space"}
      />
      <div className="mt-2 flex items-center gap-3">
        <Button size="sm" disabled={busy} onClick={save}>
          Save sleep settings
        </Button>
        {savedMsg && <span className="text-[11px] text-emerald-400">{savedMsg}</span>}
      </div>
    </div>
  );
}

function ContentSettings({
  channel,
  busy,
  onUpdate,
}: {
  channel: Channel;
  busy: boolean;
  onUpdate: (patch: unknown) => Promise<boolean>;
}) {
  const cfg = channel.config || {};
  const num = (key: string, fallback: number): string => {
    const v = cfg[key];
    return typeof v === "number" ? String(v) : String(fallback);
  };

  const [enableShorts, setEnableShorts] = useState(cfg.enable_shorts !== false);
  const [enableLong, setEnableLong] = useState(cfg.enable_long_form !== false);
  const [shortsMin, setShortsMin] = useState(num("shorts_per_day_min", 3));
  const [shortsMax, setShortsMax] = useState(num("shorts_per_day_max", 5));
  const [segMin, setSegMin] = useState(num("long_form_segments_min", 2));
  const [segMax, setSegMax] = useState(num("long_form_segments_max", 5));
  const [targetMin, setTargetMin] = useState(num("long_form_target_minutes", 10));
  const [includesShorts, setIncludesShorts] = useState(cfg.long_form_includes_shorts !== false);
  const [niche, setNiche] = useState(channel.niche ?? "");
  const [contentStyle, setContentStyle] = useState(channel.contentStyle ?? "");
  const [localErr, setLocalErr] = useState<string | null>(null);

  const atLeastOne = enableShorts || enableLong;

  async function save() {
    setLocalErr(null);
    if (!atLeastOne) {
      setLocalErr("Enable Shorts or long-form — a channel must produce at least one output.");
      return;
    }
    const config: Record<string, unknown> = {
      enable_shorts: enableShorts,
      enable_long_form: enableLong,
    };
    if (enableShorts) {
      const mn = Number(shortsMin);
      const mx = Number(shortsMax);
      if (mn > mx) {
        setLocalErr("Shorts per day: min cannot exceed max.");
        return;
      }
      config.shorts_per_day_min = mn;
      config.shorts_per_day_max = mx;
    }
    if (enableLong) {
      const mn = Number(segMin);
      const mx = Number(segMax);
      if (mn > mx) {
        setLocalErr("Long-form segments: min cannot exceed max.");
        return;
      }
      config.long_form_segments_min = mn;
      config.long_form_segments_max = mx;
      config.long_form_target_minutes = Number(targetMin);
      config.long_form_includes_shorts = includesShorts;
      config.long_form_per_day = 1; // capped at one long-form video per day for now
    }
    await onUpdate({ niche, contentStyle, config });
  }

  return (
    <div className="rounded-lg border border-border/30 bg-background/40 p-3">
      <div className="mb-3 text-sm font-medium">Content</div>

      <div className="mb-3 flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" checked={enableShorts} onChange={(e) => setEnableShorts(e.target.checked)} />
          Generate Shorts
        </label>
        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" checked={enableLong} onChange={(e) => setEnableLong(e.target.checked)} />
          Generate long-form video
        </label>
      </div>

      {enableShorts && (
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div className="w-28">
            <label className="mb-1 block text-[10px] text-muted-foreground">Shorts/day min</label>
            <Input type="number" min={1} max={10} value={shortsMin}
              onChange={(e) => setShortsMin(e.target.value)} className="text-xs" />
          </div>
          <div className="w-28">
            <label className="mb-1 block text-[10px] text-muted-foreground">Shorts/day max</label>
            <Input type="number" min={1} max={10} value={shortsMax}
              onChange={(e) => setShortsMax(e.target.value)} className="text-xs" />
          </div>
        </div>
      )}

      {enableLong && (
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <div className="w-28">
            <label className="mb-1 block text-[10px] text-muted-foreground">Segments min</label>
            <Input type="number" min={1} max={20} value={segMin}
              onChange={(e) => setSegMin(e.target.value)} className="text-xs" />
          </div>
          <div className="w-28">
            <label className="mb-1 block text-[10px] text-muted-foreground">Segments max</label>
            <Input type="number" min={1} max={20} value={segMax}
              onChange={(e) => setSegMax(e.target.value)} className="text-xs" />
          </div>
          <div className="w-32">
            <label className="mb-1 block text-[10px] text-muted-foreground">Target minutes</label>
            {/* Up to 240 so Sleep On Facts can target ~3h (180) without the
                spinner silently clamping a long-form value down to 60. */}
            <Input type="number" min={1} max={240} value={targetMin}
              onChange={(e) => setTargetMin(e.target.value)} className="text-xs" />
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={includesShorts} onChange={(e) => setIncludesShorts(e.target.checked)} />
            Include Shorts in long-form
          </label>
        </div>
      )}

      <div className="mb-3 grid gap-3 md:grid-cols-2">
        <div>
          <label className="mb-1 block text-[10px] text-muted-foreground">Niche</label>
          <Input value={niche} onChange={(e) => setNiche(e.target.value)}
            placeholder="e.g. relationship drama" className="text-xs" />
        </div>
        <div>
          <label className="mb-1 block text-[10px] text-muted-foreground">Content style</label>
          <Input value={contentStyle} onChange={(e) => setContentStyle(e.target.value)}
            placeholder="e.g. 100+ random facts, calm narration for sleep" className="text-xs" />
        </div>
      </div>

      {localErr && <p className="mb-2 text-xs text-red-400">{localErr}</p>}

      <Button size="sm" disabled={busy} onClick={save}>Save content settings</Button>
    </div>
  );
}

function IntegrationRow({
  meta,
  status,
  busy,
  connectUrl,
  onSave,
}: {
  meta: ProviderMeta;
  status?: IntegrationStatus;
  busy: boolean;
  connectUrl: string;
  onSave: (secrets: Record<string, string>, config: Record<string, string>, enabled: boolean) => Promise<boolean>;
}) {
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [config, setConfig] = useState<Record<string, string>>({});
  const enabled = status?.enabled ?? false;
  const configured = meta.isYouTube
    ? status?.hasSecret ?? false
    : meta.secretFields.length === 0
      ? enabled
      : status?.hasSecret ?? false;

  return (
    <div className="rounded-lg border border-border/30 bg-background/40 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">{meta.label}</span>
        <Badge
          variant="outline"
          className={configured
            ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30 text-[10px]"
            : "bg-muted text-muted-foreground text-[10px]"}
        >
          {configured ? "Configured" : "Not set"}
        </Badge>
        {enabled && configured && (
          <span className="ml-auto text-[10px] text-emerald-400">enabled</span>
        )}
      </div>

      {meta.isYouTube ? (
        <a
          href={connectUrl}
          className="inline-block rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:opacity-90"
        >
          🔌 {configured ? "Reconnect" : "Connect"} YouTube
        </a>
      ) : (
        <div className="space-y-2">
          {meta.secretFields.map((f) => (
            <Input
              key={f.name}
              type="password"
              placeholder={f.placeholder || `${f.label} (leave blank to keep)`}
              value={secrets[f.name] || ""}
              onChange={(e) => setSecrets((s) => ({ ...s, [f.name]: e.target.value }))}
              className="text-xs"
            />
          ))}
          {meta.configFields.map((f) => (
            <Input
              key={f.name}
              placeholder={f.label}
              value={config[f.name] ?? (status?.config?.[f.name] as string) ?? ""}
              onChange={(e) => setConfig((c) => ({ ...c, [f.name]: e.target.value }))}
              className="text-xs"
            />
          ))}
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={busy}
              onClick={async () => {
                const ok = await onSave(secrets, config, true);
                if (ok) setSecrets({});
              }}
            >
              Save &amp; enable
            </Button>
            {enabled && (
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => onSave({}, {}, false)}
              >
                Disable
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
