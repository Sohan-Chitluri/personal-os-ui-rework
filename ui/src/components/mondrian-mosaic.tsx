import { useEffect, useState, type ReactNode } from "react";

type Rect = [number, number, number, number]; // left, top, width, height in %
const tones = ["bg-block-red", "bg-canvas", "bg-block-yellow", "bg-canvas", "bg-block-blue", "bg-canvas"];

// Every layout is a full, gap-free tiling. Tiles move in lockstep (no per-tile
// delay) so the composition never shows holes or overlaps mid-transition.
const layouts: Rect[][] = [
  [[0, 0, 60, 65], [60, 0, 40, 35], [60, 35, 40, 30], [0, 65, 25, 35], [25, 65, 45, 35], [70, 65, 30, 35]],
  [[0, 0, 35, 40], [35, 0, 65, 55], [0, 40, 35, 60], [35, 55, 30, 45], [65, 55, 35, 20], [65, 75, 35, 25]],
  [[0, 0, 25, 50], [25, 0, 50, 30], [75, 0, 25, 70], [25, 30, 50, 70], [0, 50, 25, 50], [75, 70, 25, 30]],
  [[0, 0, 70, 30], [70, 0, 30, 60], [0, 30, 40, 70], [40, 30, 30, 40], [40, 70, 30, 30], [70, 60, 30, 40]],
];

export function MondrianMosaic({ children, interval = 3200 }: { children?: ReactNode; interval?: number }) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setStep((s) => s + 1), interval);
    return () => window.clearInterval(id);
  }, [interval]);
  const layout = layouts[step % layouts.length]!;
  return (
    <div className="mosaic h-full">
      {layout.map(([l, t, w, h], i) => (
        <span key={i} aria-hidden className={`mosaic-tile ${tones[i]}`} style={{ left: `${l}%`, top: `${t}%`, width: `${w}%`, height: `${h}%` }} />
      ))}
      {children && (
        <div className="absolute bottom-3 left-3 z-10 border-[3px] border-grid bg-canvas px-3 py-2 text-foreground">{children}</div>
      )}
    </div>
  );
}
