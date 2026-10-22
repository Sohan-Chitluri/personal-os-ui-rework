import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { projectApi } from "@/lib/project-api";
import { taskApi } from "@/lib/task-api";
import { BlockdayProvider } from "@/lib/blockday-store";
import { ProjectsPage } from "@/routes/projects";

function createWrapper() {
  vi.spyOn(projectApi, "listProjects").mockResolvedValue({
    total: 2,
    items: [
      {
        id: "proj_college",
        owner_id: "user_1",
        name: "cordic",
        description: null,
        status: "active",
        revision: 1,
        created_at: "2026-10-20T00:00:00Z",
        updated_at: "2026-10-20T00:00:00Z",
      },
      {
        id: "proj_personal",
        owner_id: "user_1",
        name: "Local Dev Verification Project",
        description: null,
        status: "active",
        revision: 1,
        created_at: "2026-10-20T00:00:00Z",
        updated_at: "2026-10-20T00:00:00Z",
      },
    ],
  });

  vi.spyOn(taskApi, "listTasks").mockResolvedValue({
    total: 2,
    items: [
      {
        id: "task_cordic_1",
        owner_id: "user_1",
        project_id: "proj_college",
        capture_id: null,
        title: "Implement CORDIC pipeline",
        description: "",
        status: "open",
        priority: 1,
        due_date: null,
        revision: 1,
        created_at: "2026-10-20T00:00:00Z",
        updated_at: "2026-10-20T00:00:00Z",
      },
      {
        id: "task_dev_1",
        owner_id: "user_1",
        project_id: "proj_personal",
        capture_id: null,
        title: "Verify auth proxy",
        description: "",
        status: "open",
        priority: 2,
        due_date: null,
        revision: 1,
        created_at: "2026-10-20T00:00:00Z",
        updated_at: "2026-10-20T00:00:00Z",
      },
    ],
  });

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BlockdayProvider>{children}</BlockdayProvider>
      </QueryClientProvider>
    );
  };
}

describe("ProjectsPage Dual-Window & Left Rail Categorization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders College and Personal headers in the left navigation with categorized projects", async () => {
    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <ProjectsPage />
      </Wrapper>
    );

    const nav = await screen.findByRole("navigation", { name: "Projects" });
    expect(nav).toBeInTheDocument();

    // Verify category header labels exist in left nav
    expect(within(nav).getByText("College")).toBeInTheDocument();
    expect(within(nav).getByText("Personal")).toBeInTheDocument();

    // cordic is in College, Local Dev Verification Project is in Personal
    expect(within(nav).getByRole("button", { name: /cordic/i })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /Local Dev Verification Project/i })).toBeInTheDocument();
  });

  it("renders dual stacked windows for College and Personal simultaneously", async () => {
    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <ProjectsPage />
      </Wrapper>
    );

    // Both window headers should appear
    expect(await screen.findByText("College Project")).toBeInTheDocument();
    expect(screen.getByText("Personal Project")).toBeInTheDocument();

    // Both project tasks should appear in their respective windows
    expect(screen.getByText("Implement CORDIC pipeline")).toBeInTheDocument();
    expect(screen.getByText("Verify auth proxy")).toBeInTheDocument();

    // Both task input placeholders should exist
    expect(screen.getByPlaceholderText("Add task to cordic")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Add task to Local Dev Verification Project")).toBeInTheDocument();
  });

  it("allows adding a task to the College project and to the Personal project separately", async () => {
    const Wrapper = createWrapper();
    const createTaskSpy = vi.spyOn(taskApi, "createTask").mockImplementation(async (payload) => ({
      id: "new_task",
      owner_id: "user_1",
      project_id: payload.project_id || null,
      capture_id: null,
      title: payload.title,
      description: payload.description || "",
      status: "open",
      priority: payload.priority || 3,
      due_date: null,
      revision: 1,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }));

    render(
      <Wrapper>
        <ProjectsPage />
      </Wrapper>
    );

    const collegeInput = await screen.findByPlaceholderText("Add task to cordic");
    fireEvent.change(collegeInput, { target: { value: "Synthesize Verilog" } });
    fireEvent.submit(collegeInput.closest("form")!);

    await waitFor(() => {
      expect(createTaskSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Synthesize Verilog",
          project_id: "proj_college",
        })
      );
    });

    const personalInput = screen.getByPlaceholderText("Add task to Local Dev Verification Project");
    fireEvent.change(personalInput, { target: { value: "Fix local mock" } });
    fireEvent.submit(personalInput.closest("form")!);

    await waitFor(() => {
      expect(createTaskSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Fix local mock",
          project_id: "proj_personal",
        })
      );
    });
  });
});
