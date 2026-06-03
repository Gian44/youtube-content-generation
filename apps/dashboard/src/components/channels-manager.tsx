"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface IntegrationStatus {
  providerKey: string;
  enabled: boolean;
  hasSecret: boolean;
  config: Record<string, unknown>;
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
}

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
            <Input type="number" min={1} max={60} value={targetMin}
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
