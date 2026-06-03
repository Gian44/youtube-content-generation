# YouTube OAuth Setup Guide

## Overview

StoryFactory uses the YouTube Data API v3 to upload videos. This requires OAuth 2.0 authentication.

## Step-by-Step Setup

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Click **Select a Project** → **New Project**
3. Name it `StoryFactory` (or any name)
4. Click **Create**

### 2. Enable YouTube Data API v3

1. Go to **APIs & Services** → **Library**
2. Search for "YouTube Data API v3"
3. Click **Enable**

### 3. Configure OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **External** user type
3. Fill in:
   - App name: `StoryFactory`
   - User support email: your email
   - Developer contact: your email
4. Click **Save and Continue**
5. Add scopes:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube`
   - `https://www.googleapis.com/auth/youtube.readonly`
6. Add your email as a test user
7. Click **Save and Continue**

### 4. Create OAuth Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **OAuth client ID**
3. Application type: **Web application**
4. Name: `StoryFactory Dashboard`
5. Authorized redirect URIs:
   - `http://localhost:3000/api/auth/youtube/callback`
6. Click **Create**
7. Copy **Client ID** and **Client Secret**

### 5. Add to Environment

```env
YOUTUBE_CLIENT_ID=your-client-id-here
YOUTUBE_CLIENT_SECRET=your-client-secret-here
YOUTUBE_REDIRECT_URI=http://localhost:3000/api/auth/youtube/callback
```

### 6. Generate Refresh Token

Start the dashboard and navigate to **Settings** → **YouTube Connection** → **Connect YouTube Account**.

Or use the CLI:
```bash
cd apps/worker
python -c "
from storyfactory.services.youtube_uploader import _perform_upload
# Follow the OAuth flow that opens in your browser
"
```

Copy the refresh token and add it to `.env`:
```env
YOUTUBE_REFRESH_TOKEN=your-refresh-token
```

## Audit Limitation

> ⚠️ **Important**: New YouTube API projects start in "testing" mode.

In testing mode:
- Only **test users** (added in the consent screen) can authorize the app
- Uploads may default to **private** visibility
- You're limited to **100 users** in testing mode

### To upload public videos:

1. Submit your app for **verification** in the OAuth consent screen
2. Google will review your app (can take days to weeks)
3. Once verified, you can upload public videos

### StoryFactory's approach:

- Set `UPLOAD_PRIVACY_MODE=public` in `.env`
- Set `ALLOW_PRIVATE_FALLBACK=true`
- If public upload fails (unaudited project), StoryFactory automatically falls back to private
- The dashboard logs both requested and actual privacy status
- You can manually make videos public in YouTube Studio

## Quota

The YouTube Data API has a daily quota of **10,000 units**.

| Operation | Cost |
|-----------|------|
| `videos.insert` (upload) | 1,600 units |
| `thumbnails.set` | 50 units |
| `videos.list` (analytics) | 1 unit |
| `playlistItems.insert` | 50 units |

With the default quota, you can upload approximately **6 videos per day**.

StoryFactory tracks quota usage automatically and will stop uploading when the quota is low.

## Troubleshooting

### "Access blocked: StoryFactory has not completed the Google verification process"
- Add your Google account as a test user in the OAuth consent screen

### "The request cannot be completed because you have exceeded your quota"
- Wait until the next day (quotas reset at midnight Pacific Time)
- Or request a quota increase in the Google Cloud Console

### "Upload failed: forbidden"
- Your project may not be verified for public uploads
- StoryFactory will automatically try a private upload if `ALLOW_PRIVATE_FALLBACK=true`

### Refresh token expired
- Re-authorize through the dashboard Settings page
- Refresh tokens typically don't expire unless revoked
