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

// Mirrors the worker integration registry (non-secret descriptors only).
const PROVIDER_META: ProviderMeta[] = [
  { key: "text.openai", label: "OpenAI (text)", kind: "text", secretFields: [{ name: "api_key", label: "API key", placeholder: "sk-..." }], configFields: [{ name: "model", label: "Model" }] },
  { key: "text.gemini", label: "Gemini (text)", kind: "text", secretFields: [{ name: "api_key", label: "API key" }], configFields: [{ name: "model", label: "Model" }] },
  { key: "tts.openai", label: "OpenAI (TTS)", kind: "tts", secretFields: [{ name: "api_key", label: "API key", placeholder: "sk-..." }], configFields: [] },
  { key: "tts.gemini", label: "Gemini (TTS)", kind: "tts", secretFields: [{ name: "api_key", label: "API key" }], configFields: [] },
  { key: "assets.pexels", label: "Pexels", kind: "assets", secretFields: [{ name: "api_key", label: "API key" }], configFields: [] },
  { key: "assets.pixabay", label: "Pixabay", kind: "assets", secretFields: [{ name: "api_key", label: "API key" }], configFields: [] },
  { key: "youtube", label: "YouTube", kind: "youtube", secretFields: [], configFields: [], isYouTube: true },
  { key: "storage.local", label: "Local storage", kind: "storage", secretFields: [], configFields: [{ name: "path", label: "Path" }] },
  { key: "storage.r2", label: "Cloudflare R2", kind: "storage", secretFields: [{ name: "access_key_id", label: "Access key id" }, { name: "secret_access_key", label: "Secret access key" }], configFields: [{ name: "account_id", label: "Account id" }, { name: "bucket", label: "Bucket" }] },
];

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
      <CardContent>
        <div className="grid gap-3 md:grid-cols-2">
          {PROVIDER_META.map((meta) => {
            const status = byKey.get(meta.key);
            return (
              <IntegrationRow
                key={meta.key}
                meta={meta}
                status={status}
                busy={busy}
                connectUrl={connectUrl}
                onSave={(secrets, config, enabled) =>
                  onSetSecret({ provider: meta.key, secrets, config, enabled })}
              />
            );
          })}
        </div>
      </CardContent>
    </Card>
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
