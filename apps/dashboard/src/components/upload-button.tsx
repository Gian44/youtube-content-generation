"use client";

import { useState } from "react";

export function UploadButton() {
  const [running, setRunning] = useState(false);

  const handleUpload = async () => {
    if (running) return;
    const confirmUpload = confirm("Do you want to run the upload pipeline now?");
    if (!confirmUpload) return;

    setRunning(true);
    try {
      const res = await fetch("/api/run-action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "worker:upload" }),
      });
      const data = await res.json();
      if (data.success) {
        alert("Upload pipeline started in background!");
      } else {
        alert(`Failed to start uploads: ${data.error}`);
      }
    } catch (err: any) {
      alert(`Error starting uploads: ${err.message}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <button
      onClick={handleUpload}
      disabled={running}
      className={`rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 ${
        running ? "opacity-50 cursor-wait animate-pulse" : ""
      }`}
    >
      {running ? "📤 Uploading..." : "📤 Upload Pending"}
    </button>
  );
}
