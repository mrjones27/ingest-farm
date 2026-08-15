/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#07090c",
          900: "#0b0d10",
          800: "#12161c",
          700: "#1a2028",
          600: "#252d38",
        },
        signal: {
          live: "#22c55e",
          warn: "#f59e0b",
          err: "#ef4444",
          idle: "#6b7280",
          info: "#38bdf8",
        },
      },
      fontFamily: {
        sans: ["IBM Plex Sans", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
