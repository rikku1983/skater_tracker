import type { Metadata } from "next"
import { Geist } from "next/font/google"
import "./globals.css"
import { NavBar } from "./NavBar"

const geist = Geist({ variable: "--font-geist", subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Skater Tracker",
  description: "US short track speed skating results",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geist.variable} h-full antialiased`}>
      <body suppressHydrationWarning className="min-h-full flex flex-col bg-background">
        <header className="border-b sticky top-0 bg-background z-10">
          <NavBar />
        </header>
        <main className="max-w-7xl mx-auto px-4 py-6 w-full flex-1">{children}</main>
        <footer className="border-t mt-8 py-6 text-center text-xs text-muted-foreground space-y-1">
          <p>
            Data sourced from{" "}
            <a href="https://www.usspeedskating.org/" target="_blank" rel="noopener noreferrer" className="underline hover:text-foreground transition-colors">U.S. Speed Skating</a>
            {" "}and{" "}
            <a href="https://www.shorttracklive.info/" target="_blank" rel="noopener noreferrer" className="underline hover:text-foreground transition-colors">shorttracklive.info</a>.
          </p>
          <p>This is a personal, non-commercial tool built for skaters and parents. Not affiliated with or endorsed by U.S. Speed Skating.</p>
        </footer>
      </body>
    </html>
  )
}
