import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectDevelopmentSection } from "@/components/project-development-section";
import { gitHubApi } from "@/lib/github-api";
import type { BackendProject } from "@/lib/project-types";

describe("ProjectDevelopmentSection — Browser-based GitHub OAuth Flow", () => {
  const mockProject: BackendProject = {
    id: "proj-101",
    name: "FPGA Math Core",
    description: "CORDIC hardware accelerator",
    category: "personal",
    status: "active",
    created_at: "2026-10-20T10:00:00Z",
    updated_at: "2026-10-20T10:00:00Z",
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    delete (window as unknown as { location: unknown }).location;
    window.location = {
      href: "http://localhost:5173/projects",
      pathname: "/projects",
      search: "",
      origin: "http://localhost:5173",
    } as unknown as Location;
  });

  it("shows setup message when GitHub credentials are not configured on the server", async () => {
    vi.spyOn(gitHubApi, "getAuthStatus").mockResolvedValue({
      configured: false,
      connected: false,
      username: null,
    });
    vi.spyOn(gitHubApi, "getProjectRepository").mockResolvedValue(null);

    render(<ProjectDevelopmentSection project={mockProject} />);

    await waitFor(() => {
      expect(screen.getByTestId("github-not-configured")).toBeInTheDocument();
    });

    expect(screen.getByText(/GitHub App Setup Required/i)).toBeInTheDocument();
    expect(screen.getByText(/GITHUB_CLIENT_ID/i)).toBeInTheDocument();
    expect(screen.getByText(/GITHUB_CLIENT_SECRET/i)).toBeInTheDocument();

    // Insecure token paste form must NEVER be shown
    expect(screen.queryByPlaceholderText(/Paste GitHub Personal Access Token/i)).toBeNull();
    expect(screen.queryByTestId("github-authorize-button")).toBeNull();
  });

  it("renders browser-based authorization button when configured but not connected", async () => {
    vi.spyOn(gitHubApi, "getAuthStatus").mockResolvedValue({
      configured: true,
      connected: false,
      username: null,
    });
    vi.spyOn(gitHubApi, "getProjectRepository").mockResolvedValue(null);
    const authUrlSpy = vi.spyOn(gitHubApi, "getAuthorizeUrl").mockResolvedValue({
      authorization_url: "https://github.com/login/oauth/authorize?client_id=gh-app-123&state=xyz",
      state: "xyz",
    });

    render(<ProjectDevelopmentSection project={mockProject} />);

    await waitFor(() => {
      expect(screen.getByTestId("github-authorize-button")).toBeInTheDocument();
    });

    // Ensure PAT input is NOT present
    expect(screen.queryByPlaceholderText(/Paste GitHub Personal Access Token/i)).toBeNull();

    const authBtn = screen.getByTestId("github-authorize-button");
    expect(authBtn).toHaveTextContent(/Authorize with GitHub/i);

    fireEvent.click(authBtn);

    await waitFor(() => {
      expect(authUrlSpy).toHaveBeenCalledWith({ returnTo: "/projects" });
      expect(window.location.href).toBe(
        "https://github.com/login/oauth/authorize?client_id=gh-app-123&state=xyz",
      );
    });
  });

  it("shows connected GitHub username and allows disconnecting", async () => {
    vi.spyOn(gitHubApi, "getAuthStatus")
      .mockResolvedValueOnce({
        configured: true,
        connected: true,
        username: "octocat",
        scopes: ["read:user", "repo"],
      })
      .mockResolvedValueOnce({
        configured: true,
        connected: false,
        username: null,
      });
    vi.spyOn(gitHubApi, "getProjectRepository").mockResolvedValue(null);
    vi.spyOn(gitHubApi, "listRepositories").mockResolvedValue([]);
    const disconnectSpy = vi.spyOn(gitHubApi, "disconnectAccount").mockResolvedValue({ connected: false });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ProjectDevelopmentSection project={mockProject} />);

    await waitFor(() => {
      expect(screen.getByText(/@octocat/i)).toBeInTheDocument();
    });

    const disconnectBtn = screen.getByRole("button", { name: /Disconnect GitHub/i });
    expect(disconnectBtn).toBeInTheDocument();

    fireEvent.click(disconnectBtn);

    await waitFor(() => {
      expect(disconnectSpy).toHaveBeenCalled();
      expect(screen.getByTestId("github-authorize-button")).toBeInTheDocument();
    });
  });

  it("allows searching repositories and associating with Blockday project", async () => {
    vi.spyOn(gitHubApi, "getAuthStatus").mockResolvedValue({
      configured: true,
      connected: true,
      username: "octocat",
    });
    vi.spyOn(gitHubApi, "getProjectRepository").mockResolvedValue(null);
    vi.spyOn(gitHubApi, "listRepositories").mockResolvedValue([
      {
        id: "repo-1",
        name: "cordic-accelerator",
        full_name: "octocat/cordic-accelerator",
        owner: "octocat",
        description: "CORDIC math pipeline",
        html_url: "https://github.com/octocat/cordic-accelerator",
        default_branch: "main",
        private: false,
      },
    ]);
    const assocSpy = vi.spyOn(gitHubApi, "associateProjectRepository").mockResolvedValue({
      repository: {
        id: "repo-1",
        full_name: "octocat/cordic-accelerator",
        name: "cordic-accelerator",
        owner: "octocat",
        html_url: "https://github.com/octocat/cordic-accelerator",
        default_branch: "main",
      },
      project_brief: {
        purpose: "CORDIC accelerator hardware pipeline in SystemVerilog.",
        architecture_or_stack: ["Verilog", "ASIC"],
        key_capabilities: ["16-stage pipeline"],
      },
      milestones: [],
      issues: [],
      commits: [],
    });

    render(<ProjectDevelopmentSection project={mockProject} />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Search or enter owner\/repo/i)).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText(/Search or enter owner\/repo/i);
    fireEvent.focus(input);

    await waitFor(() => {
      expect(screen.getByText("octocat/cordic-accelerator")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("octocat/cordic-accelerator"));

    await waitFor(() => {
      expect(assocSpy).toHaveBeenCalledWith("proj-101", "octocat/cordic-accelerator");
      expect(screen.getByText(/CORDIC accelerator hardware pipeline/i)).toBeInTheDocument();
    });
  });

  it("handles OAuth callback params code and state in URL automatically on load", async () => {
    window.location.search = "?code=test-auth-code&state=valid-csrf-state";
    const replaceStateSpy = vi.spyOn(window.history, "replaceState");

    const callbackSpy = vi.spyOn(gitHubApi, "completeCallback").mockResolvedValue({
      configured: true,
      connected: true,
      username: "octocat",
    });
    vi.spyOn(gitHubApi, "getAuthStatus").mockResolvedValue({
      configured: true,
      connected: true,
      username: "octocat",
    });
    vi.spyOn(gitHubApi, "getProjectRepository").mockResolvedValue(null);
    vi.spyOn(gitHubApi, "listRepositories").mockResolvedValue([]);

    render(<ProjectDevelopmentSection project={mockProject} />);

    await waitFor(() => {
      expect(callbackSpy).toHaveBeenCalledWith("test-auth-code", "valid-csrf-state");
      expect(replaceStateSpy).toHaveBeenCalled();
      expect(screen.getByText(/GitHub account successfully connected!/i)).toBeInTheDocument();
    });
  });

  it("allows public repo discovery and association without OAuth credentials in public profile mode", async () => {
    vi.spyOn(gitHubApi, "getAuthStatus").mockResolvedValue({
      configured: false,
      connected: true,
      public_only: true,
      mode: "public_only",
      username: "Sohan-Chitluri",
    });
    vi.spyOn(gitHubApi, "getProjectRepository").mockResolvedValue(null);
    vi.spyOn(gitHubApi, "listRepositories").mockResolvedValue([
      {
        id: "repo-pub-1",
        name: "personal-os-ui-rework",
        full_name: "Sohan-Chitluri/personal-os-ui-rework",
        owner: "Sohan-Chitluri",
        description: "Public UI rework",
        html_url: "https://github.com/Sohan-Chitluri/personal-os-ui-rework",
        default_branch: "main",
        private: false,
      },
    ]);
    const assocSpy = vi.spyOn(gitHubApi, "associateProjectRepository").mockResolvedValue({
      repository: {
        id: "repo-pub-1",
        full_name: "Sohan-Chitluri/personal-os-ui-rework",
        name: "personal-os-ui-rework",
        owner: "Sohan-Chitluri",
        html_url: "https://github.com/Sohan-Chitluri/personal-os-ui-rework",
        default_branch: "main",
      },
      project_brief: {
        purpose: "Personal OS public UI redesign and modernization.",
        architecture_or_stack: ["React", "TailwindCSS"],
        key_capabilities: ["Public GitHub integration"],
      },
      milestones: [],
      issues: [],
      commits: [],
    });

    render(<ProjectDevelopmentSection project={mockProject} />);

    await waitFor(() => {
      expect(screen.getByText(/Public profile:/i)).toBeInTheDocument();
    });

    // Setup warning must NOT be shown
    expect(screen.queryByTestId("github-not-configured")).toBeNull();

    // Connected public profile must be indicated, but without the "Public repositories only" badge beside it
    expect(screen.getByText(/Public profile:/i)).toBeInTheDocument();
    expect(screen.getAllByText(/@Sohan-Chitluri/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId("github-public-mode-badge")).toBeNull();
    expect(screen.queryByText(/Public repositories only/i)).toBeNull();

    // Repository input and dropdown
    const input = screen.getByPlaceholderText(/Search or enter repo/i);
    expect(input).toBeInTheDocument();
    fireEvent.focus(input);

    await waitFor(() => {
      expect(screen.getByText("Sohan-Chitluri/personal-os-ui-rework")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Sohan-Chitluri/personal-os-ui-rework"));

    await waitFor(() => {
      expect(assocSpy).toHaveBeenCalledWith("proj-101", "Sohan-Chitluri/personal-os-ui-rework");
      expect(screen.getByText(/Personal OS public UI redesign/i)).toBeInTheDocument();
    });
  });

  it("renders upgrade to private authorization button when in public mode and GitHub App credentials are configured", async () => {
    vi.spyOn(gitHubApi, "getAuthStatus").mockResolvedValue({
      configured: true,
      connected: true,
      public_only: true,
      mode: "public_only",
      username: "Sohan-Chitluri",
    });
    vi.spyOn(gitHubApi, "getProjectRepository").mockResolvedValue(null);
    vi.spyOn(gitHubApi, "listRepositories").mockResolvedValue([]);

    render(<ProjectDevelopmentSection project={mockProject} />);

    await waitFor(() => {
      expect(screen.getByTestId("github-authorize-button")).toBeInTheDocument();
    });

    expect(screen.getByTestId("github-authorize-button")).toHaveTextContent(/Authorize Private Repos/i);
  });

  it("displays public repositories only badge when viewing an associated repository in public-only mode", async () => {
    vi.spyOn(gitHubApi, "getAuthStatus").mockResolvedValue({
      configured: false,
      connected: true,
      public_only: true,
      mode: "public_only",
      username: "Sohan-Chitluri",
    });
    vi.spyOn(gitHubApi, "getProjectRepository").mockResolvedValue({
      repository: {
        id: "repo-pub-1",
        full_name: "Sohan-Chitluri/personal-os-ui-rework",
        name: "personal-os-ui-rework",
        owner: "Sohan-Chitluri",
        html_url: "https://github.com/Sohan-Chitluri/personal-os-ui-rework",
        default_branch: "main",
      },
      project_brief: {
        purpose: "Personal OS public UI redesign.",
        architecture_or_stack: ["React"],
        key_capabilities: [],
      },
      milestones: [],
      issues: [],
      commits: [],
    });

    render(<ProjectDevelopmentSection project={mockProject} />);

    await waitFor(() => {
      expect(screen.getByText("Sohan-Chitluri/personal-os-ui-rework")).toBeInTheDocument();
      expect(screen.getByTestId("github-public-mode-badge")).toBeInTheDocument();
    });
  });
});
