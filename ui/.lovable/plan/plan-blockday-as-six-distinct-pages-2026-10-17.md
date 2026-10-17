# Plan: Blockday as six distinct pages

## Goal
Grow Blockday from one screen into six pages, each with its own Mondrian composition: Today, Flow, Projects, Focus, Stats, and Diary. This phase covers the look and light interactions only, using realistic sample data.

## Shared shell
- Keep the top bar with the logo, six page tabs, search, date navigation, and theme toggle. No avatar, no extra menu items.
- Every page is built from thick black-ruled rectangles laid out unevenly, using the existing colors, type, and motion.
- Built for laptops first, but sections stretch and stack instead of breaking at one fixed width.
- Reduced-motion settings turn off the sliding and rearranging.

## Pages
1. **Today**
   - Task list with deadline marks: red = overdue, yellow = due today, blue = upcoming, warm off-white = no deadline.
   - Small labels such as `OVERDUE`, `DUE TODAY`, `2 DAYS LEFT`, `NO DEADLINE`.
   - Deadline colors appear as a separate edge strip, so they don't get confused with project colors (shown as a small square).
   - A `DONE TODAY` section with greyed-out, crossed-out tasks and a `Move to completed archive` button that only hides them for now.
   - Previous/next day arrows switch between a few sample past days.
2. **Flow**
   - A vertical timeline of the day with hour marks, task blocks sized by how long they take, duration labels, and visible free gaps.
   - Unscheduled tasks sit inside the timeline as dashed "floating" blocks, not in a separate panel. Display only.
3. **Projects**
   - A nested project list on the left.
   - Up to three featured active projects shown as large colored blocks.
   - A "recent progress" strip.
   - A workspace for the selected project with ideas, notes, and draft-image tiles.
   - Clicking a project changes the workspace.
4. **Focus**
   - A large timer with the selected task, a Focus/Break switch, and start, pause, reset, and skip.
   - A row of squares for the day's sessions.
   - A working local timer.
5. **Stats**
   - Activity heat map, completion totals, current and best streak, focus hours, and a per-project breakdown made of proportional Mondrian blocks.
   - All numbers come from one shared sample dataset so they agree with each other.
6. **Diary**
   - A dated timeline of sample voice notes and conversation summaries, with times and transcript previews.
   - Each entry expands to show project updates, decisions, and extracted tasks, each with its own visual style.
   - No recording, transcription, or AI.

## Not in this phase
- Seven-day retention, carrying unfinished tasks to the next day, real archiving, and real task history.
- Real calendar or voice features.

## Order
1. Shared shell, navigation, and all six page layouts.
2. Interactions: switching pages, completing and archiving tasks, changing days, selecting projects, expanding diary entries, timer controls.
3. Polish, then click through the main flows in the preview at laptop size.

## Technical details
- Use one route per page (`/`, `/flow`, `/projects`, `/focus`, `/stats`, `/diary`), each with its own page title and description. The shared shell lives in the root layout.
- Sample data lives in one module so the stats and the pages match. Task state stays in localStorage, per the project's local-only rule.
- Shared building blocks: GridPanel, DeadlineStrip, TaskRow, SectionLabel, StatBlock, HeatMap, and a timer hook. The current large app component is broken into these pieces.
- Deadline colors use their own design tokens, separate from the project color tokens.
- Animations use view transitions plus a `prefers-reduced-motion` guard.
- Check each flow with Playwright at 1334px wide and at a narrower width.
