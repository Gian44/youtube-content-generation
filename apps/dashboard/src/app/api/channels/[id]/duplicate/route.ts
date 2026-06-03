import { NextResponse } from 'next/server';
import { runWorkerCommand } from '@/lib/worker-cli';
import { getChannel } from '@/lib/channels';

export async function POST(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;

  let body: { name?: string; slug?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ success: false, error: 'Invalid JSON body' }, { status: 400 });
  }
  if (!body.name || !body.name.trim()) {
    return NextResponse.json({ success: false, error: 'name is required' }, { status: 400 });
  }

  const channel = await getChannel(id);
  if (!channel) {
    return NextResponse.json({ success: false, error: 'Channel not found' }, { status: 404 });
  }

  const args = ['channel', 'duplicate', '--channel', channel.id, '--name', body.name.trim()];
  if (body.slug) args.push('--slug', body.slug);

  const result = await runWorkerCommand(args);
  if (!result.ok) {
    return NextResponse.json(
      { success: false, error: result.stderr || 'Duplicate failed' },
      { status: 500 }
    );
  }
  return NextResponse.json({ success: true });
}
