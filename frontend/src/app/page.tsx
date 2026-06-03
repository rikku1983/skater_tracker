import { sql } from "@/lib/db"
import { Card, CardContent } from "@/components/ui/card"
import Link from "next/link"
import { Calendar, Users, Activity, Timer, ArrowRight } from "lucide-react"

export const dynamic = "force-dynamic"

export default async function HomePage() {
  const [eventsR, skatersR, resultsR, seasonsR, recentEvents] = await Promise.all([
    sql`SELECT COUNT(*) as n FROM events WHERE track_type='short'`,
    sql`SELECT COUNT(*) as n FROM skaters`,
    sql`SELECT COUNT(*) as n FROM results r JOIN events e ON e.id=r.event_id WHERE e.track_type='short'`,
    sql<{ season: string }[]>`SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC`,
    sql<{ id: number; event_name: string; season: string; start_date: string | null; city: string | null; state: string | null; result_count: number }[]>`
      SELECT e.id, e.event_name, e.season, e.event_date as start_date, e.city, e.state,
             COUNT(r.id) as result_count
      FROM events e
      LEFT JOIN results r ON r.event_id = e.id
      WHERE e.track_type = 'short'
      GROUP BY e.id
      ORDER BY
        SPLIT_PART(e.event_date,'/',3)::INTEGER DESC,
        SPLIT_PART(e.event_date,'/',1)::INTEGER DESC,
        SPLIT_PART(e.event_date,'/',2)::INTEGER DESC,
        e.id DESC
      LIMIT 10
    `,
  ])

  const seasons = seasonsR.map(r => r.season)
  const stats = [
    { label: "Events",  value: Number(eventsR[0].n).toLocaleString(),  icon: Calendar, href: "/events" },
    { label: "Skaters", value: Number(skatersR[0].n).toLocaleString(), icon: Users,    href: "/skaters" },
    { label: "Results", value: Number(resultsR[0].n).toLocaleString(), icon: Activity, href: "/leaderboard" },
    { label: "Seasons", value: seasons.length,                         icon: Timer,    href: null },
  ]

  return (
    <div className="space-y-8">
      <div
        className="relative rounded-2xl overflow-hidden"
        style={{ backgroundImage: "url('/bkgnd.png')", backgroundSize: "cover", backgroundPosition: "center" }}
      >
        <div className="absolute inset-0 bg-black/20" />
        <div className="relative px-8 py-12" style={{ textShadow: "0 1px 6px rgba(0,0,0,0.6)" }}>
          <h1 className="text-4xl font-bold text-white mb-2">Skater Tracker</h1>
          <p className="text-white/90 text-lg">US short track speed skating results, {seasons[seasons.length - 1]} – {seasons[0]}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {stats.map(({ label, value, icon: Icon, href }) => {
          const card = (
            <Card key={label} className={`transition-shadow ${href ? "hover:shadow-md cursor-pointer hover:border-primary/50" : ""}`}>
              <CardContent className="pt-5 pb-4">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-sm text-muted-foreground mb-1">{label}</p>
                    <p className="text-3xl font-bold text-primary">{value}</p>
                  </div>
                  <div className="bg-primary/10 text-primary rounded-lg p-2 mt-0.5">
                    <Icon className="h-5 w-5" />
                  </div>
                </div>
              </CardContent>
            </Card>
          )
          return href ? <Link key={label} href={href}>{card}</Link> : card
        })}
      </div>

      <div>
        <h2 className="text-xl font-semibold mb-3">Recent Events</h2>
        <div className="border rounded-lg divide-y overflow-hidden">
          {recentEvents.map(e => (
            <div key={e.id} className="group flex items-center justify-between px-4 py-3 hover:bg-muted/40 transition-colors border-l-2 border-transparent hover:border-primary">
              <div className="min-w-0">
                <Link href={`/events/${e.id}`} className="font-medium hover:underline">{e.event_name}</Link>
                <div className="text-sm text-muted-foreground">
                  {e.season}{e.start_date ? ` · ${e.start_date}` : ""}{e.city ? ` · ${e.city}${e.state ? `, ${e.state}` : ""}` : ""}
                </div>
              </div>
              <div className="flex items-center gap-2 text-sm text-muted-foreground shrink-0 ml-4">
                <span>{Number(e.result_count).toLocaleString()} results</span>
                <ArrowRight className="h-3.5 w-3.5 opacity-0 group-hover:opacity-100 transition-opacity text-primary" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
