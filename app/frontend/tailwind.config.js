import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#f4fbf2",
          100: "#e8f6e2",
          200: "#d2edc9",
          300: "#b4dfaa",
          400: "#8fce80",
          500: "#54B949",
          600: "#469f3c",
          700: "#397f31",
          800: "#31672c",
          900: "#2a5627"
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
