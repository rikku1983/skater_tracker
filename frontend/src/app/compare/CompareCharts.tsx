"use client"

import { useState, useMemo } from "react"
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts"
import { formatTime, compareDates } from "@/lib/utils"
import type { CompareData } from "@/lib/types"

const COLORS = [
  "#2563eb", // blue
  "#dc2626", // red
  "#059669", // emerald
  "#d97706", // amber
  "#7c3aed", // violet
  "#db2777", // pink
  "#0891b2", // cyan
  "#65a30d", // lime
  "#ea580c", // orange
  "#6366f1", // indigo
  "#0d9488", // teal
  "#c026d3", // fuchsia
]

interface CompareChartsProps {
  data: CompareData[]
  distances: number[]
  mode: "season" | "event"
  nameMap?: Record<number, string>
}

type ChartPoint = Record<string, unknown> & { _events: Record<string, string> }

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function eventTooltipLabel(_xLabel: unknown, payload: any) {
  if (!payload?.length) return String(_xLabel)
  const events = payload[0]?.payload?._events as Record<string, string> | undefined
  if (!events) return String(_xLabel)
  const unique = [...new Set(Object.values(events))]
  return unique.length ? unique.join(" / ") : String(_xLabel)
}

export function CompareCharts({ data, distances, mode, nameMap }: CompareChartsProps) {
  function label(d: CompareData) { return nameMap?.[d.skater_id] ?? d.skater_name }

  const allSeasons = useMemo(() => {
    const set = new Set<string>()
    data.forEach(d => d.season_bests.forEach(b => set.add(b.season)))
    return [...set].sort()
  }, [data])

  const allYears = useMemo(() => {
    const set = new Set<number>()
    data.forEach(d => d.event_bests.forEach(b => {
      if (b.start_date) {
        const y = Number(b.start_date.split("/")[2])
        if (y) set.add(y)
      }
    }))
    return [...set].sort()
  }, [data])

  const [fromSeason, setFromSeason] = useState("")
  const [toSeason, setToSeason] = useState("")
  const [fromYear, setFromYear] = useState(0)
  const [toYear, setToYear] = useState(0)

  const effectiveFromSeason = fromSeason || allSeasons[0] || ""
  const effectiveToSeason = toSeason || allSeasons[allSeasons.length - 1] || ""
  const effectiveFromYear = fromYear || allYears[0] || 0
  const effectiveToYear = toYear || allYears[allYears.length - 1] || 0

  function inRangeSeason(season: string) {
    return season >= effectiveFromSeason && season <= effectiveToSeason
  }

  function inRangeYear(startDate: string | null) {
    if (!startDate) return true
    const y = Number(startDate.split("/")[2])
    return y >= effectiveFromYear && y <= effectiveToYear
  }

  function renderChart(dist: number) {
    if (mode === "season") {
      const seasons = [...new Set(data.flatMap(d => d.season_bests.filter(b => b.distance_m === dist).map(b => b.season)))].sort().filter(inRangeSeason)
      const chartData: ChartPoint[] = seasons.map(s => {
        const pt: ChartPoint = { season: s, _events: {} }
        data.forEach(d => {
          const b = d.season_bests.find(x => x.distance_m === dist && x.season === s)
          if (b) { pt[label(d)] = b.best_time; pt._events[label(d)] = b.event_name }
        })
        return pt
      })
      if (chartData.length < 2) return null
      const skaters = data.filter(d => d.season_bests.some(b => b.distance_m === dist))
      return (
        <>
          <h3 className="font-semibold mb-2">{dist}m</h3>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={chartData}>
              <defs>
                {skaters.map((d, i) => (
                  <linearGradient key={d.skater_id} id={`grad-s-${dist}-${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.15} />
                    <stop offset="95%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis dataKey="season" tick={{ fontSize: 11 }} />
              <YAxis tickFormatter={formatTime} tick={{ fontSize: 11 }} width={64} domain={["auto", "auto"]} />
              <Tooltip
                formatter={(v, name, item) => {
                  const eventName = (item.payload as ChartPoint)._events[String(name)]
                  return [typeof v === "number" ? formatTime(v) : String(v), eventName || String(name)]
                }}
              />
              <Legend />
              {skaters.map((d, i) => (
                <Area key={d.skater_id} type="monotone" dataKey={label(d)}
                  stroke={COLORS[i % COLORS.length]} strokeWidth={2}
                  fill={`url(#grad-s-${dist}-${i})`}
                  dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls animationDuration={600} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </>
      )
    } else {
      const dates = [...new Set(data.flatMap(d => d.event_bests.filter(b => b.distance_m === dist && inRangeYear(b.start_date)).map(b => b.start_date ?? b.event_name)))].sort((a, b) => compareDates(a, b))
      const chartData: ChartPoint[] = dates.map(dt => {
        const pt: ChartPoint = { date: dt, _events: {} }
        data.forEach(d => {
          const b = d.event_bests.find(x => x.distance_m === dist && (x.start_date ?? x.event_name) === dt)
          if (b) { pt[label(d)] = b.best_time; pt._events[label(d)] = b.event_name }
        })
        return pt
      })
      if (chartData.length < 2) return null
      const skaters = data.filter(d => d.event_bests.some(b => b.distance_m === dist))
      return (
        <>
          <h3 className="font-semibold mb-2">{dist}m (event date)</h3>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={chartData}>
              <defs>
                {skaters.map((d, i) => (
                  <linearGradient key={d.skater_id} id={`grad-e-${dist}-${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0.15} />
                    <stop offset="95%" stopColor={COLORS[i % COLORS.length]} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis tickFormatter={formatTime} tick={{ fontSize: 11 }} width={64} domain={["auto", "auto"]} />
              <Tooltip
                formatter={(v, name) => [typeof v === "number" ? formatTime(v) : String(v), String(name)]}
                labelFormatter={eventTooltipLabel}
              />
              <Legend />
              {skaters.map((d, i) => (
                <Area key={d.skater_id} type="monotone" dataKey={label(d)}
                  stroke={COLORS[i % COLORS.length]} strokeWidth={2}
                  fill={`url(#grad-e-${dist}-${i})`}
                  dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls animationDuration={600} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </>
      )
    }
  }

  return (
    <div className="space-y-3">
      {mode === "season" && allSeasons.length > 1 && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Season range:</span>
          <select
            value={effectiveFromSeason}
            onChange={e => {
              setFromSeason(e.target.value)
              if (e.target.value > effectiveToSeason) setToSeason(e.target.value)
            }}
            className="border rounded-md px-2 py-1 bg-background text-sm"
          >
            {allSeasons.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <span className="text-muted-foreground">to</span>
          <select
            value={effectiveToSeason}
            onChange={e => {
              setToSeason(e.target.value)
              if (e.target.value < effectiveFromSeason) setFromSeason(e.target.value)
            }}
            className="border rounded-md px-2 py-1 bg-background text-sm"
          >
            {allSeasons.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          {(fromSeason || toSeason) && (
            <button onClick={() => { setFromSeason(""); setToSeason("") }}
              className="text-xs text-muted-foreground hover:text-foreground underline">Reset</button>
          )}
        </div>
      )}
      {mode === "event" && allYears.length > 1 && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Year range:</span>
          <select
            value={effectiveFromYear}
            onChange={e => {
              const v = Number(e.target.value)
              setFromYear(v)
              if (v > effectiveToYear) setToYear(v)
            }}
            className="border rounded-md px-2 py-1 bg-background text-sm"
          >
            {allYears.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          <span className="text-muted-foreground">to</span>
          <select
            value={effectiveToYear}
            onChange={e => {
              const v = Number(e.target.value)
              setToYear(v)
              if (v < effectiveFromYear) setFromYear(v)
            }}
            className="border rounded-md px-2 py-1 bg-background text-sm"
          >
            {allYears.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          {(fromYear || toYear) && (
            <button onClick={() => { setFromYear(0); setToYear(0) }}
              className="text-xs text-muted-foreground hover:text-foreground underline">Reset</button>
          )}
        </div>
      )}
      <div className="grid md:grid-cols-2 gap-4">
        {distances.map(dist => {
          const chart = renderChart(dist)
          return chart ? <div key={dist}>{chart}</div> : null
        })}
      </div>
    </div>
  )
}
