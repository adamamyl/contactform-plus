import js from "@eslint/js";
import globals from "globals";

export default [
  js.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: "script",
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      "no-unused-vars": [
        "error",
        { args: "none", caughtErrorsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    ignores: ["apps/form/static/leaflet.js"],
  },
];
