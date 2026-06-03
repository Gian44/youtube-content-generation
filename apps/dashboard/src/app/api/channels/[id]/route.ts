import { NextResponse } from 'next/server';
import { runWorkerCommand } from '@/lib/worker-cli';
import { getChannel } from '@/lib/channels';
import { query } from '@/lib/db';

/**
 * Enforce the content-profile invariants on a merged channel config so an
 * un-runnable config can't be persisted via the API (mirrors the shared Zod
 * refinements and the worker's _validate_content_config). Returns an error
 * message, or null when valid.
 */
function validateContentConfig(cfg: Record<string, unknown>): string | null {
  if (cfg.enable_shorts === false && cfg.enable_long_form === false) {
    return 'A channel must produce at least one output (enable Shorts or long-form).';
  }
  const smin = cfg.shorts_per_day_min;
  const smax = cfg.shorts_per_day_max;
  if (typeof smin === 'number' && typeof smax === 'number' && smin > smax) {
    return 'shorts_per_day_min cannot exceed shorts_per_day_max.';
  }
  const lmin = cfg.long_form_segments_min;
  const lmax = cfg.long_form_segments_max;
  if (typeof lmin === 'number' && typeof lmax === 'number' && lmin > lmax) {
    return 'long_form_segments_min cannot exceed long_form_segments_max.';
  }
  return null;
}

/** Update editable channel fields. Non-secret config is written directly to the DB. */
export async function PATCH(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;

  let body: {
    name?: string;
    description?: string;
    niche?: string;
    contentStyle?: string;
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
  if (body.contentStyle !== undefined) args.push('--content-style', body.contentStyle);
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
    const invalid = validateContentConfig(merged);
    if (invalid) {
      return NextResponse.json({ success: false, error: invalid }, { status: 400 });
    }
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
