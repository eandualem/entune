const statusEl = document.getElementById("status")!;

const res = await fetch("/api/health");
statusEl.textContent = res.ok ? "Server is up." : `Server error: ${res.status}`;

export {};
