import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "StockSage AI",
  description:
    "Multi-agent AI stock research for Pakistani (PSX) and Global (US) markets.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
