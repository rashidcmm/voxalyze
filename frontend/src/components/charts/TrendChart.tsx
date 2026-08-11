"use client";

import { useState } from "react";
import { ProgressPoint } from "@/lib/api";
import { DIMENSIONS, dimensionColorVar } from "@/lib/dimensions";

const WIDTH = 640;
const HEIGHT = 300;
const MARGIN = { top: 16, right: 16, bottom: 28, left: 32 };
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;

function scaleX(i: number, n: number): number {
  if (n <= 1) return MARGIN.left + PLOT_W / 2;
  return MARGIN.left + (i / (n - 1)) * PLOT_W;
}

function scaleY(v: number): number {
  return MARGIN.top + PLOT_H - (v / 100) * PLOT_H;
}

/** Multi-line EWMA trend chart — one line per headline dimension, 0-100.
 * Gaps (a dimension not yet configured, e.g. no Azure/Anthropic key) are
 * skipped rather than drawn as a false zero — see the coords filter below.
 */
export default function TrendChart({ points }: { points: ProgressPoint[] }) {
  const [showTable, setShowTable] = useState(false);

  if (points.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        No sessions with metrics yet — trends appear after your first completed session.
      </p>
    );
  }

  const gridLines = [0, 25, 50, 75, 100];

  return (
    <div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        role="img"
        aria-label="Headline score trends across sessions"
      >
        {gridLines.map((v) => (
          <g key={v}>
            <line
              x1={MARGIN.left}
              x2={WIDTH - MARGIN.right}
              y1={scaleY(v)}
              y2={scaleY(v)}
              stroke="var(--chart-grid)"
              strokeWidth={1}
            />
            <text
              x={MARGIN.left - 6}
              y={scaleY(v)}
              textAnchor="end"
              dominantBaseline="middle"
              fill="var(--chart-muted)"
              fontSize={9}
            >
              {v}
            </text>
          </g>
        ))}
        <line
          x1={MARGIN.left}
          x2={WIDTH - MARGIN.right}
          y1={scaleY(0)}
          y2={scaleY(0)}
          stroke="var(--chart-axis)"
          strokeWidth={1}
        />

        {[0, Math.floor((points.length - 1) / 2), points.length - 1]
          .filter((i, idx, arr) => arr.indexOf(i) === idx)
          .map((i) => (
            <text
              key={i}
              x={scaleX(i, points.length)}
              y={HEIGHT - 8}
              textAnchor="middle"
              fill="var(--chart-muted)"
              fontSize={9}
            >
              {new Date(points[i].created_at).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              })}
            </text>
          ))}

        {DIMENSIONS.map((dim) => {
          const key = `${dim.key}_ewma` as keyof ProgressPoint;
          const coords = points
            .map((p, i) => ({ i, v: p[key] as number | null }))
            .filter((c): c is { i: number; v: number } => c.v !== null);
          if (coords.length === 0) return null;

          const path = coords
            .map((c, idx) => `${idx === 0 ? "M" : "L"}${scaleX(c.i, points.length)},${scaleY(c.v)}`)
            .join(" ");
          const color = dimensionColorVar(dim.key);

          return (
            <g key={dim.key}>
              <path
                d={path}
                fill="none"
                stroke={color}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {coords.map((c) => (
                <circle key={c.i} cx={scaleX(c.i, points.length)} cy={scaleY(c.v)} r={3} fill={color}>
                  <title>
                    {dim.label}: {c.v.toFixed(1)} ({new Date(points[c.i].created_at).toLocaleDateString()})
                  </title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {DIMENSIONS.map((dim) => (
          <div key={dim.key} className="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400">
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: dimensionColorVar(dim.key) }}
            />
            {dim.label}
          </div>
        ))}
      </div>

      <button
        onClick={() => setShowTable((s) => !s)}
        className="mt-3 text-xs underline text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
      >
        {showTable ? "Hide" : "Show"} as table
      </button>

      {showTable && (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-black/10 dark:border-white/10">
                <th className="p-1 text-left">Date</th>
                {DIMENSIONS.map((d) => (
                  <th key={d.key} className="p-1 text-right">
                    {d.label}
                  </th>
                ))}
                <th className="p-1 text-right">Overall</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.session_id} className="border-b border-black/5 dark:border-white/5">
                  <td className="p-1">{new Date(p.created_at).toLocaleDateString()}</td>
                  {DIMENSIONS.map((d) => (
                    <td key={d.key} className="p-1 text-right tabular-nums">
                      {p[d.key] ?? "—"}
                    </td>
                  ))}
                  <td className="p-1 text-right tabular-nums">{p.overall}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
