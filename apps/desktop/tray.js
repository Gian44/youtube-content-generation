/**
 * StoryFactory Desktop — System Tray
 * 
 * Creates a system tray icon with context menu for quick access.
 */

const { Tray, Menu, nativeImage, Notification } = require('electron');
const path = require('path');

let tray = null;

/**
 * Create the system tray icon and context menu.
 */
function createTray({ iconPath, onShow, onRunNow, onQuit, getSchedulerStatus }) {
  // Create tray icon — use a fallback if ico not found
  let icon;
  try {
    icon = nativeImage.createFromPath(iconPath);
    if (icon.isEmpty()) {
      // Create a simple 16x16 colored icon as fallback
      icon = createFallbackIcon();
    }
  } catch {
    icon = createFallbackIcon();
  }

  tray = new Tray(icon);
  tray.setToolTip('StoryFactory — Content Generation Engine');

  // Double-click opens the dashboard
  tray.on('double-click', () => {
    onShow();
  });

  // Build context menu
  const buildMenu = () => {
    const status = getSchedulerStatus();
    const nextRunLabel = status.nextRun
      ? `Next run: ${status.nextRun}`
      : 'Scheduler: idle';
    const lastRunLabel = status.lastRun
      ? `Last run: ${status.lastRun}`
      : 'No pipeline runs yet';

    return Menu.buildFromTemplate([
      {
        label: '🏭 StoryFactory',
        enabled: false,
      },
      { type: 'separator' },
      {
        label: '📊 Open Dashboard',
        click: onShow,
      },
      { type: 'separator' },
      {
        label: '▶️  Run Pipeline Now',
        click: () => {
          onRunNow();
          showNotification('Pipeline Started', 'The full content pipeline is running...');
        },
      },
      { type: 'separator' },
      {
        label: `⏰ ${nextRunLabel}`,
        enabled: false,
      },
      {
        label: `📋 ${lastRunLabel}`,
        enabled: false,
      },
      {
        label: status.isRunning ? '🔄 Pipeline is running...' : '✅ Pipeline idle',
        enabled: false,
      },
      { type: 'separator' },
      {
        label: '❌ Quit StoryFactory',
        click: onQuit,
      },
    ]);
  };

  tray.setContextMenu(buildMenu());

  // Refresh the menu periodically to update status
  setInterval(() => {
    if (tray && !tray.isDestroyed()) {
      tray.setContextMenu(buildMenu());
    }
  }, 30000); // Every 30 seconds

  return tray;
}

function updateTrayMenu() {
  // Called externally to force a menu refresh
  if (tray && !tray.isDestroyed()) {
    tray.emit('rebuild-menu');
  }
}

function createFallbackIcon() {
  // Create a simple 16x16 icon with factory colors (dark purple/blue)
  const size = 16;
  const canvas = Buffer.alloc(size * size * 4);
  for (let i = 0; i < size * size; i++) {
    const x = i % size;
    const y = Math.floor(i / size);
    const offset = i * 4;
    // Simple gradient: purple to blue
    canvas[offset] = 100 + Math.floor((x / size) * 100);     // R
    canvas[offset + 1] = 50 + Math.floor((y / size) * 50);   // G
    canvas[offset + 2] = 200;                                  // B
    canvas[offset + 3] = 255;                                  // A
  }
  return nativeImage.createFromBuffer(canvas, { width: size, height: size });
}

function showNotification(title, body) {
  if (Notification.isSupported()) {
    new Notification({ title, body }).show();
  }
}

module.exports = { createTray, updateTrayMenu, showNotification };
