"use client";

import { HeadlineScores } from "@/lib/api";
import { DIMENSIONS, dimensionColorVar } from "@/lib/dimensions";

const SIZE = 280;
const CENTER = SIZE / 2;
const MAX_R = 100;
const RINGS = [25, 50, 75, 100];

function axisPoint(index: number, total: number, radius: number): { x: number; y: number } {
  // Start straight up, go clockwise.
  const angle = (Math.PI * 2 * index) / total - Math.PI / 2;
  return { x: CENTER + radius * Math.cos(angle), y: CENTER + radius * Math.sin(angle) };
}

/** Latest-session radar/spider chart across the 5 headline dimensions.
 * A dimension that isn't configured yet (null) plots at the center with a
 * dashed, muted marker rather than a false zero, so "not scored" is visibly
 * different from "scored zero".
 */
export default function RadarChart({ scores }: { scores: HeadlineScores }) {
  const n = DIMENSIONS.length;
  const values = DIMENSIONS.map((d) => scores[d.key]);

  const polygonPoints = values
    .map((v, i) => {
      const p = axisPoint(i, n, ((v ?? 0) / 100) * MAX_R);
      return `${p.x},${p.y}`;
    })
    .join(" ");

  return (
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="w-full max-w-xs" role="img" aria-label="Latest session radar chart">
      {RINGS.map((r) => {
        const ringPoints = DIMENSIONS.map((_, i) => {
          const p = axisPoint(i, n, (r / 100) * MAX_R);
          return `${p.x},${p.y}`;
        }).join(" ");
        return (
          <polygon
            key={r}
            points={ringPoints}
            fill="none"
            stroke="var(--chart-grid)"
            strokeWidth={1}
          />
        );
      })}

      {DIMENSIONS.map((_, i) => {
        const p = axisPoint(i, n, MAX_R);
        return (
          <line
            key={i}
            x1={CENTER}
            y1={CENTER}
            x2={p.x}
            y2={p.y}
            stroke="var(--chart-axis)"
            strokeWidth={1}
          />
        );
      })}

      <polygon
        points={polygonPoints}
        fill="var(--dim-fluency)"
        fillOpacity={0.15}
        stroke="var(--dim-fluency)"
        strokeWidth={2}
        strokeLinejoin="round"
      />

      {DIMENSIONS.map((dim, i) => {
        const v = values[i];
        const p = axisPoint(i, n, ((v ?? 0) / 100) * MAX_R);
        const color = dimensionColorVar(dim.key);
        return v === null ? (
          <circle
            key={dim.key}
            cx={p.x}
            cy={p.y}
            r={3}
            fill="none"
            stroke={color}
            strokeDasharray="2,2"
            strokeWidth={1.5}
          >
            <title>{dim.label}: not scored yet</title>
          </circle>
        ) : (
          <circle key={dim.key} cx={p.x} cy={p.y} r={3} fill={color}>
            <title>
              {dim.label}: {v.toFixed(1)}
            </title>
          </circle>
        );
      })}

      {DIMENSIONS.map((dim, i) => {
        const p = axisPoint(i, n, MAX_R + 18);
        return (
          <text
            key={dim.key}
            x={p.x}
            y={p.y}
            textAnchor="middle"
            dominantBaseline="middle"
            fill="var(--chart-muted)"
            fontSize={9}
          >
            {dim.label}
          </text>
        );
      })}
    </svg>
  );
}
