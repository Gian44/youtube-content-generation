"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Settings → Integrations: configure shared (app-level) provider credentials
 * once for the whole app. Secrets are sent to the worker (which encrypts them)
 * and never returned to the client. YouTube is configured per channel, not here.
 */

interface AppIntegrationStatus {
  providerKey: string;
  enabled: boolean;
  hasSecret: boolean;
  config: Record<string, unknown>;
}

interface ProviderField {
  name: string;
  label: string;
  placeholder?: string;
}

interface AppProviderMeta {
  key: string;
  label: string;
  secretFields: ProviderField[];
  configFields: ProviderField[];
}

// Mirrors the worker registry's app-scoped providers (non-secret descriptors).
const APP_PROVIDER_META: AppProviderMeta[] = [
  { key: "text.openai", label: "OpenAI (text)", secretFields: [{ name: "api_key", label: "API key", placeholder: "sk-..." }], configFields: [{ name: "model", label: "Model" }] },
  { key: "text.gemini", label: "Gemini (text)", secretFields: [{ name: "api_key", label: "API key" }], configFields: [{ name: "model", label: "Model" }] },
  { key: "tts.openai", label: "OpenAI (TTS)", secretFields: [{ name: "api_key", label: "API key", placeholder: "sk-..." }], configFields: [] },
  { key: "tts.gemini", label: "Gemini (TTS)", secretFields: [{ name: "api_key", label: "API key" }], configFields: [] },
  { key: "assets.pexels", label: "Pexels", secretFields: [{ name: "api_key", label: "API key" }], configFields: [] },
  { key: "assets.pixabay", label: "Pixabay", secretFields: [{ name: "api_key", label: "API key" }], configFields: [] },
  { key: "storage.local", label: "Local storage", secretFields: [], configFields: [{ name: "path", label: "Path" }] },
  { key: "storage.r2", label: "Cloudflare R2", secretFields: [{ name: "access_key_id", label: "Access key id" }, { name: "secret_access_key", label: "Secret access key" }], configFields: [{ name: "account_id", label: "Account id" }, { name: "bucket", label: "Bucket" }] },
];

export function AppIntegrationsManager({ integrations }: { integrations: AppIntegrationStatus[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const byKey = new Map(integrations.map((i) => [i.providerKey, i]));

  async function save(
    provider: string,
    secrets: Record<string, string>,
    config: Record<string, string>,
    enabled: boolean
  ): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/settings/integrations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, secrets, config, enabled }),
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
    <div className="space-y-3">
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400">
          {error}
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {APP_PROVIDER_META.map((meta) => (
          <AppIntegrationRow
            key={meta.key}
            meta={meta}
            status={byKey.get(meta.key)}
            busy={busy}
            onSave={(secrets, config, enabled) => save(meta.key, secrets, config, enabled)}
          />
        ))}
      </div>
    </div>
  );
}

function AppIntegrationRow({
  meta,
  status,
  busy,
  onSave,
}: {
  meta: AppProviderMeta;
  status?: AppIntegrationStatus;
  busy: boolean;
  onSave: (secrets: Record<string, string>, config: Record<string, string>, enabled: boolean) => Promise<boolean>;
}) {
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [config, setConfig] = useState<Record<string, string>>({});
  const enabled = status?.enabled ?? false;
  const configured =
    meta.secretFields.length === 0 ? enabled : status?.hasSecret ?? false;

  return (
    <div className="rounded-lg border border-border/30 bg-background/40 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">{meta.label}</span>
        <Badge
          variant="outline"
          className={
            configured
              ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30 text-[10px]"
              : "bg-muted text-muted-foreground text-[10px]"
          }
        >
          {configured ? "Configured" : "Not set"}
        </Badge>
        {enabled && configured && <span className="ml-auto text-[10px] text-emerald-400">enabled</span>}
      </div>

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
            <Button variant="outline" size="sm" disabled={busy} onClick={() => onSave({}, {}, false)}>
              Disable
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
