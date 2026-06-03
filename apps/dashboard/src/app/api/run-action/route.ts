import { NextResponse } from 'next/server';
import { exec } from 'child_process';
import path from 'path';
import fs from 'fs';
import { getActiveChannelId } from '@/lib/channels';

const ALLOWED_ACTIONS: Record<string, string> = {
  'worker:daily': 'npm run worker:daily',
  'worker:render': 'npm run worker:render',
  'worker:upload': 'npm run worker:upload',
  'worker:analytics': 'npm run worker:analytics',
  'worker:full': 'npm run worker:full',
};

// Only forward channel ids that look safe to interpolate into the command.
function isSafeChannelId(value: string | null): value is string {
  return !!value && /^[A-Za-z0-9_-]{1,64}$/.test(value);
}

function getRootDir(): string {
  let cwd = process.cwd();
  const workerFolder = ['worker'].join('');
  // Traverse up to find root package.json
  for (let i = 0; i < 3; i++) {
    if (fs.existsSync(path.join(cwd, 'package.json')) && fs.existsSync(path.join(cwd, 'apps', workerFolder))) {
      return cwd;
    }
    cwd = path.dirname(cwd);
  }
  return process.cwd();
}

export async function POST(request: Request) {
  try {
    const body = await request.json().catch(() => ({}));
    const { action, allActive } = body as { action?: string; allActive?: boolean };

    if (!action || !ALLOWED_ACTIONS[action]) {
      return NextResponse.json(
        { success: false, error: `Invalid action: ${action}` },
        { status: 400 }
      );
    }

    // Target the active channel (or all active channels) so runs are scoped.
    let command = ALLOWED_ACTIONS[action];
    if (allActive) {
      command += ' -- --all-active';
    } else {
      const channelId = await getActiveChannelId();
      if (isSafeChannelId(channelId)) {
        command += ` -- --channel ${channelId}`;
      }
    }

    const rootDir = getRootDir();

    console.log(`[API] Starting background action: "${command}" in ${rootDir}`);

    // Execute the command in the background (asynchronous)
    const child = exec(command, {
      cwd: rootDir,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
      }
    });

    child.stdout?.on('data', (data) => {
      console.log(`[Worker Output] ${data.toString().trim()}`);
    });

    child.stderr?.on('data', (data) => {
      console.error(`[Worker Error] ${data.toString().trim()}`);
    });

    child.on('close', (code) => {
      console.log(`[Worker Exit] "${command}" exited with code ${code}`);
    });

    return NextResponse.json({
      success: true,
      message: `Action ${action} started in the background.`,
    });
  } catch (error: any) {
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  }
}
