"use client";

import { useState } from "react";

export function RunBatchButton() {
  const [running, setRunning] = useState(false);

  const handleRun = async () => {
    if (running) return;
    const confirmRun = confirm("Are you sure you want to trigger a new daily batch?");
    if (!confirmRun) return;

    setRunning(true);
    try {
      const res = await fetch("/api/run-action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "worker:daily" }),
      });
      const data = await res.json();
      if (data.success) {
        alert("Daily pipeline successfully triggered in the background!");
      } else {
        alert(`Failed to trigger daily pipeline: ${data.error}`);
      }
    } catch (err: any) {
      alert(`Error triggering pipeline: ${err.message}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <button
      onClick={handleRun}
      disabled={running}
      className={`rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-all hover:opacity-90 hover:shadow-lg hover:shadow-primary/20 ${
        running ? "opacity-50 cursor-wait animate-pulse" : ""
      }`}
    >
      {running ? "🚀 Running..." : "🚀 Run New Batch"}
    </button>
  );
}
