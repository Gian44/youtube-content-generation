import { NextResponse } from 'next/server';
import { runWorkerCommand } from '@/lib/worker-cli';
import { APP_SCOPED_PROVIDERS } from '@/lib/integration-scope';

/**
 * Configure a shared, app-level integration (used by every channel).
 *
 * Secrets are sent to the worker over **stdin as JSON** (never argv), so they do
 * not appear in the process argument list. Non-secret config (model, bucket,
 * path) is passed as argv — acceptable on a single-user local desktop app. The
 * worker owns all encryption; this handler never returns secret values to the
 * client. Authorize/rate-limit at the edge in production.
 */

export async function POST(request: Request) {
  let body: {
    provider?: string;
    secrets?: Record<string, string>;
    config?: Record<string, string>;
    enabled?: boolean;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ success: false, error: 'Invalid JSON body' }, { status: 400 });
  }

  const { provider, secrets = {}, config = {}, enabled } = body;

  if (!provider || !APP_SCOPED_PROVIDERS.has(provider)) {
    return NextResponse.json(
      { success: false, error: `Unknown shared integration provider: ${provider}` },
      { status: 400 }
    );
  }

  const args = ['settings', 'set-secret', '--provider', provider];
  for (const [k, v] of Object.entries(config)) {
    if (v !== undefined && v !== null) args.push('--config', `${k}=${v}`);
  }
  if (enabled === true) args.push('--enable');
  if (enabled === false) args.push('--disable');

  // Secrets travel via stdin JSON, not argv.
  const cleanSecrets = Object.fromEntries(
    Object.entries(secrets).filter(([, v]) => typeof v === 'string' && v.length > 0)
  );
  let input: string | undefined;
  if (Object.keys(cleanSecrets).length > 0) {
    args.push('--secrets-stdin');
    input = JSON.stringify(cleanSecrets);
  }

  const result = await runWorkerCommand(args, input);
  if (!result.ok) {
    return NextResponse.json(
      { success: false, error: result.stderr || 'Worker command failed' },
      { status: 500 }
    );
  }

  // Never return secret values — only confirmation.
  return NextResponse.json({ success: true, provider });
}
