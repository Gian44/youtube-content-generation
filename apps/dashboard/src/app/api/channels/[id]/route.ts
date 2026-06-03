import { NextResponse } from 'next/server';
import { runWorkerCommand } from '@/lib/worker-cli';
import { getChannel } from '@/lib/channels';
import { query } from '@/lib/db';

/** Update editable channel fields. Non-secret config is written directly to the DB. */
export async function PATCH(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;

  let body: {
    name?: string;
    description?: string;
    niche?: string;
    status?: 'active' | 'paused';
    config?: Record<string, unknown>;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ success: false, error: 'Invalid JSON body' }, { status: 400 });
  }

  const channel = await getChannel(id);
  if (!channel) {
    return NextResponse.json({ success: false, error: 'Channel not found' }, { status: 404 });
  }

  // String/status fields go through the worker for consistency + validation.
  const args = ['channel', 'update', '--channel', channel.id];
  if (body.name !== undefined) args.push('--name', body.name);
  if (body.description !== undefined) args.push('--description', body.description);
  if (body.niche !== undefined) args.push('--niche', body.niche);
  if (body.status !== undefined) args.push('--status', body.status);
  if (args.length > 4) {
    const result = await runWorkerCommand(args);
    if (!result.ok) {
      return NextResponse.json(
        { success: false, error: result.stderr || 'Update failed' },
        { status: 500 }
      );
    }
  }

  // Typed config (quotas, weights, ratios...) is non-secret: merge + write directly.
  if (body.config && typeof body.config === 'object') {
    const merged = { ...(channel.config || {}), ...body.config };
    await query(`UPDATE channels SET config = $1, updated_at = $2 WHERE id = $3`, [
      JSON.stringify(merged),
      new Date().toISOString(),
      channel.id,
    ]);
  }

  return NextResponse.json({ success: true });
}

export async function DELETE(_request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const channel = await getChannel(id);
  if (!channel) {
    return NextResponse.json({ success: false, error: 'Channel not found' }, { status: 404 });
  }

  const result = await runWorkerCommand(['channel', 'delete', '--channel', channel.id]);
  if (!result.ok) {
    return NextResponse.json(
      { success: false, error: result.stderr || 'Delete failed' },
      { status: 500 }
    );
  }
  // The worker prints a friendly message (and refuses if the channel has history).
  if (/has \d+ batch/.test(result.stdout)) {
    return NextResponse.json({ success: false, error: result.stdout }, { status: 409 });
  }
  return NextResponse.json({ success: true });
}
