import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { NuqsAdapter } from "nuqs/adapters/next";
import "./globals.css";
import { ThemeProvider } from "@/components/theme-provider";
import { AppNav } from "@/components/app-nav";
import { FreshnessBar } from "@/components/freshness-bar";
import { Toaster } from "@/components/ui/sonner";

const geistSans = Geist({ variable: "--font-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: {
    default: "Insider Signal",
    template: "%s · Insider Signal",
  },
  description:
    "Research-backed buy signals from SEC Form 4 insider purchase disclosures. Ingested daily, scored, backtested, and shown in full.",
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://insider-signal.vercel.app"),
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <NuqsAdapter>
            <AppNav />
            <FreshnessBar />
            <main className="flex-1">{children}</main>
            <footer className="border-t py-4 text-center text-xs text-muted-foreground">
              <div className="mx-auto flex max-w-[1600px] flex-col items-center gap-1 px-4 sm:flex-row sm:justify-between">
                <span>
                  Data: SEC EDGAR Form 4. Not investment advice. Signals are informational only.
                </span>
                <Link
                  href="https://github.com/syoopie/insider-signal"
                  className="underline-offset-2 hover:underline"
                >
                  Source on GitHub
                </Link>
              </div>
            </footer>
            <Toaster />
          </NuqsAdapter>
        </ThemeProvider>
      </body>
    </html>
  );
}
