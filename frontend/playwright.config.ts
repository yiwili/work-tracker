import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8766",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "..\\.venv\\Scripts\\python.exe ..\\tests\\e2e_server.py",
    url: "http://127.0.0.1:8766/api/health",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
