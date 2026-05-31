import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatTime(seconds: number | null | undefined): string {
  if (seconds == null) return "—"
  if (seconds >= 60) {
    const m = Math.floor(seconds / 60)
    const s = (seconds % 60).toFixed(3).padStart(6, "0")
    return `${m}:${s}`
  }
  return seconds.toFixed(3)
}

export function formatSeason(season: string | null | undefined): string {
  if (!season) return "—"
  return season
}

// Dates in the DB are stored as "M/D/YYYY" text. These utilities let you
// sort and compare them correctly without relying on lexicographic order.

/** Returns a numeric sort key (YYYYMMDD) for a "M/D/YYYY" date string. */
export function dateSortKey(s: string | null | undefined): number {
  if (!s) return 0
  const parts = s.split("/")
  if (parts.length !== 3) return 0
  const [m, d, y] = parts.map(Number)
  if (!y || !m || !d) return 0
  return y * 10000 + m * 100 + d
}

/** Comparator for sorting "M/D/YYYY" strings chronologically. */
export function compareDates(a: string | null | undefined, b: string | null | undefined): number {
  return dateSortKey(a) - dateSortKey(b)
}

/** SQL fragment to sort a "M/D/YYYY" column correctly (ascending). */
export const SQL_DATE_ASC = (col: string) =>
  `CAST(substr(${col},-4) AS INTEGER),` +
  `CAST(substr(${col},1,instr(${col},'/')-1) AS INTEGER),` +
  `CAST(substr(${col},instr(${col},'/')+1) AS INTEGER)`

/** SQL fragment to sort a "M/D/YYYY" column correctly (descending). */
export const SQL_DATE_DESC = (col: string) =>
  `CAST(substr(${col},-4) AS INTEGER) DESC,` +
  `CAST(substr(${col},1,instr(${col},'/')-1) AS INTEGER) DESC,` +
  `CAST(substr(${col},instr(${col},'/')+1) AS INTEGER) DESC`
