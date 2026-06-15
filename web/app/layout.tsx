import type { Metadata } from "next";
import Link from "next/link";
import { ThemeToggle } from "@/components/theme-toggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "ANDES v2",
  description: "ANDES set similarity and GSEA prototype",
  referrer: "no-referrer"
};

const themeScript = `
(function () {
  try {
    var stored = window.localStorage.getItem("andes-theme");
    var theme = stored === "dark" || stored === "light" ? stored : "dark";
    document.documentElement.dataset.theme = theme;
  } catch (_) {
    document.documentElement.dataset.theme = "dark";
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <meta name="referrer" content="no-referrer" />
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body suppressHydrationWarning>
        <main className="shell">
          <header className="topbar">
            <div className="brand">
              <span className="brand-mark">ANDES</span>
              <div>
                <h1>ANDES v2</h1>
                <p>Set similarity and embedding-based enrichment analysis</p>
              </div>
            </div>
            <div className="topbar-actions">
              <nav className="nav">
                <Link href="/">Overview</Link>
                <Link href="/about">About</Link>
                <Link href="/set-similarity">Set Similarity</Link>
                <Link href="/gsea">GSEA</Link>
                <Link href="/jobs">My Jobs</Link>
                <Link href="/admin">Admin</Link>
              </nav>
              <ThemeToggle />
            </div>
          </header>
          {children}
        </main>
      </body>
    </html>
  );
}
