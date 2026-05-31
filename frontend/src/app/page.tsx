import { getDb } from "@/lib/db"
import { Card, CardContent } from "@/components/ui/card"
import Link from "next/link"
import { Calendar, Users, Activity, Timer, ArrowRight } from "lucide-react"

export default function HomePage() {
  const db = getDb()

  const total_events = (db.prepare("SELECT COUNT(*) as n FROM events WHERE track_type='short'").get() as { n: number }).n
  const total_skaters = (db.prepare("SELECT COUNT(*) as n FROM skaters").get() as { n: number }).n
  const total_results = (db.prepare(
    "SELECT COUNT(*) as n FROM results r JOIN events e ON e.id=r.event_id WHERE e.track_type='short'"
  ).get() as { n: number }).n
  const seasons = (db.prepare(
    "SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC"
  ).all() as { season: string }[]).map(r => r.season)

  const recentEvents = db.prepare(`
    SELECT e.id, e.event_name, e.season, e.event_date as start_date, e.city, e.state,
           COUNT(r.id) as result_count
    FROM events e
    LEFT JOIN results r ON r.event_id = e.id
    WHERE e.track_type = 'short'
    GROUP BY e.id
    ORDER BY CAST(substr(e.event_date,-4) AS INTEGER) DESC,
             CAST(substr(e.event_date,1,instr(e.event_date,'/')-1) AS INTEGER) DESC,
             CAST(substr(e.event_date,instr(e.event_date,'/')+1) AS INTEGER) DESC,
             e.id DESC
    LIMIT 10
  `).all() as { id: number; event_name: string; season: string; start_date: string | null; city: string | null; state: string | null; result_count: number }[]

  const stats = [
    { label: "Events",  value: total_events.toLocaleString(),  icon: Calendar },
    { label: "Skaters", value: total_skaters.toLocaleString(), icon: Users },
    { label: "Results", value: total_results.toLocaleString(), icon: Activity },
    { label: "Seasons", value: seasons.length,                 icon: Timer },
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
        {stats.map(({ label, value, icon: Icon }) => (
          <Card key={label} className="hover:shadow-md transition-shadow">
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
        ))}
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
                <span>{e.result_count.toLocaleString()} results</span>
                <ArrowRight className="h-3.5 w-3.5 opacity-0 group-hover:opacity-100 transition-opacity text-primary" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
