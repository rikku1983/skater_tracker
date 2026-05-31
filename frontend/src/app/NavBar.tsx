"use client"

import Link from "next/link"
import Image from "next/image"
import { usePathname } from "next/navigation"
import { Users, Calendar, Building2, Trophy, GitCompare, Menu, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { useState } from "react"

const links = [
  { href: "/skaters",     label: "Skaters",     icon: Users },
  { href: "/events",      label: "Events",       icon: Calendar },
  { href: "/clubs",       label: "Clubs",        icon: Building2 },
  { href: "/leaderboard", label: "Leaderboard",  icon: Trophy },
  { href: "/compare",     label: "Compare",      icon: GitCompare },
]

export function NavBar() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)

  return (
    <>
      <nav className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-1">
        <Link href="/" onClick={() => setOpen(false)}
          className="flex items-center gap-2 font-semibold text-base mr-4">
          <Image src="/logo.png" alt="Skater Tracker" width={52} height={52} className="rounded" />
          <span className="hidden sm:inline">Skater Tracker</span>
        </Link>

        {/* Desktop nav links */}
        <div className="hidden md:flex items-center">
          {links.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + "/")
            return (
              <Link key={href} href={href} className={cn(
                "flex items-center gap-1.5 px-3 h-14 text-sm border-b-2 transition-colors",
                active
                  ? "border-primary text-primary font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/30"
              )}>
                <Icon className="h-3.5 w-3.5" />
                {label}
              </Link>
            )
          })}
        </div>

        {/* Mobile hamburger button */}
        <button
          className="md:hidden ml-auto p-2 text-muted-foreground hover:text-foreground"
          onClick={() => setOpen(o => !o)}
          aria-label="Toggle menu"
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </nav>

      {/* Mobile dropdown menu */}
      {open && (
        <div className="md:hidden border-t bg-background px-4 py-2">
          {links.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + "/")
            return (
              <Link key={href} href={href} onClick={() => setOpen(false)}
                className={cn(
                  "flex items-center gap-3 py-3 text-sm border-b border-muted/40 last:border-0",
                  active ? "text-primary font-medium" : "text-muted-foreground"
                )}>
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            )
          })}
        </div>
      )}
    </>
  )
}
