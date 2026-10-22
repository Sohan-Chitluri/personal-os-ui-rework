/**
 * Canonical task types and adapters conforming to the Personal OS API contract.
 * Authority: API_CONTRACT.md and src/personal_os/api/schemas.py
 */

export type Mood = "Deep" | "Clear" | "Easy" | "Creative";
export type Tone = "red" | "blue" | "yellow" | "neutral";
export type EntitySyncStatus = "pending" | "syncing" | "synced" | "conflict" | "failed";

/**
 * Backend Task representation from GET/POST/PATCH /api/v1/tasks
 */
export interface BackendTask {
  id: string;
  owner_id: string;
  project_id: string | null;
  capture_id: string | null;
  title: string;
  description: string | null;
  status: "open" | "done" | string;
  priority: number | null;
  due_date: string | null;
  soft_deadline?: string | null;
  hard_deadline?: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface TaskListResponse {
  items: BackendTask[];
  total: number;
}

export interface TaskCreatePayload {
  title: string;
  project_id?: string | null | undefined;
  description?: string | null | undefined;
  priority?: number | null | undefined;
  due_date?: string | null | undefined;
  soft_deadline?: string | null | undefined;
  hard_deadline?: string | null | undefined;
}

export interface TaskUpdatePayload {
  title?: string | undefined;
  project_id?: string | null | undefined;
  description?: string | null | undefined;
  status?: string | undefined;
  priority?: number | null | undefined;
  due_date?: string | null | undefined;
  soft_deadline?: string | null | undefined;
  hard_deadline?: string | null | undefined;
}

/**
 * RFC 7807 Problem Details representation for API errors
 */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail?: string | null | undefined;
  instance?: string | null | undefined;
  extensions?:
    | {
        current_revision?: number | undefined;
        server_state?: Record<string, unknown> | undefined;
        [key: string]: unknown;
      }
    | undefined;
}

/**
 * Unified UI Task model bridging backend authority with frontend presentation.
 */
export interface Task {
  id: string;
  title: string;
  projectId: string;
  /** Days from the viewed day. Negative = overdue, null = no deadline. (Soft deadline countdown if present, else hard deadline). */
  dueIn: number | null;
  softDueIn?: number | null | undefined;
  hardDueIn?: number | null | undefined;
  /** Estimated duration in minutes. */
  estimate: number;
  mood: Mood;
  frog?: boolean | undefined;
  done: boolean;
  /** Minutes from 06:00 when scheduled in Flow; undefined = unscheduled. */
  start?: number | undefined;
  /** Optimistic concurrency revision from backend. */
  revision: number;
  description?: string | null | undefined;
  dueDate?: string | null | undefined;
  softDeadline?: string | null | undefined;
  hardDeadline?: string | null | undefined;
  priority?: number | null | undefined;
  ownerId?: string | undefined;
  createdAt?: string | undefined;
  updatedAt?: string | undefined;
  syncStatus?: EntitySyncStatus | undefined;
  conflictId?: string | null | undefined;
}

export interface ParsedDescription {
  userDescription: string | null;
  mood?: Mood | undefined;
  estimate?: number | undefined;
}

/**
 * Parse metadata tags ([mood:...], [estimate:...]) from raw description
 * and separate the clean user-facing description.
 */
export function parseDescriptionMetadata(
  rawDescription: string | null | undefined,
): ParsedDescription {
  if (!rawDescription) {
    return { userDescription: null };
  }

  let mood: Mood | undefined;
  let estimate: number | undefined;

  // Extract last mood tag if multiple exist
  const moodMatches = [...rawDescription.matchAll(/\[mood:(Deep|Clear|Easy|Creative)\]/g)];
  const lastMoodMatch = moodMatches[moodMatches.length - 1];
  if (lastMoodMatch?.[1]) {
    mood = lastMoodMatch[1] as Mood;
  }

  // Extract last estimate tag if multiple exist
  const estMatches = [...rawDescription.matchAll(/\[estimate:(\d+)\]/g)];
  const lastEstMatch = estMatches[estMatches.length - 1];
  if (lastEstMatch?.[1]) {
    estimate = parseInt(lastEstMatch[1], 10);
  }

  // Remove internal tags and collapse spaces
  const cleaned = rawDescription
    .replace(/\[mood:(?:Deep|Clear|Easy|Creative)\]/g, "")
    .replace(/\[estimate:\d+\]/g, "")
    .replace(/[ \t]+/g, " ")
    .trim();

  return {
    userDescription: cleaned.length > 0 ? cleaned : null,
    mood,
    estimate,
  };
}

/**
 * Format user description and metadata tags for backend storage.
 * Replaces any existing metadata tags in userDescription rather than appending duplicates.
 */
export function formatBackendDescription(
  userDescription: string | null | undefined,
  mood?: Mood | null | undefined,
  estimate?: number | null | undefined,
): string | null {
  const cleaned = userDescription
    ? userDescription
        .replace(/\[mood:(?:Deep|Clear|Easy|Creative)\]/g, "")
        .replace(/\[estimate:\d+\]/g, "")
        .replace(/[ \t]+/g, " ")
        .trim()
    : "";

  const parts: string[] = [];
  if (cleaned) {
    parts.push(cleaned);
  }
  if (mood) {
    parts.push(`[mood:${mood}]`);
  }
  if (estimate !== undefined && estimate !== null) {
    parts.push(`[estimate:${estimate}]`);
  }

  return parts.length > 0 ? parts.join(" ") : null;
}

