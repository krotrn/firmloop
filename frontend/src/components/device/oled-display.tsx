export interface OledDisplayProps {
  rows: number;
  cols: number;
  lines: readonly string[];
}

/** A dark monospace box of `rows` lines, each padded or clipped to `cols` characters. */
export function OledDisplay({ rows, cols, lines }: OledDisplayProps) {
  const shown = Array.from({ length: rows }, (_, index) =>
    (lines[index] ?? "").slice(0, cols).padEnd(cols, " ")
  );
  return (
    <div
      role="group"
      aria-label="Display"
      className="w-fit max-w-full overflow-x-auto rounded-xl bg-neutral-950 p-4 font-mono text-sm leading-6 text-emerald-300"
    >
      {shown.map((line, index) => (
        <div key={index} className="whitespace-pre" data-testid="oled-line">
          {line}
        </div>
      ))}
    </div>
  );
}
