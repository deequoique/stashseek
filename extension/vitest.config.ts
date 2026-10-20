import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    environmentOptions: { jsdom: { url: "https://app.example.test" } },
    setupFiles: "./src/test/setup.ts",
    coverage: {
      provider: "v8",
      include: ["src/page-capture.ts"],
      reporter: ["text"],
      thresholds: {
        statements: 80,
        branches: 72,
        functions: 85,
        lines: 90,
      },
    },
  },
});
