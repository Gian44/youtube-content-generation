import { NextResponse } from 'next/server';
import { runWorkerCommand } from '@/lib/worker-cli';

/**
 * YouTube OAuth callback.
 *
 * The `state` parameter carries the channel id/slug the connect flow was
 * started for. The exchanged refresh token is stored ENCRYPTED, per channel,
 * via the worker CLI — it is never written to `.env` and never returned to the
 * client.
 */
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get('code');
  const error = searchParams.get('error');
  const channelRef = searchParams.get('state') || 'default';

  const redirectTo = (status: string, message?: string) => {
    const params = new URLSearchParams({ status });
    if (message) params.set('message', message);
    params.set('channel', channelRef);
    return NextResponse.redirect(new URL(`/channels?${params.toString()}`, request.url));
  };

  if (error) {
    return redirectTo('error', error);
  }
  if (!code) {
    return redirectTo('error', 'No authorization code provided');
  }

  try {
    const clientId = process.env.YOUTUBE_CLIENT_ID;
    const clientSecret = process.env.YOUTUBE_CLIENT_SECRET;
    const redirectUri =
      process.env.YOUTUBE_REDIRECT_URI || 'http://localhost:3000/api/auth/youtube/callback';

    if (!clientId || !clientSecret) {
      return redirectTo(
        'error',
        'YOUTUBE_CLIENT_ID or YOUTUBE_CLIENT_SECRET is not configured in .env'
      );
    }

    // Exchange auth code for access & refresh tokens
    const tokenRes = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        code,
        client_id: clientId,
        client_secret: clientSecret,
        redirect_uri: redirectUri,
        grant_type: 'authorization_code',
      }),
    });

    const tokens = await tokenRes.json();

    if (tokens.error) {
      return redirectTo('error', tokens.error_description || tokens.error);
    }

    const refreshToken = tokens.refresh_token;
    if (!refreshToken) {
      return redirectTo(
        'error',
        'No refresh token returned. Remove this app from your Google Account access and connect again.'
      );
    }

    // Store the per-channel refresh token (encrypted) via the worker.
    const result = await runWorkerCommand([
      'channel',
      'set-youtube-token',
      '--channel',
      channelRef,
      '--token',
      refreshToken,
    ]);

    if (!result.ok) {
      console.error('[OAuth] Failed to store YouTube token via worker:', result.stderr);
      return redirectTo('error', 'Failed to store the YouTube token for this channel.');
    }

    return redirectTo('success');
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Unexpected error';
    return redirectTo('error', message);
  }
}
