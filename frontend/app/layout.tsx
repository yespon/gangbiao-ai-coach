import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gangbiao Chat",
  description: "Next.js frontend for Gangbiao Chatbot API",
  icons: {
    icon: "/favicon.svg",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
