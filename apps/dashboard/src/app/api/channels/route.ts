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
    importEnv?: boolean;
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
  if (body.importEnv) args.push('--import-env');

  const result = await runWorkerCommand(args);
  if (!result.ok) {
    return NextResponse.json(
      { success: false, error: result.stderr || 'Failed to create channel' },
      { status: 500 }
    );
  }
  return NextResponse.json({ success: true });
}
