import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
import {
  DeadlineBadge,
  DeadlineStrip,
  TaskRow,
  TaskEditDialog,
} from "@/components/blockday-ui";
import {
  getTaskStripeClass,
  formatHardDeadlineLocal,
  calcDueInDays,
  type Task,
} from "@/lib/blockday-data";
import { BlockdayProvider } from "@/lib/blockday-store";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OfflineMutationBoundary } from "@/lib/offline-mutations";
import { IndexedDbOfflineOperationQueue } from "@/lib/offline-queue";

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BlockdayProvider>{ui}</BlockdayProvider>
    </QueryClientProvider>,
  );
}

describe("Two-tier Deadline Badge & Stripe Semantics", () => {
  it("shows soft deadline countdown when only soft deadline is present without flip wrapper", () => {
    const task: Task = {
      id: "t1",
      title: "Soft task only",
      projectId: "studio",
      dueIn: 3,
      softDueIn: 3,
      hardDueIn: null,
      softDeadline: "2026-11-10T12:00:00Z",
      hardDeadline: null,
      estimate: 15,
      mood: "Clear",
      done: false,
      revision: 1,
    };

    const { container } = render(<DeadlineBadge task={task} />);
    expect(screen.getByText(/3 days left/i)).toBeInTheDocument();
    // Must NOT have flip container when hard deadline is missing
    expect(container.querySelector(".has-flip")).toBeNull();
    expect(container.querySelector(".deadline-card-back")).toBeNull();
  });

  it("shows hard deadline countdown when only hard deadline is present without flip wrapper", () => {
    const task: Task = {
      id: "t2",
      title: "Hard task only",
      projectId: "studio",
      dueIn: 1,
      softDueIn: null,
      hardDueIn: 1,
      softDeadline: null,
      hardDeadline: "2026-11-05T18:00:00Z",
      estimate: 20,
      mood: "Deep",
      done: false,
      revision: 1,
    };

    const { container } = render(<DeadlineBadge task={task} />);
    expect(screen.getByText(/HARD · 1 day left/i)).toBeInTheDocument();
    expect(container.querySelector(".has-flip")).toBeNull();
    expect(container.querySelector(".deadline-card-back")).toBeNull();
  });

  it("renders 3D flip card when both soft and hard deadlines exist", () => {
    const hardIso = "2026-11-05T18:00:00Z";
    const expectedLocal = formatHardDeadlineLocal(hardIso);

    const task: Task = {
      id: "t3",
      title: "Dual deadline task",
      projectId: "studio",
      dueIn: 2,
      softDueIn: 2,
      hardDueIn: 4,
      softDeadline: "2026-11-03T12:00:00Z",
      hardDeadline: hardIso,
      estimate: 30,
      mood: "Creative",
      done: false,
      revision: 1,
    };

    const { container } = render(<DeadlineBadge task={task} />);

    // Default front face shows soft deadline countdown
    const front = container.querySelector(".deadline-card-front");
    expect(front).toHaveTextContent(/2 days left/i);

    // Back face contains hard deadline date and time in user's local timezone
    const back = container.querySelector(".deadline-card-back");
    expect(back).toHaveTextContent(`HARD: ${expectedLocal}`);

    // Wrapper has flip functionality
    const wrapper = container.querySelector(".deadline-badge-wrapper.has-flip");
    expect(wrapper).not.toBeNull();
    expect(wrapper).not.toHaveClass("is-flipped");

    // Touch tap / click toggles flipped state
    fireEvent.click(wrapper!);
    expect(wrapper).toHaveClass("is-flipped");

    // Clicking again toggles back
    fireEvent.click(wrapper!);
    expect(wrapper).not.toHaveClass("is-flipped");

    // Pointer leave resets flip state
    fireEvent.click(wrapper!);
    expect(wrapper).toHaveClass("is-flipped");
    fireEvent.pointerLeave(wrapper!);
    expect(wrapper).not.toHaveClass("is-flipped");
  });

  it("tapping the deadline badge stops propagation and does not trigger task row selection", () => {
    const onSelectSpy = vi.fn();
    const task: Task = {
      id: "t4",
      title: "Interactive row test",
      projectId: "studio",
      dueIn: 2,
      softDueIn: 2,
      hardDueIn: 5,
      softDeadline: "2026-11-03T12:00:00Z",
      hardDeadline: "2026-11-06T18:00:00Z",
      estimate: 15,
      mood: "Easy",
      done: false,
      revision: 1,
    };

    const { container } = render(
      <TaskRow task={task} onSelect={onSelectSpy} />,
    );

    const badge = container.querySelector(".deadline-badge-wrapper");
    expect(badge).not.toBeNull();

    // Tap badge
    fireEvent.click(badge!);

    // Badge toggled, but row onSelect was NOT triggered due to stopPropagation
    expect(onSelectSpy).not.toHaveBeenCalled();

    // Clicking the row body DOES trigger onSelect
    const row = container.querySelector(".task-row");
    fireEvent.click(row!);
    expect(onSelectSpy).toHaveBeenCalledTimes(1);
  });

  it("applies correct stripe colors based on deadline semantics", () => {
    // 1. Hard deadline overdue -> deadline-hard-overdue (strong red)
    expect(
      getTaskStripeClass({
        hardDeadline: "2020-01-01T00:00:00Z",
        hardDueIn: -3,
        dueIn: -3,
      }),
    ).toBe("deadline-hard-overdue");

    // 2. Hard deadline urgent/today -> deadline-hard-urgent (vibrant red)
    expect(
      getTaskStripeClass({
        hardDeadline: "2026-10-21T00:00:00Z",
        hardDueIn: 0,
        dueIn: 0,
      }),
    ).toBe("deadline-hard-urgent");

    // 3. Hard deadline upcoming -> deadline-hard-upcoming (restrained terracotta)
    expect(
      getTaskStripeClass({
        hardDeadline: "2026-11-15T00:00:00Z",
        hardDueIn: 5,
        dueIn: 5,
      }),
    ).toBe("deadline-hard-upcoming");

    // 4. Soft deadline overdue -> deadline-soft-overdue (restrained neutral slate)
    expect(
      getTaskStripeClass({
        softDeadline: "2020-01-01T00:00:00Z",
        softDueIn: -2,
        hardDeadline: null,
        dueIn: -2,
      }),
    ).toBe("deadline-soft-overdue");

    // 5. Soft deadline today -> deadline-soft-today (restrained neutral slate)
    expect(
      getTaskStripeClass({
        softDeadline: "2026-10-21T00:00:00Z",
        softDueIn: 0,
        hardDeadline: null,
        dueIn: 0,
      }),
    ).toBe("deadline-soft-today");

    // 6. Soft deadline upcoming -> deadline-soft-upcoming (restrained neutral slate)
    expect(
      getTaskStripeClass({
        softDeadline: "2026-11-20T00:00:00Z",
        softDueIn: 10,
        hardDeadline: null,
        dueIn: 10,
      }),
    ).toBe("deadline-soft-upcoming");

    // 7. No deadline -> deadline-none (neutral)
    expect(
      getTaskStripeClass({
        softDeadline: null,
        hardDeadline: null,
        dueIn: null,
      }),
    ).toBe("deadline-none");
  });

  it("TaskEditDialog supports editing both soft and hard deadlines", async () => {
    const onSaveSpy = vi.fn();
    const onCloseSpy = vi.fn();
    const task: Task = {
      id: "t_edit",
      title: "Editing deadlines",
      description: "My context",
      projectId: "studio",
      dueIn: 1,
      softDeadline: "2026-11-01T10:00:00Z",
      hardDeadline: "2026-11-05T14:30:00Z",
      estimate: 25,
      mood: "Deep",
      frog: true,
      done: false,
      revision: 1,
    };

    renderWithProviders(
      <TaskEditDialog
        open={true}
        task={task}
        onClose={onCloseSpy}
        onSave={onSaveSpy}
      />,
    );

    expect(screen.getByDisplayValue("Editing deadlines")).toBeInTheDocument();
    expect(screen.getByLabelText(/soft deadline/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/hard deadline/i)).toBeInTheDocument();

    // Submit form
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /save block/i }));
    });

    expect(onSaveSpy).toHaveBeenCalledTimes(1);
    const callArgs = onSaveSpy.mock.calls[0];
    expect(callArgs).toBeDefined();
    const saved = callArgs![0];
    expect(saved.title).toBe("Editing deadlines");
    expect(saved.softDeadline).toBeTruthy();
    expect(saved.hardDeadline).toBeTruthy();
  });

  it("regression: renders task list with all deadline permutations without calcDueInDays error", () => {
    const tasks: Task[] = [
      {
        id: "t_both",
        title: "Both deadlines task",
        projectId: "studio",
        dueIn: 3,
        softDueIn: 3,
        hardDueIn: 6,
        softDeadline: "2026-11-10T12:00:00Z",
        hardDeadline: "2026-11-13T12:00:00Z",
        estimate: 25,
        mood: "Clear",
        done: false,
        revision: 1,
      },
      {
        id: "t_soft_only",
        title: "Soft only task",
        projectId: "studio",
        dueIn: 4,
        softDueIn: 4,
        hardDueIn: null,
        softDeadline: "2026-11-11T12:00:00Z",
        hardDeadline: null,
        estimate: 15,
        mood: "Easy",
        done: false,
        revision: 1,
      },
      {
        id: "t_hard_only",
        title: "Hard only task",
        projectId: "studio",
        dueIn: 2,
        softDueIn: null,
        hardDueIn: 2,
        softDeadline: null,
        hardDeadline: "2026-11-09T18:00:00Z",
        estimate: 30,
        mood: "Deep",
        done: false,
        revision: 1,
      },
      {
        id: "t_no_deadline",
        title: "No deadline task",
        projectId: "studio",
        dueIn: null,
        softDueIn: null,
        hardDueIn: null,
        softDeadline: null,
        hardDeadline: null,
        estimate: 10,
        mood: "Clear",
        done: false,
        revision: 1,
      },
      {
        id: "t_overdue_soft",
        title: "Overdue soft task",
        projectId: "studio",
        dueIn: -3,
        softDueIn: -3,
        hardDueIn: null,
        softDeadline: "2026-10-18T12:00:00Z",
        hardDeadline: null,
        estimate: 20,
        mood: "Chaos",
        done: false,
        revision: 1,
      },
      {
        id: "t_overdue_hard",
        title: "Overdue hard task",
        projectId: "studio",
        dueIn: -1,
        softDueIn: null,
        hardDueIn: -1,
        softDeadline: null,
        hardDeadline: "2026-10-20T12:00:00Z",
        estimate: 45,
        mood: "Deep",
        done: false,
        revision: 1,
      },
      {
        id: "t_done",
        title: "Completed dual-deadline task",
        projectId: "studio",
        dueIn: 1,
        softDueIn: 1,
        hardDueIn: 3,
        softDeadline: "2026-11-02T12:00:00Z",
        hardDeadline: "2026-11-04T12:00:00Z",
        estimate: 15,
        mood: "Clear",
        done: true,
        revision: 1,
      },
      {
        id: "t_legacy_due_date",
        title: "Legacy dueDate task",
        projectId: "studio",
        dueIn: null,
        dueDate: "2026-11-15T12:00:00Z",
        softDeadline: null,
        hardDeadline: null,
        estimate: 15,
        mood: "Clear",
        done: false,
        revision: 1,
      },
    ];

    const { container } = render(
      <div className="task-list">
        {tasks.map((task) => (
          <TaskRow key={task.id} task={task} />
        ))}
      </div>,
    );

    // Verify all 8 tasks render without error
    expect(screen.getByText("Both deadlines task")).toBeInTheDocument();
    expect(screen.getByText("Soft only task")).toBeInTheDocument();
    expect(screen.getByText("Hard only task")).toBeInTheDocument();
    expect(screen.getByText("No deadline task")).toBeInTheDocument();
    expect(screen.getByText("Overdue soft task")).toBeInTheDocument();
    expect(screen.getByText("Overdue hard task")).toBeInTheDocument();
    expect(screen.getByText("Completed dual-deadline task")).toBeInTheDocument();
    expect(screen.getByText("Legacy dueDate task")).toBeInTheDocument();

    // Verify flip badge is present only for dual-deadline incomplete task
    const flipBadges = container.querySelectorAll(".deadline-badge-wrapper.has-flip");
    expect(flipBadges.length).toBe(1);

    // Verify stripe semantics
    const taskRows = container.querySelectorAll(".task-row");
    expect(taskRows.length).toBe(8);

    // Check overdue stripes
    const softOverdueRow = screen.getByText("Overdue soft task").closest(".task-row");
    expect(softOverdueRow?.querySelector(".deadline-soft-overdue")).not.toBeNull();

    const hardOverdueRow = screen.getByText("Overdue hard task").closest(".task-row");
    expect(hardOverdueRow?.querySelector(".deadline-hard-overdue")).not.toBeNull();

    // Check completed task does not render badge
    const doneRow = screen.getByText("Completed dual-deadline task").closest(".task-row");
    expect(doneRow).toHaveClass("task-done");
    expect(doneRow?.querySelector(".deadline-badge-wrapper")).toBeNull();
  });

  it("regression: rehydrates offline queue mutations with soft and hard deadlines without calcDueInDays error", async () => {
    const factory = new IDBFactory();
    const queue = new IndexedDbOfflineOperationQueue({
      database_name: "test-rehydration-deadlines",
      indexed_db_factory: factory,
    });
    const boundary = new OfflineMutationBoundary(queue);

    // Enqueue task creation with soft and hard deadlines
    const created = await boundary.enqueueTaskCreate({
      title: "Offline task with deadlines",
      estimate: 30,
      mood: "Deep",
      dueDate: "2026-11-20T12:00:00Z",
      hardDeadline: "2026-11-25T12:00:00Z",
    });

    // Enqueue task update changing deadlines
    await boundary.enqueueTaskUpdate(
      created.task,
      {
        dueDate: "2026-11-22T12:00:00Z",
        hardDeadline: "2026-11-27T12:00:00Z",
      },
    );

    // Call overlayQueueOnTasks - this executed the exact path that previously threw ReferenceError
    const rehydrated = await boundary.overlayQueueOnTasks([]);
    expect(rehydrated.length).toBe(1);
    const rehydratedTask = rehydrated[0]!;
    expect(rehydratedTask.title).toBe("Offline task with deadlines");
    expect(rehydratedTask.softDeadline).toBe("2026-11-22T12:00:00Z");
    expect(rehydratedTask.hardDeadline).toBe("2026-11-27T12:00:00Z");
    expect(typeof rehydratedTask.softDueIn).toBe("number");
    expect(typeof rehydratedTask.hardDueIn).toBe("number");
    expect(typeof rehydratedTask.dueIn).toBe("number");

    // Render the rehydrated task in TaskRow to ensure DOM rendering completes without errors
    const { container } = render(<TaskRow task={rehydratedTask} />);
    expect(screen.getByText("Offline task with deadlines")).toBeInTheDocument();
    expect(container.querySelector(".deadline-badge-wrapper.has-flip")).not.toBeNull();

    queue.close();
  });
});
