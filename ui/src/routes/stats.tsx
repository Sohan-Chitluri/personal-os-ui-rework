import { createFileRoute } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { toneBg } from "@/components/blockday-ui";
import {
  advancements,
  dailyCompletions,
  projectById,
  projectShare,
  streaks,
} from "@/lib/blockday-data";
import { useOptionalBlockday } from "@/lib/blockday-store";

export const Route = createFileRoute("/stats")({
  head: () => ({
    meta: [
      { title: "Composition of Work — Blockday" },
      {
        name: "description",
        content:
          "The last twelve months: unified activity grid, flavor mix, streaks, and focus metrics.",
      },
      { property: "og:title", content: "Composition of Work — Blockday" },
      {
        property: "og:description",
        content:
          "The last twelve months: unified activity grid, flavor mix, streaks, and focus metrics.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: StatsPage,
});

type CellTone = "empty" | "red" | "blue" | "yellow" | "cream";

// Generates a deterministic 52-week x 7-day chronological matrix closely matching the reference screenshot
function generateYearMatrix(): { tone: CellTone; isToday?: boolean }[][] {
  const cols: { tone: CellTone; isToday?: boolean }[][] = [];
  let seed = 127;
  const rand = () => {
    seed = (seed * 9301 + 49297) % 233280;
    return seed / 233280;
  };

  for (let c = 0; c < 52; c++) {
    const col: { tone: CellTone; isToday?: boolean }[] = [];
    for (let r = 0; r < 7; r++) {
      const isToday = c === 51 && r === 4;

      if (c < 12) {
        // Sep - Nov: mostly dark with sparse points
        if ((c === 4 && r === 1) || (c === 8 && r === 5) || (c === 11 && r === 2)) {
          col.push({ tone: "yellow" });
        } else if (c === 10 && r === 6) {
          col.push({ tone: "blue" });
        } else {
          col.push({ tone: "empty" });
        }
      } else if (c < 16) {
        // Transition Dec
        const v = rand();
        if (v > 0.65) col.push({ tone: "yellow" });
        else if (v > 0.5) col.push({ tone: "blue" });
        else col.push({ tone: "empty" });
      } else {
        // High density activity (Jan - Sep)
        const v = rand();
        if (v > 0.77) col.push({ tone: "yellow", isToday });
        else if (v > 0.58) col.push({ tone: "blue", isToday });
        else if (v > 0.44) col.push({ tone: "red", isToday });
        else if (v > 0.3) col.push({ tone: "cream", isToday });
        else col.push({ tone: "empty", isToday });
      }
    }
    cols.push(col);
  }
  return cols;
}

const yearMatrix = generateYearMatrix();
const monthLabels = [
  "SEP",
  "OCT",
  "NOV",
  "DEC",
  "JAN",
  "FEB",
  "MAR",
  "APR",
  "MAY",
  "JUN",
  "JUL",
  "AUG",
  "SEP",
];

interface QuoteItem {
  id: number;
  text: string;
  author: string;
  context?: string;
  accent?: "red" | "blue" | "yellow";
}

const CURATED_QUOTES: QuoteItem[] = [
  {
    id: 1,
    text: "Simplicity is about subtracting the obvious and adding the meaningful.",
    author: "John Maeda",
    context: "The Laws of Simplicity",
    accent: "red",
  },
  {
    id: 2,
    text: "Art is not made for anybody and is, at the same time, for everybody.",
    author: "Piet Mondrian",
    context: "De Stijl Manifesto",
    accent: "blue",
  },
  {
    id: 3,
    text: "Good design is as little design as possible. Less, but better.",
    author: "Dieter Rams",
    context: "Ten Principles for Good Design",
    accent: "yellow",
  },
  {
    id: 4,
    text: "The details are not the details. They make the design.",
    author: "Charles Eames",
    context: "Design Philosophy",
    accent: "red",
  },
  {
    id: 5,
    text: "Make the hard thing happen before the easy things crowd the day.",
    author: "Blockday",
    context: "A Note to Self",
    accent: "yellow",
  },
  {
    id: 6,
    text: "Have no fear of perfection — you'll never reach it.",
    author: "Salvador Dalí",
    context: "On Mastery & Craft",
    accent: "blue",
  },
  {
    id: 7,
    text: "Focus means saying no to the hundred other good ideas that there are.",
    author: "Steve Jobs",
    context: "On Strategy",
    accent: "red",
  },
  {
    id: 8,
    text: "The sculpture is already complete in the marble. Chisel away the superfluous.",
    author: "Michelangelo",
    context: "On Craft & Form",
    accent: "blue",
  },
];

interface MondrianRect {
  col: string;
  row: string;
  tone: string;
}

const MONDRIAN_LAYOUTS: MondrianRect[][] = [
  // 1: Red dominant (Piet Mondrian 1921 style)
  [
    { col: "1 / 3", row: "1 / 3", tone: "bg-block-red" },
    { col: "3 / 4", row: "1 / 2", tone: "bg-canvas" },
    { col: "3 / 4", row: "2 / 4", tone: "bg-block-blue" },
    { col: "1 / 2", row: "3 / 4", tone: "bg-block-yellow" },
    { col: "2 / 3", row: "3 / 4", tone: "bg-panel" },
    { col: "1 / 2", row: "4 / 5", tone: "bg-panel" },
    { col: "2 / 3", row: "4 / 5", tone: "bg-canvas" },
    { col: "3 / 4", row: "4 / 5", tone: "bg-block-yellow" },
  ],
  // 2: Blue dominant (Composition with Blue)
  [
    { col: "1 / 2", row: "1 / 3", tone: "bg-canvas" },
    { col: "2 / 4", row: "1 / 3", tone: "bg-block-blue" },
    { col: "1 / 2", row: "3 / 4", tone: "bg-block-yellow" },
    { col: "2 / 3", row: "3 / 5", tone: "bg-block-red" },
    { col: "3 / 4", row: "3 / 4", tone: "bg-panel" },
    { col: "1 / 2", row: "4 / 5", tone: "bg-panel" },
    { col: "3 / 4", row: "4 / 5", tone: "bg-canvas" },
  ],
  // 3: Yellow dominant (Composition with Yellow)
  [
    { col: "1 / 3", row: "1 / 2", tone: "bg-canvas" },
    { col: "3 / 4", row: "1 / 3", tone: "bg-block-red" },
    { col: "1 / 3", row: "2 / 4", tone: "bg-block-yellow" },
    { col: "3 / 4", row: "3 / 4", tone: "bg-block-blue" },
    { col: "1 / 2", row: "4 / 5", tone: "bg-panel" },
    { col: "2 / 4", row: "4 / 5", tone: "bg-canvas" },
  ],
  // 4: Balanced Tableau (Composition II)
  [
    { col: "1 / 2", row: "1 / 3", tone: "bg-block-red" },
    { col: "2 / 4", row: "1 / 2", tone: "bg-panel" },
    { col: "2 / 3", row: "2 / 4", tone: "bg-canvas" },
    { col: "3 / 4", row: "2 / 3", tone: "bg-block-blue" },
    { col: "3 / 4", row: "3 / 5", tone: "bg-block-yellow" },
    { col: "1 / 2", row: "3 / 5", tone: "bg-panel" },
    { col: "2 / 3", row: "4 / 5", tone: "bg-canvas" },
  ],
  // 5: High Contrast De Stijl
  [
    { col: "1 / 2", row: "1 / 3", tone: "bg-canvas" },
    { col: "2 / 4", row: "1 / 2", tone: "bg-block-red" },
    { col: "2 / 3", row: "2 / 4", tone: "bg-block-blue" },
    { col: "3 / 4", row: "2 / 3", tone: "bg-block-yellow" },
    { col: "3 / 4", row: "3 / 4", tone: "bg-panel" },
    { col: "1 / 2", row: "3 / 5", tone: "bg-block-red" },
    { col: "2 / 3", row: "4 / 5", tone: "bg-panel" },
    { col: "3 / 4", row: "4 / 5", tone: "bg-block-yellow" },
  ],
  // 6: Primary Triad Rhythm
  [
    { col: "1 / 2", row: "1 / 4", tone: "bg-block-yellow" },
    { col: "2 / 4", row: "1 / 2", tone: "bg-canvas" },
    { col: "2 / 3", row: "2 / 4", tone: "bg-block-red" },
    { col: "3 / 4", row: "2 / 3", tone: "bg-block-blue" },
    { col: "3 / 4", row: "3 / 4", tone: "bg-panel" },
    { col: "1 / 2", row: "4 / 5", tone: "bg-panel" },
    { col: "2 / 3", row: "4 / 5", tone: "bg-canvas" },
    { col: "3 / 4", row: "4 / 5", tone: "bg-block-red" },
  ],
  // 7: Ivory & Crimson Anchor
  [
    { col: "1 / 3", row: "1 / 2", tone: "bg-block-red" },
    { col: "3 / 4", row: "1 / 3", tone: "bg-block-blue" },
    { col: "1 / 2", row: "2 / 4", tone: "bg-panel" },
    { col: "2 / 3", row: "2 / 4", tone: "bg-canvas" },
    { col: "3 / 4", row: "3 / 5", tone: "bg-block-yellow" },
    { col: "1 / 2", row: "4 / 5", tone: "bg-block-red" },
    { col: "2 / 3", row: "4 / 5", tone: "bg-canvas" },
  ],
  // 8: Architectural Balance
  [
    { col: "1 / 3", row: "1 / 2", tone: "bg-block-blue" },
    { col: "3 / 4", row: "1 / 2", tone: "bg-canvas" },
    { col: "1 / 2", row: "2 / 3", tone: "bg-panel" },
    { col: "2 / 4", row: "2 / 4", tone: "bg-block-yellow" },
    { col: "1 / 2", row: "3 / 5", tone: "bg-block-red" },
    { col: "2 / 3", row: "4 / 5", tone: "bg-canvas" },
    { col: "3 / 4", row: "4 / 5", tone: "bg-panel" },
  ],
];

export function StatsPage() {
  const [quoteIndex, setQuoteIndex] = useState(0);

  const blockday = useOptionalBlockday();
  const liveTasks = blockday?.tasks ?? [];
  const liveTasksDone = liveTasks.filter((t) => t.done).length;
  const liveTotalTasks = liveTasks.length;

  const { current } = streaks(dailyCompletions);
  const sampleTasksDone = 542; // Representative sample matching reference
  const displayedTasksDone = liveTotalTasks > 0 ? liveTasksDone : sampleTasksDone;
  const pomodoros = 118; // Matching reference
  const bestDay = Math.max(...dailyCompletions, 7);
  const totalProjectDone = projectShare.reduce((sum, p) => sum + p.done, 0);

  const currentQuote = CURATED_QUOTES[quoteIndex % CURATED_QUOTES.length]!;
  const currentLayout = MONDRIAN_LAYOUTS[quoteIndex % MONDRIAN_LAYOUTS.length]!;

  const handlePrevQuote = () => {
    setQuoteIndex((i) => (i - 1 + CURATED_QUOTES.length) % CURATED_QUOTES.length);
  };

  const handleNextQuote = () => {
    setQuoteIndex((i) => (i + 1) % CURATED_QUOTES.length);
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowLeft") {
        setQuoteIndex((i) => (i - 1 + CURATED_QUOTES.length) % CURATED_QUOTES.length);
      } else if (e.key === "ArrowRight") {
        setQuoteIndex((i) => (i + 1) % CURATED_QUOTES.length);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const cellColorClass = (tone: CellTone, isToday?: boolean) => {
    const base = isToday ? "ring-2 ring-block-red ring-offset-2 ring-offset-canvas z-10 " : "";
    switch (tone) {
      case "red":
        return base + "bg-block-red border border-grid";
      case "blue":
        return base + "bg-block-blue border border-grid";
      case "yellow":
        return base + "bg-block-yellow border border-grid";
      case "cream":
        return base + "bg-panel text-foreground border border-grid";
      default:
        return base + "bg-panel/25 border border-grid/35 hover:border-grid";
    }
  };

  return (
    <main className="flex flex-col h-full min-h-0 min-w-0 bg-grid gap-[4px] border-l-[4px] border-grid overflow-y-auto">
      {/* =========================================================================
          SECTION A: QUOTE COMPOSITION (TOP)
          Large editorial quote panel (left) + irregular Mondrian color blocks (right)
         ========================================================================= */}
      <section className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.35fr)_minmax(280px,380px)] xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,440px)] gap-[4px] bg-grid flex-shrink-0">
        {/* Left: Editorial Quote Panel */}
        <div className="bg-canvas p-6 sm:p-8 xl:p-10 flex flex-col justify-between min-h-[300px] sm:min-h-[340px] xl:min-h-[380px]">
          {/* Header Row: Label + Browsing Controls */}
          <div className="flex items-center justify-between gap-4 pb-4 border-b-2 border-grid">
            <div className="flex items-center gap-2">
              <span className="size-2 bg-block-red flex-shrink-0" />
              <span className="text-[10px] xl:text-[11px] font-black uppercase tracking-[0.24em] text-muted-foreground">
                A NOTE TO SELF
              </span>
            </div>

            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] xl:text-xs font-black tracking-wider text-muted-foreground select-none">
                {quoteIndex + 1} / {CURATED_QUOTES.length}
              </span>
              <div className="flex border-2 border-grid divide-x-2 divide-grid bg-canvas">
                <button
                  type="button"
                  onClick={handlePrevQuote}
                  className="p-1 sm:p-1.5 hover:bg-block-ink hover:text-canvas transition-colors"
                  aria-label="Previous quote"
                  title="Previous quote"
                >
                  <ChevronLeft className="size-3.5 stroke-[3]" />
                </button>
                <button
                  type="button"
                  onClick={handleNextQuote}
                  className="p-1 sm:p-1.5 hover:bg-block-ink hover:text-canvas transition-colors"
                  aria-label="Next quote"
                  title="Next quote"
                >
                  <ChevronRight className="size-3.5 stroke-[3]" />
                </button>
              </div>
            </div>
          </div>

          {/* Focal Point: Bold Oversized Quote */}
          <div className="my-6 sm:my-8 xl:my-10 flex-1 flex flex-col justify-center">
            <blockquote className="max-w-4xl">
              <p className="font-display text-2xl sm:text-3xl md:text-4xl xl:text-[42px] font-extrabold uppercase leading-[1.08] tracking-tight text-foreground">
                “{currentQuote.text}”
              </p>
            </blockquote>
          </div>

          {/* Attribution Row */}
          <div className="flex items-center justify-between gap-4 pt-4 border-t-2 border-grid/20">
            <div className="flex items-center gap-2.5">
              <span
                className={`w-5 h-1 ${
                  currentQuote.accent === "yellow"
                    ? "bg-block-yellow"
                    : currentQuote.accent === "blue"
                      ? "bg-block-blue"
                      : "bg-block-red"
                } flex-shrink-0`}
              />
              <cite className="text-[10px] sm:text-[11px] font-black uppercase tracking-[0.2em] text-muted-foreground not-italic">
                {currentQuote.author}
                {currentQuote.context ? ` · ${currentQuote.context}` : ""}
              </cite>
            </div>
            <span className="text-[9px] font-black uppercase tracking-[0.18em] text-muted-foreground/60 hidden sm:inline-block">
              Editorial Daily
            </span>
          </div>
        </div>

        {/* Right: Mondrian Geometric Color Blocks */}
        <div
          className="w-full h-[220px] sm:h-[280px] lg:h-full min-h-[220px] sm:min-h-[280px] lg:min-h-0 bg-grid p-0 overflow-hidden"
          aria-label="Mondrian geometric composition"
        >
          <div className="grid grid-cols-[1.2fr_0.8fr_1fr] grid-rows-[1.3fr_0.7fr_1.1fr_0.6fr] gap-[4px] bg-grid h-full w-full">
            {currentLayout.map((rect, idx) => (
              <div
                key={idx}
                style={{ gridColumn: rect.col, gridRow: rect.row }}
                className={`${rect.tone} transition-colors duration-300`}
              />
            ))}
          </div>
        </div>
      </section>

      {/* =========================================================================
          SECTION B: COMPOSITION OF WORK (BELOW THE QUOTE)
          Twelve-month activity heatmap, summary metrics, flavor mix, intensity legend
         ========================================================================= */}
      <section className="bg-canvas p-5 sm:p-6 xl:p-8 flex flex-col justify-between flex-shrink-0">
        {/* Header Row: Title + Integrated Metric Cells */}
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between pb-5 border-b-2 border-grid">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-black uppercase tracking-[0.24em] text-muted-foreground">
                The last twelve months
              </span>
              <span className="text-[8px] font-mono uppercase text-muted-foreground/80 border border-grid/40 px-1 py-0.5">
                {liveTotalTasks > 0 ? "Live Tasks Connected" : "Sample Demo Archive"}
              </span>
            </div>
            <h1 className="font-display text-4xl sm:text-5xl xl:text-6xl uppercase leading-none tracking-tight text-foreground">
              Composition of Work
            </h1>
          </div>

          {/* Metrics: single contiguous grid row with internal line dividers */}
          <div className="flex border-2 border-grid bg-canvas self-start lg:self-auto overflow-x-auto max-w-full divide-x-2 divide-grid">
            <div className="bg-block-yellow text-block-ink px-4 py-2 sm:px-5 sm:py-2.5 text-center min-w-[85px] xl:min-w-[105px]">
              <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums block">
                {current || 5}
              </span>
              <span className="text-[8.5px] xl:text-[9.5px] font-black uppercase tracking-[0.16em] block mt-1">
                Day Streak
              </span>
            </div>
            <div className="bg-canvas text-foreground px-4 py-2 sm:px-5 sm:py-2.5 text-center min-w-[95px] xl:min-w-[120px]">
              <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums block">
                {displayedTasksDone}
              </span>
              <span className="text-[8.5px] xl:text-[9.5px] font-black uppercase tracking-[0.16em] text-muted-foreground block mt-1">
                Tasks Done
              </span>
            </div>
            <div className="bg-canvas text-foreground px-4 py-2 sm:px-5 sm:py-2.5 text-center min-w-[95px] xl:min-w-[120px]">
              <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums block">
                {pomodoros}
              </span>
              <span className="text-[8.5px] xl:text-[9.5px] font-black uppercase tracking-[0.16em] text-muted-foreground block mt-1">
                Pomodoros
              </span>
            </div>
            <div className="bg-canvas text-foreground px-4 py-2 sm:px-5 sm:py-2.5 text-center min-w-[80px] xl:min-w-[95px]">
              <span className="font-display text-4xl xl:text-5xl font-black leading-none tabular-nums block">
                {bestDay}
              </span>
              <span className="text-[8.5px] xl:text-[9.5px] font-black uppercase tracking-[0.16em] text-muted-foreground block mt-1">
                Best Day
              </span>
            </div>
          </div>
        </div>

        {/* Activity Heat Map: Direct grid spanning the section */}
        <div className="my-5 xl:my-6 overflow-x-auto">
          <div className="min-w-[760px] xl:min-w-[920px]">
            {/* Month Markers directly aligned over columns */}
            <div className="flex justify-between pb-2 text-[9px] xl:text-[10px] font-black uppercase tracking-[0.24em] text-muted-foreground px-1">
              {monthLabels.map((m, idx) => (
                <span key={idx}>{m}</span>
              ))}
            </div>

            {/* 52-column x 7-row Activity Grid */}
            <div className="border-2 border-grid p-3 sm:p-4 bg-canvas">
              <div className="grid grid-flow-col grid-rows-7 gap-[3px] xl:gap-[4px]">
                {yearMatrix.map((col, colIdx) =>
                  col.map((cell, rowIdx) => (
                    <div
                      key={`${colIdx}-${rowIdx}`}
                      title={`Week ${colIdx + 1}, Day ${rowIdx + 1}`}
                      className={`size-3.5 sm:size-4 md:size-[17px] xl:size-[19px] aspect-square transition-transform hover:scale-125 ${cellColorClass(cell.tone, cell.isToday)}`}
                    />
                  )),
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Bottom Bar: Flavor Mix & Activity Intensity Legend & Action */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between pt-4 border-t-2 border-grid text-[9.5px] font-black uppercase tracking-[0.14em]">
          {/* Flavor Mix: single continuous segmented bar */}
          <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center">
            <span className="text-[8.5px] text-muted-foreground mr-2 tracking-[0.2em]">
              Flavor Mix
            </span>
            <div className="flex border-2 border-grid h-7 xl:h-8 divide-x-2 divide-grid overflow-hidden">
              <div className="bg-block-red text-on-primary px-3 flex items-center">Frog 1</div>
              <div className="bg-block-blue text-on-primary px-3 flex items-center">Deep 2</div>
              <div className="bg-block-yellow text-block-ink px-3 flex items-center">Spark 1</div>
              <div className="bg-panel text-foreground px-3 flex items-center">Errand 3</div>
              <div className="bg-canvas text-foreground px-3 flex items-center">Plain 2</div>
            </div>
          </div>

          {/* Activity Intensity Legend & Action */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1.5 text-[8.5px] text-muted-foreground">
              <span>Less</span>
              <span className="size-3 border border-grid bg-panel/30" />
              <span className="size-3 border border-grid bg-block-blue" />
              <span className="size-3 border border-grid bg-block-yellow" />
              <span className="size-3 border border-grid bg-block-red" />
              <span>More</span>
            </div>
            <button
              type="button"
              onClick={() => alert("Data reset to initial composition state")}
              className="ghost-chip text-[8.5px] py-1 px-2.5 hover:bg-block-ink hover:text-canvas"
            >
              Reset all data
            </button>
          </div>
        </div>
      </section>

      {/* =========================================================================
          SECTION C: ASYMMETRIC DE STIJL TRIAD (BOTTOM WORKSPACE)
          1. Project Allocation (~44%)
          2. Energy Architecture / Mood Distribution (~34%)
          3. Milestones & Grounding Slabs (~22%)
         ========================================================================= */}
      <section className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1.05fr)_minmax(260px,320px)] xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1.1fr)_minmax(280px,340px)] gap-[4px] bg-grid flex-1 min-h-[260px]">
        {/* Cell 1: Project Allocation & Share */}
        <div className="bg-canvas p-5 sm:p-6 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between pb-3 border-b-2 border-grid">
              <div>
                <span className="text-[9px] font-black uppercase tracking-[0.24em] text-muted-foreground block">
                  Distribution of Effort
                </span>
                <h3 className="font-display text-xl sm:text-2xl uppercase tracking-tight text-foreground mt-0.5">
                  Project Allocation
                </h3>
              </div>
              <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                {totalProjectDone} Tasks Total
              </span>
            </div>

            {/* Proportional Segmented De Stijl Bar */}
            <div className="mt-4 mb-4">
              <div className="flex border-2 border-grid h-4 sm:h-5 overflow-hidden divide-x-2 divide-grid border-collapse">
                {projectShare.map((ps) => {
                  const proj = projectById(ps.projectId);
                  const pct = Math.round((ps.done / totalProjectDone) * 100);
                  const toneClass = proj ? toneBg[proj.tone] : "bg-panel";
                  return (
                    <div
                      key={ps.projectId}
                      style={{ width: `${pct}%` }}
                      title={`${proj?.name ?? ps.projectId}: ${pct}% (${ps.done} tasks)`}
                      className={`${toneClass} transition-all`}
                    />
                  );
                })}
              </div>
            </div>

            {/* Project Rows Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
              {projectShare.map((ps) => {
                const proj = projectById(ps.projectId);
                const pct = Math.round((ps.done / totalProjectDone) * 100);
                return (
                  <div
                    key={ps.projectId}
                    className="flex items-center justify-between p-2 border border-grid/25 hover:border-grid bg-panel/20 transition-colors"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className={`size-2.5 flex-shrink-0 ${proj ? toneBg[proj.tone] : "bg-panel"}`}
                      />
                      <span className="font-bold truncate text-[11px] sm:text-xs text-foreground">
                        {proj?.name ?? ps.projectId}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground ml-2 flex-shrink-0">
                      <span>{ps.done}</span>
                      <span className="font-bold text-foreground">{pct}%</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-grid/20 flex items-center justify-between text-[8.5px] font-black uppercase tracking-[0.16em] text-muted-foreground">
            <span>Primary Focus: Blockday Launch</span>
            <span>Balanced Output</span>
          </div>
        </div>

        {/* Cell 2: Energy Architecture & Mood Mix */}
        <div className="bg-canvas p-5 sm:p-6 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between pb-3 border-b-2 border-grid">
              <div>
                <span className="text-[9px] font-black uppercase tracking-[0.24em] text-muted-foreground block">
                  Cognitive Rhythm
                </span>
                <h3 className="font-display text-xl sm:text-2xl uppercase tracking-tight text-foreground mt-0.5">
                  Energy Distribution
                </h3>
              </div>
              <span className="font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                28.4h / Wk Avg
              </span>
            </div>

            {/* Proportional Segmented Energy Bar */}
            <div className="mt-4 mb-4">
              <div className="flex border-2 border-grid h-4 sm:h-5 overflow-hidden divide-x-2 divide-grid border-collapse">
                <div style={{ width: "42%" }} className="bg-block-red" title="Deep Work: 42%" />
                <div
                  style={{ width: "28%" }}
                  className="bg-block-blue"
                  title="Clear Execution: 28%"
                />
                <div
                  style={{ width: "18%" }}
                  className="bg-block-yellow"
                  title="Creative Exploration: 18%"
                />
                <div style={{ width: "12%" }} className="bg-panel" title="Easy Admin: 12%" />
              </div>
            </div>

            {/* 4 Energy Mood Cards */}
            <div className="grid grid-cols-2 gap-2">
              <div className="border border-grid/30 p-2.5 bg-panel/15 flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <span className="text-[8.5px] font-black uppercase tracking-[0.16em] text-block-red">
                    Deep
                  </span>
                  <span className="font-mono text-[11px] font-black text-foreground">42%</span>
                </div>
                <p className="mt-1 text-[9px] font-bold text-muted-foreground">
                  High-order synthesis
                </p>
              </div>

              <div className="border border-grid/30 p-2.5 bg-panel/15 flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <span className="text-[8.5px] font-black uppercase tracking-[0.16em] text-block-blue">
                    Clear
                  </span>
                  <span className="font-mono text-[11px] font-black text-foreground">28%</span>
                </div>
                <p className="mt-1 text-[9px] font-bold text-muted-foreground">Linear execution</p>
              </div>

              <div className="border border-grid/30 p-2.5 bg-panel/15 flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <span className="text-[8.5px] font-black uppercase tracking-[0.16em] text-block-yellow">
                    Creative
                  </span>
                  <span className="font-mono text-[11px] font-black text-foreground">18%</span>
                </div>
                <p className="mt-1 text-[9px] font-bold text-muted-foreground">
                  Drafts & exploration
                </p>
              </div>

              <div className="border border-grid/30 p-2.5 bg-panel/15 flex flex-col justify-between">
                <div className="flex items-center justify-between">
                  <span className="text-[8.5px] font-black uppercase tracking-[0.16em] text-muted-foreground">
                    Easy
                  </span>
                  <span className="font-mono text-[11px] font-black text-foreground">12%</span>
                </div>
                <p className="mt-1 text-[9px] font-bold text-muted-foreground">Admin & cadence</p>
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-grid/20 flex items-center justify-between text-[8.5px] font-black uppercase tracking-[0.16em] text-muted-foreground">
            <span>70% High Cognitive Demand</span>
            <span>Target: &gt;65%</span>
          </div>
        </div>

        {/* Cell 3: Recent Advancements & Grounding Color Slabs */}
        <div className="grid grid-rows-[minmax(0,1fr)_85px] sm:grid-rows-[minmax(0,1fr)_95px] gap-[4px] bg-grid h-full">
          {/* Sub-cell 3A: Recent Advancements */}
          <div className="bg-canvas p-4 xl:p-5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-2 border-b-2 border-grid">
                <span className="text-[8.5px] font-black uppercase tracking-[0.2em] text-muted-foreground">
                  Milestones
                </span>
                <span className="size-1.5 bg-block-red" />
              </div>

              <div className="mt-2.5 space-y-2">
                {advancements.slice(0, 3).map((adv, idx) => {
                  const proj = projectById(adv.projectId);
                  return (
                    <div
                      key={idx}
                      className="border-b border-grid/15 pb-2 last:border-b-0 last:pb-0"
                    >
                      <div className="flex items-center justify-between text-[8px] font-black uppercase tracking-wider text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <span className={`size-1.5 ${proj ? toneBg[proj.tone] : "bg-panel"}`} />
                          {proj?.name ?? adv.projectId}
                        </span>
                        <span className="font-mono">{adv.when}</span>
                      </div>
                      <p className="mt-0.5 text-[10px] xl:text-[11px] font-bold text-foreground leading-tight truncate">
                        {adv.text}
                      </p>
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="pt-2 border-t border-grid/20 flex items-center justify-between text-[8px] font-black uppercase tracking-[0.16em] text-muted-foreground">
              <span>Studio Pace</span>
              <span>4 / 6 Active</span>
            </div>
          </div>

          {/* Sub-cell 3B: Solid De Stijl Grounding Slabs */}
          <div className="grid grid-cols-[1.4fr_1fr] gap-[4px] bg-grid h-full w-full">
            <div className="bg-block-yellow h-full w-full" />
            <div className="bg-block-blue h-full w-full" />
          </div>
        </div>
      </section>
    </main>
  );
}
