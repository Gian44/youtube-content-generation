/**
 * StoryFactory Desktop — Preload Script
 * 
 * Exposes a secure API from the main process to the renderer (dashboard).
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('storyfactory', {
  // Scheduler
  getSchedulerStatus: () => ipcRenderer.invoke('get-scheduler-status'),
  runPipelineNow: () => ipcRenderer.invoke('run-pipeline-now'),
  runAnalyticsNow: () => ipcRenderer.invoke('run-analytics-now'),

  // App info
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  getIsDev: () => ipcRenderer.invoke('get-is-dev'),

  // Startup screen
  onStartupProgress: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('startup-progress', listener);
    return () => ipcRenderer.removeListener('startup-progress', listener);
  },
  startupRetry: () => ipcRenderer.invoke('startup-retry'),
  startupQuit: () => ipcRenderer.invoke('startup-quit'),

  // Listen for events from main process
  onSchedulerUpdate: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('scheduler-update', listener);
    return () => ipcRenderer.removeListener('scheduler-update', listener);
  },
  onPipelineLog: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('pipeline-log', listener);
    return () => ipcRenderer.removeListener('pipeline-log', listener);
  },
});
