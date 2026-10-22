import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatsPage } from "@/routes/stats";

describe("StatsPage — Neo-Brutalist Mondrian Layout", () => {
  it("renders quote composition on top with 'A NOTE TO SELF' and initial quote", () => {
    render(<StatsPage />);

    expect(screen.getByText("A NOTE TO SELF")).toBeInTheDocument();
    expect(screen.getByText("1 / 8")).toBeInTheDocument();
    expect(screen.getByText(/SIMPLICITY IS ABOUT SUBTRACTING THE OBVIOUS/i)).toBeInTheDocument();
    expect(screen.getByText(/JOHN MAEDA/i)).toBeInTheDocument();
  });

  it("navigates forward and backward through curated quotes", () => {
    render(<StatsPage />);

    const nextBtn = screen.getByLabelText("Next quote");
    const prevBtn = screen.getByLabelText("Previous quote");

    // Click next -> Quote 2
    fireEvent.click(nextBtn);
    expect(screen.getByText("2 / 8")).toBeInTheDocument();
    expect(screen.getByText(/PIET MONDRIAN/i)).toBeInTheDocument();

    // Click next -> Quote 3
    fireEvent.click(nextBtn);
    expect(screen.getByText("3 / 8")).toBeInTheDocument();
    expect(screen.getByText(/DIETER RAMS/i)).toBeInTheDocument();

    // Click prev twice -> back to Quote 1
    fireEvent.click(prevBtn);
    expect(screen.getByText("2 / 8")).toBeInTheDocument();
    fireEvent.click(prevBtn);
    expect(screen.getByText("1 / 8")).toBeInTheDocument();

    // Click prev from 1 -> loops to 8 / 8
    fireEvent.click(prevBtn);
    expect(screen.getByText("8 / 8")).toBeInTheDocument();
    expect(screen.getByText(/MICHELANGELO/i)).toBeInTheDocument();
  });

  it("renders Section B Composition of Work below with heatmap and metrics", () => {
    render(<StatsPage />);

    expect(screen.getByRole("heading", { name: /COMPOSITION OF WORK/i })).toBeInTheDocument();
    expect(screen.getByText("The last twelve months")).toBeInTheDocument();
    expect(screen.getByText("Day Streak")).toBeInTheDocument();
    expect(screen.getByText("Tasks Done")).toBeInTheDocument();
    expect(screen.getByText("Pomodoros")).toBeInTheDocument();
    expect(screen.getByText("Best Day")).toBeInTheDocument();

    // Check legend items
    expect(screen.getByText("Flavor Mix")).toBeInTheDocument();
    expect(screen.getByText("Frog 1")).toBeInTheDocument();
    expect(screen.getByText("Deep 2")).toBeInTheDocument();
    expect(screen.getByText("Spark 1")).toBeInTheDocument();
    expect(screen.getByText("Errand 3")).toBeInTheDocument();
    expect(screen.getByText("Plain 2")).toBeInTheDocument();
    expect(screen.getByText("Reset all data")).toBeInTheDocument();
  });

  it("removes entirely the timer controls and task list from Stats page", () => {
    render(<StatsPage />);

    expect(screen.queryByPlaceholderText("Add to today...")).not.toBeInTheDocument();
    expect(screen.queryByText("Sort by energy")).not.toBeInTheDocument();
    expect(screen.queryByText("Drag a task here, or press ▶ on one")).not.toBeInTheDocument();
    expect(screen.queryByText("Google Calendar")).not.toBeInTheDocument();
  });

  it("renders Section C Asymmetric De Stijl Triad with Project Allocation, Energy Distribution, and Milestones", () => {
    render(<StatsPage />);

    // Cell 1: Project Allocation
    expect(screen.getByRole("heading", { name: /Project Allocation/i })).toBeInTheDocument();
    expect(screen.getByText("Distribution of Effort")).toBeInTheDocument();
    expect(screen.getAllByText(/Blockday launch/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/142 Tasks Total/i)).toBeInTheDocument();

    // Cell 2: Energy Distribution
    expect(screen.getByRole("heading", { name: /Energy Distribution/i })).toBeInTheDocument();
    expect(screen.getByText("Cognitive Rhythm")).toBeInTheDocument();
    expect(screen.getByText("28.4h / Wk Avg")).toBeInTheDocument();

    // Cell 3: Milestones
    expect(screen.getByText("Milestones")).toBeInTheDocument();
    expect(screen.getByText("Six page layouts approved")).toBeInTheDocument();
    expect(screen.getByText("Studio Pace")).toBeInTheDocument();
  });
});
