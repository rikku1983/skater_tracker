"use client"

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot } from "recharts"
import { formatTime } from "@/lib/utils"

interface Point {
  x: string
  y: number
  label?: string
  isPR?: boolean
}

interface TimeChartProps {
  data: Point[]
  color?: string
  tickFormatter?: (v: number) => string
}

function defaultTick(v: number) { return formatTime(v) }

export function TimeChart({ data, color = "#2563eb", tickFormatter = defaultTick }: TimeChartProps) {
  const minY = Math.min(...data.map(d => d.y))

  const annotated = data.map((d, i) => ({
    ...d,
    isPR: d.y < Math.min(...data.slice(0, i + 1).slice(0, -0).map(p => (i === 0 ? Infinity : p.y)), Infinity) || i === 0
      ? true
      : d.y < Math.min(...data.slice(0, i).map(p => p.y)),
  }))

  return (
    <ResponsiveContainer width="100%" height={180}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis dataKey="x" tick={{ fontSize: 11 }} />
        <YAxis
          tickFormatter={tickFormatter}
          tick={{ fontSize: 11 }}
          domain={[minY * 0.97, "auto"]}
          width={60}
        />
        <Tooltip
          formatter={(v) => [typeof v === "number" ? tickFormatter(v) : String(v), "Time"]}
          labelClassName="font-medium"
        />
        <Line
          type="monotone"
          dataKey="y"
          stroke={color}
          strokeWidth={2}
          dot={{ r: 3 }}
          activeDot={{ r: 5 }}
        />
        {annotated.filter(d => d.isPR).map((d, i) => (
          <ReferenceDot key={i} x={d.x} y={d.y} r={5} fill={color} stroke="white" strokeWidth={2} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}
