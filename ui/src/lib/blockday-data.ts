// Representative sample data. Everything here is static so all pages agree.
export type Tone = "red" | "blue" | "yellow" | "neutral";
export type Mood = "Deep" | "Clear" | "Easy" | "Creative";

export type Project = { id: string; name: string; tone: Tone; parent?: string; progress: number; active?: boolean; summary: string };
export type Task = {
  id: string;
  title: string;
  projectId: string;
  /** Days from the viewed day. Negative = overdue, null = no deadline. */
  dueIn: number | null;
  estimate: number;
  mood: Mood;
  frog?: boolean;
  done: boolean;
  /** Minutes from 06:00 when scheduled in Flow; undefined = unscheduled. */
  start?: number;
};

export const projects: Project[] = [
  { id: "studio", name: "Studio", tone: "red", progress: 62, active: true, summary: "Client work and the weekly studio rhythm." },
  { id: "blockday", name: "Blockday launch", tone: "blue", parent: "studio", progress: 48, active: true, summary: "Ship the six-page planner and the launch page." },
  { id: "identity", name: "Identity refresh", tone: "red", parent: "studio", progress: 21, summary: "New marks and type for the studio." },
  { id: "personal", name: "Personal", tone: "yellow", progress: 70, active: true, summary: "Health, reading, and slow mornings." },
  { id: "home", name: "Home reset", tone: "neutral", parent: "personal", progress: 35, summary: "Declutter, plants, and the long shelf." },
  { id: "reading", name: "Reading list", tone: "yellow", parent: "personal", progress: 54, summary: "Twelve books before the new year." },
];

export const projectById = (id: string) => projects.find((p) => p.id === id);

export function makeTodayTasks(): Task[] {
  return [
    { id: "t1", title: "Resolve the onboarding flow", projectId: "blockday", dueIn: 0, estimate: 50, mood: "Deep", frog: true, done: false, start: 180 },
    { id: "t2", title: "Send invoice to Halden & Co.", projectId: "studio", dueIn: -2, estimate: 15, mood: "Easy", done: false, start: 120 },
    { id: "t3", title: "Write launch page headline", projectId: "blockday", dueIn: 2, estimate: 25, mood: "Creative", done: false, start: 300 },
    { id: "t4", title: "Sketch three logo directions", projectId: "identity", dueIn: 5, estimate: 45, mood: "Creative", done: false },
    { id: "t5", title: "Water the big plants", projectId: "home", dueIn: null, estimate: 10, mood: "Easy", done: false, start: 600 },
    { id: "t6", title: "Read two chapters", projectId: "reading", dueIn: null, estimate: 30, mood: "Clear", done: false },
    { id: "t7", title: "Review interaction notes", projectId: "studio", dueIn: 0, estimate: 25, mood: "Clear", done: true, start: 60 },
    { id: "t8", title: "Morning pages", projectId: "personal", dueIn: null, estimate: 20, mood: "Easy", done: true, start: 0 },
    { id: "t9", title: "Reply to Sam about Friday", projectId: "studio", dueIn: 0, estimate: 10, mood: "Easy", done: true },
  ];
}

export const historicalDays: Record<number, Task[]> = {
  [-1]: [
    { id: "h1", title: "Draft onboarding copy", projectId: "blockday", dueIn: 1, estimate: 40, mood: "Creative", done: true },
    { id: "h2", title: "Studio critique prep", projectId: "studio", dueIn: 0, estimate: 30, mood: "Clear", done: true },
    { id: "h3", title: "Send invoice to Halden & Co.", projectId: "studio", dueIn: -1, estimate: 15, mood: "Easy", done: false },
    { id: "h4", title: "Evening run", projectId: "personal", dueIn: null, estimate: 35, mood: "Easy", done: true },
  ],
  [-2]: [
    { id: "h5", title: "Map the six page layouts", projectId: "blockday", dueIn: 0, estimate: 60, mood: "Deep", frog: true, done: true },
    { id: "h6", title: "Order shelf brackets", projectId: "home", dueIn: 3, estimate: 10, mood: "Easy", done: true },
    { id: "h7", title: "Read two chapters", projectId: "reading", dueIn: null, estimate: 30, mood: "Clear", done: false },
  ],
  [-3]: [
    { id: "h8", title: "Weekly studio update", projectId: "studio", dueIn: 0, estimate: 15, mood: "Easy", done: true },
    { id: "h9", title: "Moodboard for identity refresh", projectId: "identity", dueIn: 4, estimate: 45, mood: "Creative", done: true },
  ],
};

export type DeadlineKind = "overdue" | "today" | "upcoming" | "none";
export function deadline(dueIn: number | null): { kind: DeadlineKind; label: string } {
  if (dueIn === null) return { kind: "none", label: "No deadline" };
  if (dueIn < 0) return { kind: "overdue", label: `${Math.abs(dueIn)} ${Math.abs(dueIn) === 1 ? "day" : "days"} overdue` };
  if (dueIn === 0) return { kind: "today", label: "Due today" };
  return { kind: "upcoming", label: `${dueIn} ${dueIn === 1 ? "day" : "days"} left` };
}

