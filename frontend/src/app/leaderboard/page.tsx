import { getDb } from "@/lib/db"
import { LeaderboardClient } from "./LeaderboardClient"

export default function LeaderboardPage() {
  const db = getDb()
  const seasons = (db.prepare(
    "SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC"
  ).all() as { season: string }[]).map(r => r.season)

  const distances = (db.prepare(`
    SELECT DISTINCT distance_m FROM results r
    JOIN events e ON e.id=r.event_id
    WHERE e.track_type='short' AND r.distance_m IS NOT NULL
    ORDER BY distance_m
  `).all() as { distance_m: number }[]).map(r => r.distance_m)

  const birthYears = (db.prepare(`
    SELECT DISTINCT s.birth_year
    FROM skaters s
    JOIN results r ON r.skater_id = s.id
    JOIN events e ON e.id = r.event_id
    WHERE s.birth_year IS NOT NULL AND e.track_type = 'short'
    ORDER BY s.birth_year DESC
  `).all() as { birth_year: number }[]).map(r => r.birth_year)

  return (
    <div className="space-y-6">
      <div
        className="relative rounded-2xl overflow-hidden"
        style={{ backgroundImage: "url('/leaderboard.png')", backgroundSize: "cover", backgroundPosition: "center" }}
      >
        <div className="absolute inset-0 bg-black/25" />
        <div className="relative px-8 py-12" style={{ textShadow: "0 1px 6px rgba(0,0,0,0.7)" }}>
          <h1 className="text-4xl font-bold text-white mb-2">Leaderboard</h1>
          <p className="text-white/80 text-lg">Top times by season and distance</p>
        </div>
      </div>
      <LeaderboardClient seasons={seasons} distances={distances} birthYears={birthYears} />
    </div>
  )
}
