"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { formatTime } from "@/lib/utils"
import type { LeaderboardRow } from "@/lib/types"

export function LeaderboardClient({ seasons, distances, birthYears }: { seasons: string[]; distances: number[]; birthYears: number[] }) {
  const [season, setSeason] = useState("")
  const [distance, setDistance] = useState(500)
  const [gender, setGender] = useState("")
  const [birthYear, setBirthYear] = useState("")
  const [rows, setRows] = useState<LeaderboardRow[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!distance) return
    setLoading(true)
    const params = new URLSearchParams({ distance_m: String(distance) })
    if (season) params.set("season", season)
    if (gender) params.set("gender", gender)
    if (birthYear) params.set("birth_year", birthYear)
    fetch(`/api/leaderboard?${params}`)
      .then(r => r.json())
      .then(d => { setRows(Array.isArray(d) ? d : []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [season, distance, gender, birthYear])

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:flex gap-2 sm:gap-3 sm:flex-wrap">
        <select value={season} onChange={e => setSeason(e.target.value)} className="border rounded-md px-3 py-2 text-sm bg-background">
          <option value="">All seasons</option>
          {seasons.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={distance} onChange={e => setDistance(Number(e.target.value))} className="border rounded-md px-3 py-2 text-sm bg-background">
          {distances.map(d => <option key={d} value={d}>{d}m</option>)}
        </select>
        <select value={gender} onChange={e => setGender(e.target.value)} className="border rounded-md px-3 py-2 text-sm bg-background">
          <option value="">All genders</option>
          <option value="Male">Male</option>
          <option value="Female">Female</option>
        </select>
        <select value={birthYear} onChange={e => setBirthYear(e.target.value)} className="border rounded-md px-3 py-2 text-sm bg-background">
          <option value="">All birth years</option>
          {birthYears.map(y => <option key={y} value={y}>Born {y}</option>)}
        </select>
      </div>

      {loading && <div className="text-sm text-muted-foreground">Loading…</div>}

      <div className="border rounded-lg overflow-x-auto">
        <table className="w-full text-sm min-w-[360px]">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-center px-4 py-2 font-medium w-14">Rank</th>
              <th className="text-left px-4 py-2 font-medium">Skater</th>
              <th className="text-left px-4 py-2 font-medium hidden sm:table-cell">Club</th>
              <th className="text-right px-4 py-2 font-medium">Best Time</th>
              <th className="text-right px-4 py-2 font-medium hidden sm:table-cell">Races</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map(r => (
              <tr key={r.skater_id} className={
                r.rank === 1 ? "bg-amber-50/60 hover:bg-amber-50 transition-colors" :
                r.rank === 2 ? "bg-slate-50/60 hover:bg-slate-100/60 transition-colors" :
                r.rank === 3 ? "bg-orange-50/60 hover:bg-orange-50 transition-colors" :
                "hover:bg-muted/30 transition-colors"
              }>
                <td className="px-4 py-2 text-center">
                  {r.rank === 1
                    ? <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-amber-100 text-amber-700 font-bold text-xs">1</span>
                    : r.rank === 2
                    ? <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-slate-200 text-slate-600 font-bold text-xs">2</span>
                    : r.rank === 3
                    ? <span className="inline-flex items-center justify-center w-7 h-7 rounded-full bg-orange-100 text-orange-700 font-bold text-xs">3</span>
                    : <span className="text-muted-foreground font-mono">{r.rank}</span>}
                </td>
                <td className="px-4 py-2">
                  <Link href={`/skaters/${r.skater_id}`} className="hover:underline font-medium">{r.skater_name}</Link>
                  <div className="text-xs text-muted-foreground sm:hidden">{r.club_name ?? ""}</div>
                </td>
                <td className="px-4 py-2 text-muted-foreground hidden sm:table-cell">{r.club_name ?? "—"}</td>
                <td className="px-4 py-2 text-right font-mono font-semibold">{formatTime(r.best_time)}</td>
                <td className="px-4 py-2 text-right text-muted-foreground hidden sm:table-cell">{r.num_races}</td>
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">No results for this filter</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
