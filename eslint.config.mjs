import eslint from "@eslint/js";
import prettier from "eslint-config-prettier";
import globals from "globals";

export default [
  {
    ignores: ["node_modules/", "playwright-report/", "test-results/"],
  },
  eslint.configs.recommended,
  {
    files: ["src/gpo_studio/static/js/**/*.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: globals.browser,
    },
    rules: {
      // Existing modules predate the lint gate. Keep this legacy cleanup visible
      // without blocking WP-1; undefined names remain release-blocking errors.
      "no-empty": "warn",
      "no-unused-vars": "warn",
    },
  },
  {
    files: ["tests/**/*.mjs", "*.config.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: globals.node,
    },
  },
  prettier,
];
