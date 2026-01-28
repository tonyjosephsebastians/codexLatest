import typography from "@tailwindcss/typography";

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
        soft: "0 8px 24px -16px rgba(15, 23, 42, 0.25)"
      },
      fontFamily: {
        sans: ["Manrope", "system-ui", "sans-serif"]
      },
      typography: ({ theme }) => ({
        DEFAULT: {
          css: {
            color: theme("colors.slate.700"),
            maxWidth: "none",
            a: {
              color: theme("colors.primary.700"),
              textDecoration: "none",
              fontWeight: "600",
              "&:hover": {
                color: theme("colors.primary.800")
              }
            },
            h1: {
              color: theme("colors.slate.900"),
              fontWeight: "700"
            },
            h2: {
              color: theme("colors.slate.900"),
              fontWeight: "600"
            },
            h3: {
              color: theme("colors.slate.900"),
              fontWeight: "600"
            },
            code: {
              color: theme("colors.slate.900"),
              backgroundColor: theme("colors.slate.100"),
              paddingLeft: "0.25rem",
              paddingRight: "0.25rem",
              paddingTop: "0.1rem",
              paddingBottom: "0.1rem",
              borderRadius: "0.25rem"
            },
            pre: {
              backgroundColor: theme("colors.slate.50"),
              borderColor: theme("colors.slate.200")
            }
          }
        }
      })
    }
  },
  plugins: [typography]
};
