import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/frontend/**/*.test.mjs"],
    environment: "node",
    restoreMocks: true,
  },
});
