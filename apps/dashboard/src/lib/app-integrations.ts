import { query } from '@/lib/db';

/**
 * Server-side reader for app-level (shared) integrations.
 *
 * Like channel integrations, secrets are never selected here — only whether an
 * encrypted blob exists (`hasSecret`) and non-secret config. All encryption and
 * authoritative validation happen in the worker.
 */

export interface AppIntegrationRow {
  providerKey: string;
  enabled: boolean;
  hasSecret: boolean;
  config: Record<string, unknown>;
  status: string;
}

export async function getAppIntegrations(): Promise<AppIntegrationRow[]> {
  try {
    const rows = await query<{
      providerKey: string;
      enabled: number | boolean;
      hasSecret: number | boolean;
      config: Record<string, unknown>;
      status: string;
    }>(
      `SELECT provider_key,
              enabled,
              (secrets_encrypted IS NOT NULL AND secrets_encrypted != '') AS has_secret,
              config,
              status
       FROM app_integrations`
    );
    return rows.map((r) => ({
      providerKey: r.providerKey,
      enabled: Boolean(r.enabled),
      hasSecret: Boolean(r.hasSecret),
      config: (r.config as Record<string, unknown>) || {},
      status: r.status,
    }));
  } catch {
    // Table may not exist yet on an un-migrated database.
    return [];
  }
}
