import type { Config } from "tailwindcss";
import daisyui from "daisyui";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      boxShadow: {
        panel: "0 24px 80px rgba(24, 34, 46, 0.12)",
      },
      backgroundImage: {
        "governance-grid":
          "radial-gradient(circle at 1px 1px, rgba(31,41,55,0.08) 1px, transparent 0)",
      },
    },
  },
  daisyui: {
    themes: [
      {
        governance: {
          primary: "#9d5c0d",
          "primary-content": "#fffaf2",
          secondary: "#205781",
          "secondary-content": "#eff8ff",
          accent: "#5b7c39",
          "accent-content": "#f7fff0",
          neutral: "#22313f",
          "neutral-content": "#f3f6f8",
          "base-100": "#f4efe6",
          "base-200": "#ebe3d3",
          "base-300": "#d8ccb8",
          "base-content": "#1c2732",
          info: "#2f6690",
          success: "#4f772d",
          warning: "#c77d16",
          error: "#b23a48",
        },
      },
    ],
  },
  plugins: [daisyui],
} satisfies Config;