/**
 * Calculate difference in whole days between an ISO date string and today.
 */
export function calcDueInDays(isoString: string | null | undefined): number | null {
  if (!isoString) return null;
  const parsed = new Date(isoString);
  if (isNaN(parsed.getTime())) return null;
  const dueTime = new Date(parsed).setHours(0, 0, 0, 0);
  const nowTime = new Date().setHours(0, 0, 0, 0);
  return Math.round((dueTime - nowTime) / (1000 * 60 * 60 * 24));
}

/**
 * Convert backend task response to canonical UI Task.
 */
export function backendToTask(bt: BackendTask, fallback?: Partial<Task>): Task {
  const softDeadline =
    bt.soft_deadline ?? bt.due_date ?? fallback?.softDeadline ?? fallback?.dueDate ?? null;
  const hardDeadline = bt.hard_deadline ?? fallback?.hardDeadline ?? null;

  const softDueIn = calcDueInDays(softDeadline);
  const hardDueIn = calcDueInDays(hardDeadline);
  // Default countdown: soft deadline if present, else hard deadline
  const dueIn = softDueIn ?? hardDueIn ?? fallback?.dueIn ?? null;

  // Parse metadata from description if serialized or fallback
  const parsed = parseDescriptionMetadata(bt.description);
  const mood: Mood = parsed.mood ?? fallback?.mood ?? "Clear";
  const estimate = parsed.estimate ?? fallback?.estimate ?? 15;
  const description = parsed.userDescription ?? fallback?.description ?? null;

  return {
    id: bt.id,
    title: bt.title,
    projectId: bt.project_id || fallback?.projectId || "unassigned",
    dueIn,
    softDueIn,
    hardDueIn,
    estimate,
    mood,
    frog: bt.priority === 1 || Boolean(fallback?.frog),
    done: bt.status === "done",
    start: fallback?.start,
    revision: bt.revision ?? 1,
    description,
    dueDate: softDeadline,
    softDeadline,
    hardDeadline,
    priority: bt.priority,
    ownerId: bt.owner_id,
    createdAt: bt.created_at,
    updatedAt: bt.updated_at,
  };
}

/**
 * Convert UI Task fields to backend TaskCreate payload.
 */
export function taskToCreatePayload(task: {
  title: string;
  projectId?: string;
  estimate?: number;
  mood?: Mood;
  frog?: boolean;
  dueDate?: string | null;
  softDeadline?: string | null;
  hardDeadline?: string | null;
  description?: string | null;
}): TaskCreatePayload {
  const soft = task.softDeadline ?? task.dueDate ?? null;
  return {
    title: task.title,
    project_id: task.projectId && task.projectId !== "unassigned" ? task.projectId : null,
    description: formatBackendDescription(task.description, task.mood, task.estimate),
    priority: task.frog ? 1 : 3,
    due_date: soft,
    soft_deadline: soft,
    hard_deadline: task.hardDeadline ?? null,
  };
}

/**
 * Convert UI Task update to backend TaskUpdate payload.
 * When updating mood or estimate alone, preserves existing user-authored description and other metadata.
 * Omits description if neither description nor metadata was changed (PATCH omission semantics).
 */
export function taskToUpdatePayload(
  updates: Partial<Task>,
  currentTask?: Task | undefined,
): TaskUpdatePayload {
  const payload: TaskUpdatePayload = {};

  if (updates.title !== undefined) payload.title = updates.title;
  if (updates.projectId !== undefined) {
    payload.project_id =
      updates.projectId && updates.projectId !== "unassigned" ? updates.projectId : null;
  }
  if (updates.done !== undefined) {
    payload.status = updates.done ? "done" : "open";
  }
  if (updates.frog !== undefined) {
    payload.priority = updates.frog ? 1 : 3;
  }
  if (updates.priority !== undefined) {
    payload.priority = updates.priority;
  }
  if (updates.softDeadline !== undefined) {
    payload.soft_deadline = updates.softDeadline;
    payload.due_date = updates.softDeadline;
  } else if (updates.dueDate !== undefined) {
    payload.due_date = updates.dueDate;
    payload.soft_deadline = updates.dueDate;
  }
  if (updates.hardDeadline !== undefined) {
    payload.hard_deadline = updates.hardDeadline;
  }

  const hasDesc = updates.description !== undefined;
  const hasMood = updates.mood !== undefined;
  const hasEstimate = updates.estimate !== undefined;

  if (hasDesc || hasMood || hasEstimate) {
    const effectiveDesc = hasDesc ? updates.description : currentTask?.description;
    const effectiveMood = hasMood ? updates.mood : currentTask?.mood;
    const effectiveEst = hasEstimate ? updates.estimate : currentTask?.estimate;

    payload.description = formatBackendDescription(effectiveDesc, effectiveMood, effectiveEst);
  }

  return payload;
}
