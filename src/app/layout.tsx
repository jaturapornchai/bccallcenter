import type { Metadata } from "next";
import { JetBrains_Mono, Noto_Sans_Thai, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

const notoSansThai = Noto_Sans_Thai({
  subsets: ["thai", "latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-thai",
});

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-jakarta",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "ระบบ BC Call Center | ศูนย์บัญชาการรับสายอัตโนมัติ 5 คู่สาย",
  description: "ระบบ BC Call Center ศูนย์บัญชาการรับสายอัตโนมัติ 5 คู่สาย เชื่อมต่อ Javis AI พร้อมระบบจดจำประวัติการคุยต่อเนื่องและวิเคราะห์คุณภาพหลังวางสาย",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="th" className={`${notoSansThai.variable} ${jakarta.variable} ${jetbrainsMono.variable}`}>
      <body className="min-h-screen antialiased selection:bg-emerald-500/30 selection:text-emerald-200">
        {children}
      </body>
    </html>
  );
}
