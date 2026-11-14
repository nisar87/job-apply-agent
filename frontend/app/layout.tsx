import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Job Apply Agent",
  description: "Finds, scores and applies to jobs from your own Gmail account.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
