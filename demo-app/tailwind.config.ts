import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        neo: {
          blue:  "#018BFF",
          green: "#00CC76",
          dark:  "#0f1117",
          panel: "#1a1d27",
          border: "#2a2d3a",
        },
      },
    },
  },
  plugins: [],
};
export default config;
