// The 5 headline scoring dimensions (Day 5). Colors live as CSS custom
// properties (--dim-<key>) in globals.css — the first 5 slots of the
// validated categorical palette, in fixed order (blue, orange, aqua, yellow,
// magenta), swapped per light/dark media query there. Chart components read
// them via var(--dim-<key>) so a dimension's color means the same thing
// everywhere it appears, without relying on Tailwind's static class scanner
// (which can't see dynamically-built arbitrary-value class names).
export interface DimensionSpec {
  key: "fluency" | "vocabulary" | "clarity" | "relevance" | "argumentation";
  label: string;
}

export const DIMENSIONS: DimensionSpec[] = [
  { key: "fluency", label: "Fluency" },
  { key: "vocabulary", label: "Vocabulary" },
  { key: "clarity", label: "Clarity" },
  { key: "relevance", label: "Relevance" },
  { key: "argumentation", label: "Argumentation" },
];

export function dimensionColorVar(key: DimensionSpec["key"]): string {
  return `var(--dim-${key})`;
}
