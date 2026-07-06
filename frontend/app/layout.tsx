import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "岗标AI教练",
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
