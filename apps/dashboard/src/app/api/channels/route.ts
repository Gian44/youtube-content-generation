import { NextResponse } from 'next/server';
import { runWorkerCommand } from '@/lib/worker-cli';
import { getChannels } from '@/lib/channels';

export async function GET() {
  const channels = await getChannels();
  return NextResponse.json({ success: true, channels });
}

export async function POST(request: Request) {
  let body: {
    name?: string;
    slug?: string;
    niche?: string;
    description?: string;
    contentStyle?: string;
    importEnv?: boolean;
    // Optional content-config preset (pipeline_mode, enable flags, recap/sleep
    // blocks...). Passed to the worker via stdin so it never hits the argv list.
    config?: Record<string, unknown>;
  };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ success: false, error: 'Invalid JSON body' }, { status: 400 });
  }

  if (!body.name || !body.name.trim()) {
    return NextResponse.json({ success: false, error: 'name is required' }, { status: 400 });
  }

  const args = ['channel', 'create', '--name', body.name.trim()];
  if (body.slug) args.push('--slug', body.slug);
  if (body.niche) args.push('--niche', body.niche);
  if (body.description) args.push('--description', body.description);
  if (body.contentStyle) args.push('--content-style', body.contentStyle);
  if (body.importEnv) args.push('--import-env');

  let input: string | undefined;
  if (body.config && typeof body.config === 'object') {
    args.push('--config-stdin');
    input = JSON.stringify(body.config);
  }

  const result = await runWorkerCommand(args, input);
  // The CLI prints validation errors (e.g. mode/output mismatch) to stdout and
  // exits 0, so confirm success by the explicit "Created channel" marker.
  if (!result.ok || !/Created channel/.test(result.stdout)) {
    return NextResponse.json(
      { success: false, error: result.stdout || result.stderr || 'Failed to create channel' },
      { status: 500 }
    );
  }
  return NextResponse.json({ success: true });
}
