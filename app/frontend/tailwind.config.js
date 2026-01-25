/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#f0fdf4",
          100: "#dcfce7",
          200: "#bbf7d0",
          300: "#86efac",
          400: "#4ade80",
          500: "#22c55e",
          600: "#16a34a",
          700: "#15803d",
          800: "#166534",
          900: "#14532d"
        },
        "border-soft": "#e5e7eb",
        "surface": "#ffffff"
      },
      boxShadow: {
        soft: "0 12px 24px -16px rgba(15, 23, 42, 0.35)"
      },
      fontFamily: {
        sans: ["Manrope", "system-ui", "sans-serif"]
      }
    }
  },
  plugins: []
};
