import { NextResponse } from 'next/server';
import { runWorkerCommand } from '@/lib/worker-cli';
import { APP_SCOPED_PROVIDERS, CHANNEL_SCOPED_PROVIDERS } from '@/lib/integration-scope';

/**
 * Set (encrypted) secrets and/or non-secret config for a channel integration.
 *
 * Only channel-scoped providers may be set per channel. Shared providers
 * (OpenAI/Gemini/Pexels/Pixabay/TTS/storage) are configured once under
 * Settings → Integrations; writing them per channel would be silently ignored
 * by the resolver, so we reject them here with a clear message.
 *
 * Secrets are forwarded to the worker, which owns all encryption. This handler
 * NEVER returns secret values to the client — only a success flag and the
 * provider's resulting status. Authorize/rate-limit at the edge in production.
 */

export async function POST(
  request: Request,
  ctx: { params: Promise<{ id: string }> }
) {
  // Next 15+ delivers route params as a Promise; awaiting a plain value is safe too.
  const params = await ctx.params;
  const channelId = params.id;

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

  if (provider && APP_SCOPED_PROVIDERS.has(provider)) {
    return NextResponse.json(
      {
        success: false,
        error: `${provider} is a shared provider — configure it once under Settings → Integrations, not per channel.`,
      },
      { status: 400 }
    );
  }

  if (!provider || !CHANNEL_SCOPED_PROVIDERS.has(provider)) {
    return NextResponse.json(
      { success: false, error: `Unknown or non-channel integration provider: ${provider}` },
      { status: 400 }
    );
  }

  const args = ['channel', 'set-secret', '--channel', channelId, '--provider', provider];
  for (const [k, v] of Object.entries(secrets)) {
    if (typeof v === 'string' && v.length > 0) args.push('--secret', `${k}=${v}`);
  }
  for (const [k, v] of Object.entries(config)) {
    if (v !== undefined && v !== null) args.push('--config', `${k}=${v}`);
  }
  if (enabled === true) args.push('--enable');
  if (enabled === false) args.push('--disable');

  const result = await runWorkerCommand(args);
  if (!result.ok) {
    return NextResponse.json(
      { success: false, error: result.stderr || 'Worker command failed' },
      { status: 500 }
    );
  }

  // Never return secret values — only confirmation.
  return NextResponse.json({ success: true, provider });
}
