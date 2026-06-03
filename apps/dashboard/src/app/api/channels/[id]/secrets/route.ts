import { NextResponse } from 'next/server';
import { runWorkerCommand } from '@/lib/worker-cli';

/**
 * Set (encrypted) secrets and/or non-secret config for a channel integration.
 *
 * Secrets are forwarded to the worker, which owns all encryption. This handler
 * NEVER returns secret values to the client — only a success flag and the
 * provider's resulting status. Authorize/rate-limit at the edge in production.
 */

const ALLOWED_PROVIDERS = new Set([
  'text.openai',
  'text.gemini',
  'tts.openai',
  'tts.gemini',
  'assets.pexels',
  'assets.pixabay',
  'youtube',
  'storage.local',
  'storage.r2',
]);

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

  if (!provider || !ALLOWED_PROVIDERS.has(provider)) {
    return NextResponse.json(
      { success: false, error: `Unknown integration provider: ${provider}` },
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
