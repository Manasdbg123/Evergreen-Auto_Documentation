import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";

const KEY = "evergreen-theme";

/**
 * Light / Dark / System.
 *
 * Three states rather than two, because "system" is a real choice and the
 * default one: it stamps no attribute and lets `prefers-color-scheme` decide,
 * so the app follows the OS when the OS changes. An explicit choice stamps
 * `data-theme` on <html>, which the stylesheet honours over the media query in
 * both directions.
 *
 * The initial value is also applied by an inline script in index.html, before
 * first paint. Doing it only here would render the wrong theme for one frame.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const stored = localStorage.getItem(KEY);
      return stored === "dark" || stored === "light" ? stored : "system";
    } catch {
      return "system"; // private mode, blocked storage — the default still works
    }
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      /* the theme still applies for this session */
    }
  }, [theme]);

  return (
    <div className="theme-toggle" role="group" aria-label="Colour theme">
      {(["light", "dark", "system"] as Theme[]).map((option) => (
        <button
          key={option}
          className={theme === option ? "on" : ""}
          aria-pressed={theme === option}
          onClick={() => setTheme(option)}
        >
          {option === "light" ? "Light" : option === "dark" ? "Dark" : "Auto"}
        </button>
      ))}
    </div>
  );
}
