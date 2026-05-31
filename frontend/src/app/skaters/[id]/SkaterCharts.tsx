"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot } from "recharts"
import { formatTime } from "@/lib/utils"

interface Point { x: string; y: number; label?: string }

interface SkaterChartsProps {
  chartData: Record<number, Point[]>
  eventChartData: Record<number, Point[]>
}

const DIST_COLORS: Record<number, string> = {
  107: "#64748b",
  222: "#8b5cf6",
  333: "#7c3aed",
  500: "#2563eb",
  777: "#059669",
  1000: "#d97706",
  1500: "#dc2626",
}

function prFlags(data: Point[]) {
  let best = Infinity
  return data.map(d => {
    const isPR = d.y < best
    if (isPR) best = d.y
    return isPR
  })
}

function Chart({ data, color }: { data: Point[]; color: string }) {
  const flags = prFlags(data)
  const minY = Math.min(...data.map(d => d.y))

  const gradId = `grad-${color.replace("#", "")}`

  return (
    <ResponsiveContainer width="100%" height={180}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.25} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis dataKey="x" tick={{ fontSize: 11 }} />
        <YAxis
          tickFormatter={formatTime}
          tick={{ fontSize: 11 }}
          domain={[minY * 0.97, "auto"]}
          width={60}
        />
        <Tooltip
          formatter={(v) => [typeof v === "number" ? formatTime(v) : String(v), "Time"]}
          labelFormatter={(label, payload) => {
            const pt = payload?.[0]?.payload as Point | undefined
            return pt?.label ? `${pt.label}` : String(label)
          }}
        />
        <Area type="monotone" dataKey="y" stroke={color} strokeWidth={2}
          fill={`url(#${gradId})`} dot={{ r: 3 }} activeDot={{ r: 5 }}
          animationDuration={600} />
        {data.map((d, i) =>
          flags[i] ? (
            <ReferenceDot key={i} x={d.x} y={d.y} r={5} fill={color} stroke="white" strokeWidth={2} />
          ) : null
        )}
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function SkaterCharts({ chartData, eventChartData }: SkaterChartsProps) {
  const [mode, setMode] = useState<"season" | "event">("season")

  const activeData = mode === "season" ? chartData : eventChartData

  // 500 always first; remaining distances sorted by total data points (both modes) descending
  function distCount(d: number) {
    return (chartData[d]?.length ?? 0) + (eventChartData[d]?.length ?? 0)
  }
  const distances = Object.keys(activeData).map(Number)
    .filter(d => activeData[d].length >= 2)
    .sort((a, b) => {
      if (a === 500) return -1
      if (b === 500) return 1
      return distCount(b) - distCount(a)
    })

  const hasSeasonData = Object.keys(chartData).length > 0
  const hasEventData = Object.keys(eventChartData).length > 0

  if (!hasSeasonData && !hasEventData) return null

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">
          {mode === "season" ? "Season Best Times" : "Event Best Times"}
        </CardTitle>
        <div className="flex gap-1">
          <Button
            variant={mode === "season" ? "default" : "outline"}
            size="sm"
            className="h-7 text-xs"
            onClick={() => setMode("season")}
          >
            Season
          </Button>
          <Button
            variant={mode === "event" ? "default" : "outline"}
            size="sm"
            className="h-7 text-xs"
            onClick={() => setMode("event")}
          >
            By Event
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {distances.map(dist => (
          <div key={dist}>
            <div className="text-xs font-medium text-muted-foreground mb-1">{dist}m</div>
            <Chart data={activeData[dist]} color={DIST_COLORS[dist] ?? "#2563eb"} />
          </div>
        ))}
        {distances.length === 0 && (
          <p className="text-sm text-muted-foreground py-2">No data for this view.</p>
        )}
      </CardContent>
    </Card>
  )
}
