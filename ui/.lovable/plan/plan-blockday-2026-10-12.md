# Plan: Blockday

## Product direction
Build **Blockday**, a laptop-first personal daily planner inspired by Mondrian’s grid paintings: thick black rules, off-white canvas, primary red/blue/yellow blocks, and disciplined modernist typography. The experience will stay minimalist while using energetic layout motion and rearranging squares to make progress feel alive.

## Core experience
- Replace the blank home screen with a dense desktop workspace: project rail, daily task canvas, and a focused utility column.
- Organize tasks beneath nested projects/folders, with expandable checklists and drag-and-drop reordering.
- Provide quick task creation with title, due date, recurrence, time estimate, mood/energy, project, and an optional “Eat the frog” priority.
- Arrange the day as an effort arc: demanding “frog” work first, then a visual progression toward lighter, lower-energy tasks.
- Include top-level search plus compact view/filter controls for Today, Upcoming, Projects, mood, and completion state.
- Add a built-in Pomodoro timer tied to the selected task, with start, pause, reset, and visible session progress.
- Add a square completion heat map that reflects recent daily task completion.
- Show a clearly labeled Google Calendar sync mockup with connected/disconnected and upcoming-event states, without requiring an account or key.

## Visual and motion system
- Create matching light and dark Mondrian themes using semantic color tokens throughout.
- Use a distinctive geometric sans-serif pairing with tight, clean hierarchy and restrained labels.
- Build the layout from asymmetric rectangular regions separated by strong grid lines; avoid rounded, generic dashboard cards.
- Use generous motion for task sorting, completion, filter changes, panel transitions, timer progress, and grid rearrangement, while honoring reduced-motion settings.
- Optimize exclusively for laptop/desktop organization, with a clear minimum-width experience rather than a mobile redesign.

## Local behavior
- Keep all task, project, preference, timer, theme, and completion data on the device with browser storage.
- Seed a polished personal demo day so the full workflow and visual rhythm are visible immediately.
- Make all primary controls functional: creating, editing, completing, deleting, filtering, searching, nesting, selecting moods, changing theme, running the timer, and reordering tasks.
- Use lightweight client-side parsing for quick-entry phrases such as “tomorrow”, durations, and simple recurrence; no AI or external API.

## Technical details
- Keep the existing React/TanStack foundation and implement the product at `/`.
- Use focused React components and a small local persistence layer; add a lightweight drag-and-drop library only if the native interaction cannot provide polished reordering.
- Define all palette, typography, border, and shadow roles in the global design system using Tailwind v4 semantic tokens.
- Add app-specific page metadata and font links in the existing route head configuration.
- Verify the finished central flow in the running preview at a laptop viewport: add a task, reorder it, complete it, filter/search, switch theme, and run the Pomodoro timer.
