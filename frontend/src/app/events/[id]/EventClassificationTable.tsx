"use client"

import React, { useState, useMemo } from "react"
import Link from "next/link"
import { formatTime } from "@/lib/utils"

interface Row {
  id: number
  skater_id: number | null
  skater_name: string
  club_name: string | null
  division: string | null
  section_type: string | null
  rank: number | null
  points: number | null
  distance_m: number | null
  time_seconds: number | null
  bib: string | null
}

const SECTION_LABELS: Record<string, string> = {
  overall: "Overall",
  overall_dist: "Overall by Distance",
  distance: "Time Classification",
}

function groupByDivisionAndType(data: Row[]) {
  const groups: { division: string | null; section_type: string | null; rows: Row[] }[] = []
  let cur: (typeof groups)[0] | null = null
  for (const r of data) {
    if (!cur || cur.division !== r.division || cur.section_type !== r.section_type) {
      cur = { division: r.division, section_type: r.section_type, rows: [] }
      groups.push(cur)
    }
    cur.rows.push(r)
  }
  return groups
}

export function EventClassificationTable({ rows }: { rows: Record<string, unknown>[] }) {
  const data = rows as unknown as Row[]
  const [filterType, setFilterType] = useState("overall")
  const [filterDiv, setFilterDiv] = useState("")

  const sections = useMemo(() => [...new Set(data.map(r => r.section_type).filter(Boolean))].sort() as string[], [data])
  const divisions = useMemo(() => [...new Set(data.map(r => r.division).filter(Boolean))].sort() as string[], [data])

  const filtered = useMemo(() => data.filter(r =>
    (!filterType || r.section_type === filterType) &&
    (!filterDiv || r.division === filterDiv)
  ), [data, filterType, filterDiv])

  const groups = useMemo(() => groupByDivisionAndType(filtered), [filtered])

  return (
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap items-center">
        <select value={filterType} onChange={e => setFilterType(e.target.value)} className="border rounded-md px-3 py-1.5 text-sm bg-background">
          <option value="">All types</option>
          {sections.map(s => <option key={s} value={s}>{SECTION_LABELS[s] ?? s}</option>)}
        </select>
        <select value={filterDiv} onChange={e => setFilterDiv(e.target.value)} className="border rounded-md px-3 py-1.5 text-sm bg-background">
          <option value="">All divisions</option>
          {divisions.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <span className="text-sm text-muted-foreground">{filtered.length.toLocaleString()} rows</span>
      </div>

      <div className="border rounded-lg overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-right px-3 py-2 font-medium w-12">Rank</th>
              <th className="text-left px-3 py-2 font-medium">Name</th>
              <th className="text-left px-3 py-2 font-medium">Club</th>
              <th className="text-right px-3 py-2 font-medium">Dist</th>
              <th className="text-right px-3 py-2 font-medium">Time</th>
              <th className="text-right px-3 py-2 font-medium">Points</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g, gi) => (
              <React.Fragment key={`grp-${gi}`}>
                <tr className="bg-muted/70 border-t">
                  <td colSpan={6} className="px-3 py-1.5 text-xs font-semibold text-muted-foreground tracking-wide uppercase">
                    {g.division ?? "Unknown"} · {SECTION_LABELS[g.section_type ?? ""] ?? g.section_type ?? ""}
                    {g.section_type === "overall_dist" && g.rows[0]?.distance_m ? ` · ${g.rows[0].distance_m}m` : ""}
                  </td>
                </tr>
                {g.rows.map(r => (
                  <tr key={r.id} className="hover:bg-muted/30 transition-colors border-t border-muted/30">
                    <td className="px-3 py-1.5 text-right text-muted-foreground">{r.rank ?? "—"}</td>
                    <td className="px-3 py-1.5">
                      {r.skater_id
                        ? <Link href={`/skaters/${r.skater_id}`} className="hover:underline font-medium">{r.skater_name}</Link>
                        : r.skater_name}
                    </td>
                    <td className="px-3 py-1.5 text-muted-foreground">{r.club_name ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right">{r.distance_m ? `${r.distance_m}m` : "—"}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{r.time_seconds != null ? formatTime(r.time_seconds) : "—"}</td>
                    <td className="px-3 py-1.5 text-right">{r.points ?? "—"}</td>
                  </tr>
                ))}
              </React.Fragment>
            ))}
            {filtered.length === 0 && <tr><td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">No classification data</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
