"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  window.localStorage.setItem("andes-theme", theme);
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    setTheme(current);
  }, []);

  function chooseTheme(nextTheme: Theme) {
    setTheme(nextTheme);
    applyTheme(nextTheme);
  }

  return (
    <div className="theme-toggle" role="group" aria-label="Color theme">
      <button
        aria-pressed={theme === "light"}
        className={theme === "light" ? "active" : ""}
        type="button"
        onClick={() => chooseTheme("light")}
      >
        Light
      </button>
      <button
        aria-pressed={theme === "dark"}
        className={theme === "dark" ? "active" : ""}
        type="button"
        onClick={() => chooseTheme("dark")}
      >
        Dark
      </button>
    </div>
  );
}
