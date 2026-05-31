"use client"

import Link from "next/link"
import Image from "next/image"
import { usePathname } from "next/navigation"
import { Users, Calendar, Building2, Trophy, GitCompare } from "lucide-react"
import { cn } from "@/lib/utils"

const links = [
  { href: "/skaters",     label: "Skaters",     icon: Users },
  { href: "/events",      label: "Events",       icon: Calendar },
  { href: "/clubs",       label: "Clubs",        icon: Building2 },
  { href: "/leaderboard", label: "Leaderboard",  icon: Trophy },
  { href: "/compare",     label: "Compare",      icon: GitCompare },
]

export function NavBar() {
  const pathname = usePathname()

  return (
    <nav className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-1">
      <Link href="/" className="flex items-center gap-2 font-semibold text-base mr-4">
        <Image src="/logo.png" alt="Skater Tracker" width={52} height={52} className="rounded" />
        Skater Tracker
      </Link>
      {links.map(({ href, label, icon: Icon }) => {
        const active = pathname === href || pathname.startsWith(href + "/")
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "flex items-center gap-1.5 px-3 h-14 text-sm border-b-2 transition-colors",
              active
                ? "border-primary text-primary font-medium"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/30"
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </Link>
        )
      })}
    </nav>
  )
}
