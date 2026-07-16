import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const workspaceDirectory = mkdtempSync(join(tmpdir(), "gpo-studio-browser-"));
const database = join(workspaceDirectory, "workspace.db");
const python = process.env.GPO_STUDIO_TEST_PYTHON;
const command = python || (process.env.CI ? "python" : "uv");
const args =
  python || process.env.CI
    ? ["-m", "gpo_studio", "run", "--port", "4173", "--database", database]
    : ["run", "gpo-studio", "run", "--port", "4173", "--database", database];

const server = spawn(command, args, {
  stdio: "inherit",
  env: { ...process.env, PYTHONUNBUFFERED: "1" },
});

let cleaned = false;
function cleanup() {
  if (!cleaned) {
    cleaned = true;
    rmSync(workspaceDirectory, { recursive: true, force: true });
  }
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.kill(signal));
}

server.once("error", (error) => {
  cleanup();
  throw error;
});

server.once("exit", (code, signal) => {
  cleanup();
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 1);
});
