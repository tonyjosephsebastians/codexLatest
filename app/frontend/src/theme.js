import { alpha, createTheme } from "@mui/material/styles";

export const codexTheme = createTheme({
  palette: {
    mode: "light",
    primary: {
      main: "#54B949",
      dark: "#43933a",
      light: "#7dcb75",
      contrastText: "#ffffff",
    },
    background: {
      default: "#f6f8f7",
      paper: "#ffffff",
    },
    divider: "#e2e8f0",
    text: {
      primary: "#0f172a",
      secondary: "#475569",
    },
  },
  shape: {
    borderRadius: 10,
  },
  typography: {
    fontFamily: "Manrope, system-ui, sans-serif",
    h6: { fontWeight: 700, fontSize: "1rem" },
    subtitle2: { fontWeight: 600, fontSize: "0.78rem" },
    body2: { lineHeight: 1.55 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          margin: 0,
          overflow: "hidden",
          background:
            "radial-gradient(circle at 10% -30%, rgba(84,185,73,0.14), transparent 38%), #f6f8f7",
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          boxShadow: "0 8px 32px -22px rgba(15, 23, 42, 0.42)",
          border: "1px solid #e2e8f0",
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          fontWeight: 600,
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          border: "1px solid #e2e8f0",
          backgroundColor: "#ffffff",
          "&:hover": {
            backgroundColor: alpha("#54B949", 0.08),
            borderColor: alpha("#54B949", 0.45),
          },
        },
      },
    },
  },
});