// Stats: 35 days of completions (oldest first). Deterministic.
export const dailyCompletions = [3, 5, 0, 4, 6, 2, 1, 4, 7, 5, 3, 0, 2, 5, 6, 4, 8, 3, 0, 1, 5, 6, 7, 4, 2, 3, 6, 5, 4, 7, 8, 6, 3, 5, 3];
export const focusMinutesByDay = dailyCompletions.map((n) => n * 22 + (n ? 15 : 0));
export const projectShare: { projectId: string; done: number }[] = [
  { projectId: "blockday", done: 46 },
  { projectId: "studio", done: 38 },
  { projectId: "personal", done: 24 },
  { projectId: "identity", done: 14 },
  { projectId: "home", done: 12 },
  { projectId: "reading", done: 8 },
];

export function streaks(values: number[]) {
  let best = 0, run = 0;
  for (const v of values) { run = v > 0 ? run + 1 : 0; best = Math.max(best, run); }
  let current = 0;
  for (let i = values.length - 1; i >= 0 && (values[i] ?? 0) > 0; i--) current++;
  return { current, best };
}

export const advancements = [
  { projectId: "blockday", text: "Six page layouts approved", when: "Today" },
  { projectId: "studio", text: "Halden contract signed", when: "Yesterday" },
  { projectId: "reading", text: "Finished “The Shape of Design”", when: "Wed" },
  { projectId: "identity", text: "Moodboard shared with team", when: "Tue" },
];

export const workspace: Record<string, { ideas: string[]; notes: string[]; drafts: Tone[] }> = {
  blockday: { ideas: ["Grid that reshuffles on completion", "Weekly poster export", "Energy arc sorting"], notes: ["Keep deadline colour separate from project colour.", "Launch page should feel like a painting, not a dashboard."], drafts: ["blue", "red", "yellow", "neutral"] },
  studio: { ideas: ["Friday critique format", "Shared reading shelf"], notes: ["Invoice terms move to 14 days in November."], drafts: ["red", "neutral", "blue"] },
  personal: { ideas: ["Sunday slow-morning ritual", "Walk-and-think calls"], notes: ["Sleep before 23:30 on weekdays."], drafts: ["yellow", "neutral"] },
  identity: { ideas: ["Square monogram", "Heavy grotesk wordmark"], notes: ["Avoid gradients entirely."], drafts: ["red", "yellow", "blue"] },
  home: { ideas: ["Long shelf as gallery wall"], notes: ["Brackets arrive Thursday."], drafts: ["neutral", "yellow"] },
  reading: { ideas: ["One essay a week"], notes: ["Next: Josef Albers, Interaction of Color."], drafts: ["yellow"] },
};

export type DiaryEntry = {
  id: string;
  day: string;
  time: string;
  kind: "Voice capture" | "Conversation";
  title: string;
  duration: string;
  transcript: string;
  updates: string[];
  decisions: string[];
  tasks: string[];
};

export const diary: DiaryEntry[] = [
  { id: "d1", day: "Today", time: "08:12", kind: "Voice capture", title: "Morning walk thoughts", duration: "3:41", transcript: "Okay so the onboarding thing — I think the problem is we ask for too much up front. What if the first screen is just one task…", updates: ["Blockday: onboarding reduced to one question"], decisions: ["First run asks for a single task only"], tasks: ["Prototype one-question onboarding", "Tell Sam about the change"] },
  { id: "d2", day: "Today", time: "11:30", kind: "Conversation", title: "Call with Sam — launch timing", duration: "18:05", transcript: "Sam: if we slip the launch a week we get the press slot. Me: fine, as long as the headline is locked by Thursday…", updates: ["Blockday launch moves one week later"], decisions: ["Headline locked by Thursday", "Press slot accepted"], tasks: ["Write launch page headline"] },
  { id: "d3", day: "Yesterday", time: "19:02", kind: "Voice capture", title: "Identity refresh idea", duration: "1:12", transcript: "Square monogram. Heavy grotesk. No gradients, ever. Maybe the red is a bit warmer than usual…", updates: ["Identity refresh: direction narrowed"], decisions: ["No gradients in the system"], tasks: ["Sketch three logo directions"] },
  { id: "d4", day: "Wed, Oct 14", time: "21:40", kind: "Conversation", title: "Weekly review with myself", duration: "9:30", transcript: "Reading is going well, home reset is slipping. Plants keep getting forgotten, so make it a recurring block…", updates: ["Reading list: 7 of 12 books", "Home reset: behind schedule"], decisions: ["Plants become a weekly block"], tasks: ["Water the big plants", "Order shelf brackets"] },
];
