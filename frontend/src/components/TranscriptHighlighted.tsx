"use client";

import { SlowSegment, TranscriptWord } from "@/lib/api";

// Same thresholds as the backend's fluency metrics (app/metrics/fluency.py:
// NATURAL_PAUSE_S / HESITATION_PAUSE_S) — recomputed here from raw word
// timestamps rather than matched against the API's `pauses` list, so there's
// no float-equality matching between a backend-rounded value and a raw one.
const NATURAL_PAUSE_S = 0.5;
const HESITATION_PAUSE_S = 2.0;

function isInSlowSegment(word: TranscriptWord, slowSegments: SlowSegment[]): boolean {
  return slowSegments.some((s) => word.start_s >= s.start_s && word.start_s < s.end_s);
}

/** Transcript with slow (amber background) and hesitant-pause (red gap
 * marker) segments highlighted, per the Day 5 feedback page requirement. */
export default function TranscriptHighlighted({
  words,
  slowSegments,
}: {
  words: TranscriptWord[];
  slowSegments: SlowSegment[];
}) {
  if (words.length === 0) {
    return <p className="text-sm text-gray-500">No transcript available.</p>;
  }

  return (
    <p className="leading-relaxed">
      {words.map((w, i) => {
        const gap = i > 0 ? w.start_s - words[i - 1].end_s : 0;
        const slow = isInSlowSegment(w, slowSegments);
        return (
          <span key={i}>
            {i > 0 && gap >= NATURAL_PAUSE_S && (
              <span
                className={
                  gap >= HESITATION_PAUSE_S
                    ? "mx-0.5 rounded bg-red-100 px-1 text-[10px] text-red-700 dark:bg-red-950 dark:text-red-400"
                    : "mx-0.5 rounded bg-amber-100 px-1 text-[10px] text-amber-700 dark:bg-amber-950 dark:text-amber-400"
                }
                title={`Pause: ${gap.toFixed(1)}s`}
              >
                ⏸ {gap.toFixed(1)}s
              </span>
            )}
            <span className={slow ? "rounded bg-amber-100 dark:bg-amber-950" : undefined} title={slow ? "Slow segment (<90 wpm)" : undefined}>
              {w.word}
            </span>{" "}
          </span>
        );
      })}
    </p>
  );
}
