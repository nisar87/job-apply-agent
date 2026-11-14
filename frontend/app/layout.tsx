import type { Metadata } from "next";
import "./globals.css";

// A private deployment (staging) is built with NEXT_PUBLIC_HIDE_FROM_SEARCH=1: no search engine may index it.
const hidden = process.env.NEXT_PUBLIC_HIDE_FROM_SEARCH === "1";

export const metadata: Metadata = {
  title: "Job Apply Agent",
  description: "Finds, scores and applies to jobs from your own Gmail account.",
  ...(hidden && { robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } } }),
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
