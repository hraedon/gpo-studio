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
      "no-empty": "error",
      "no-unused-vars": "error",
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
