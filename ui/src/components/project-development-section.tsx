import { useEffect, useState } from "react";
import {
  ExternalLink,
  GitBranch,
  GitCommit,
  Layers,
  RefreshCw,
  Terminal,
  Trash2,
  AlertCircle,
  FileText,
  Link as LinkIcon,
  CheckCircle,
} from "lucide-react";
import {
  gitHubApi,
  type GitHubAuthStatus,
  type GitHubRepositoryItem,
  type ProjectRepositoryContext,
} from "@/lib/github-api";
import type { BackendProject } from "@/lib/project-types";

interface ProjectDevelopmentSectionProps {
  project: BackendProject;
}

export function ProjectDevelopmentSection({ project }: ProjectDevelopmentSectionProps) {
  const [authStatus, setAuthStatus] = useState<GitHubAuthStatus | null>(null);
  const [repoContext, setRepoContext] = useState<ProjectRepositoryContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [authorizing, setAuthorizing] = useState(false);
  const [disconnectingAuth, setDisconnectingAuth] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successNotice, setSuccessNotice] = useState<string | null>(null);

  // Connection forms state
  const [repoInput, setRepoInput] = useState("");
  const [connectingRepo, setConnectingRepo] = useState(false);
  const [userRepos, setUserRepos] = useState<GitHubRepositoryItem[]>([]);
  const [showRepoDropdown, setShowRepoDropdown] = useState(false);
  const [activeTab, setActiveTab] = useState<"brief" | "issues" | "milestones" | "commits">("brief");

  useEffect(() => {
    let mounted = true;

    async function loadData() {
      setLoading(true);
      setError(null);

      // Check query parameters for OAuth redirects or results
      if (typeof window !== "undefined") {
        const params = new URLSearchParams(window.location.search);
        const ghError = params.get("github_error");
        const ghConnected = params.get("github_connected");
        const code = params.get("code");
        const state = params.get("state");

        if (ghError) {
          setError(decodeURIComponent(ghError));
          const url = new URL(window.location.href);
          url.searchParams.delete("github_error");
          window.history.replaceState({}, document.title, url.pathname + (url.search || ""));
        } else if (ghConnected) {
          setSuccessNotice("GitHub account successfully connected!");
          const url = new URL(window.location.href);
          url.searchParams.delete("github_connected");
          window.history.replaceState({}, document.title, url.pathname + (url.search || ""));
        } else if (code && state) {
          try {
            await gitHubApi.completeCallback(code, state);
            setSuccessNotice("GitHub account successfully connected!");
            const url = new URL(window.location.href);
            url.searchParams.delete("code");
            url.searchParams.delete("state");
            window.history.replaceState({}, document.title, url.pathname + (url.search || ""));
          } catch (callbackErr) {
            setError(callbackErr instanceof Error ? callbackErr.message : String(callbackErr));
          }
        }
      }

      try {
        const [auth, repo] = await Promise.all([
          gitHubApi.getAuthStatus().catch(() => ({ configured: true, connected: false, username: null })),
          gitHubApi.getProjectRepository(project.id).catch(() => null),
        ]);
        if (!mounted) return;
        setAuthStatus(auth);
        setRepoContext(repo);

        if (auth.connected && !repo) {
          void gitHubApi
            .listRepositories()
            .then((repos) => {
              if (mounted) setUserRepos(repos);
            })
            .catch(() => {});
        }
      } catch (err) {
        if (!mounted) return;
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (mounted) setLoading(false);
      }
    }

    void loadData();
    return () => {
      mounted = false;
    };
  }, [project.id]);

  async function handleAuthorizeGitHub() {
    setAuthorizing(true);
    setError(null);
    try {
      const res = await gitHubApi.getAuthorizeUrl({
        returnTo: window.location.pathname,
      });
      window.location.href = res.authorization_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setAuthorizing(false);
    }
  }

  async function handleDisconnectAccount() {
    if (!confirm("Disconnect your GitHub account? Repository links and Blockday projects will remain intact.")) {
      return;
    }
    setDisconnectingAuth(true);
    setError(null);
    try {
      await gitHubApi.disconnectAccount();
      const freshStatus = await gitHubApi.getAuthStatus().catch(() => ({
        configured: authStatus?.configured ?? true,
        connected: false,
        username: null,
      }));
      setAuthStatus(freshStatus);
      if (freshStatus.connected && !repoContext) {
        const repos = await gitHubApi.listRepositories().catch(() => []);
        setUserRepos(repos);
      } else {
        setUserRepos([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDisconnectingAuth(false);
    }
  }

  async function handleConnectRepo(repoFullName: string) {
    if (!repoFullName.trim()) return;
    setConnectingRepo(true);
    setError(null);
    try {
      const ctx = await gitHubApi.associateProjectRepository(project.id, repoFullName.trim());
      setRepoContext(ctx);
      setRepoInput("");
      setShowRepoDropdown(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConnectingRepo(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    setError(null);
    try {
      const updated = await gitHubApi.syncProjectRepository(project.id);
      setRepoContext(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSyncing(false);
    }
  }

  async function handleDisconnectRepo() {
    if (!confirm("Disconnect this repository from the project? The project will remain intact.")) {
      return;
    }
    setError(null);
    try {
      await gitHubApi.disconnectProjectRepository(project.id);
      setRepoContext(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (loading) {
    return (
      <div className="mt-6 border-2 border-grid/40 bg-panel/30 p-4">
        <p className="font-mono text-xs text-muted-foreground animate-pulse">
          Loading repository status…
        </p>
      </div>
    );
  }

  return (
    <div className="mt-6 border-2 border-grid bg-canvas p-4 text-foreground shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b-2 border-grid pb-2">
        <div className="flex items-center gap-2">
          <span className="grid size-6 place-items-center border border-grid bg-block-blue text-white">
            <GitBranch className="size-3.5" />
          </span>
          <h3 className="font-display text-sm uppercase tracking-wide">
            Development & Repository Context
          </h3>
        </div>

        {repoContext && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handleSync()}
              disabled={syncing}
              className="flex items-center gap-1 border border-grid bg-panel px-2 py-1 font-mono text-[10px] font-bold uppercase transition-colors hover:bg-muted disabled:opacity-50"
              title="Refresh repository brief and issues"
            >
              <RefreshCw className={`size-3 ${syncing ? "animate-spin" : ""}`} />
              <span>{syncing ? "Syncing…" : "Sync"}</span>
            </button>

            <button
              type="button"
              onClick={() => void handleDisconnectRepo()}
              className="flex items-center gap-1 border border-grid bg-panel px-2 py-1 font-mono text-[10px] font-bold uppercase text-block-red transition-colors hover:bg-block-red/10"
              title="Disconnect repository"
            >
              <Trash2 className="size-3" />
              <span>Unlink</span>
            </button>
          </div>
        )}
      </div>

      {error && (
        <div className="mt-3 flex items-center gap-2 border border-block-red bg-block-red/10 p-2.5 text-xs text-block-red">
          <AlertCircle className="size-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {successNotice && (
        <div className="mt-3 flex items-center gap-2 border border-emerald-500 bg-emerald-500/10 p-2.5 text-xs text-emerald-400">
          <CheckCircle className="size-4 shrink-0" />
          <span>{successNotice}</span>
        </div>
      )}

      {/* Linked Repository View */}
      {repoContext ? (
        <div className="mt-3 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2 border border-grid/50 bg-panel/40 p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs font-bold text-foreground">
                {repoContext.repository.full_name}
              </span>
              <span className="border border-grid/60 bg-muted px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                {repoContext.repository.default_branch}
              </span>
              {authStatus?.public_only && (
                <span
                  data-testid="github-public-mode-badge"
                  className="border border-block-blue bg-block-blue/15 px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase text-block-blue"
                >
                  Public repositories only
                </span>
              )}
            </div>

            <a
              href={repoContext.repository.html_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 font-mono text-[10px] text-block-blue hover:underline"
            >
              <span>View on GitHub</span>
              <ExternalLink className="size-3" />
            </a>
          </div>

          {/* Sub-tabs: Brief / Issues / Milestones / Commits */}
          <div className="flex border-b border-grid/40 gap-1 text-xs font-bold uppercase">
            <button
              type="button"
              onClick={() => setActiveTab("brief")}
              className={`border-b-2 px-3 py-1.5 transition-colors ${
                activeTab === "brief"
                  ? "border-block-blue text-block-blue"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              Project Brief
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("issues")}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-1.5 transition-colors ${
                activeTab === "issues"
                  ? "border-block-blue text-block-blue"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <span>Open Issues</span>
              <span className="rounded-full bg-panel px-1.5 py-0.2 font-mono text-[9px]">
                {repoContext.issues.length}
              </span>
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("milestones")}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-1.5 transition-colors ${
                activeTab === "milestones"
                  ? "border-block-blue text-block-blue"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <span>Milestones</span>
              <span className="rounded-full bg-panel px-1.5 py-0.2 font-mono text-[9px]">
                {repoContext.milestones.length}
              </span>
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("commits")}
              className={`flex items-center gap-1.5 border-b-2 px-3 py-1.5 transition-colors ${
                activeTab === "commits"
                  ? "border-block-blue text-block-blue"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <span>Recent Commits</span>
              <span className="rounded-full bg-panel px-1.5 py-0.2 font-mono text-[9px]">
                {repoContext.commits.length}
              </span>
            </button>
          </div>

          {/* Tab Content: Project Brief */}
          {activeTab === "brief" && (
            <div className="space-y-3 pt-1">
              {repoContext.project_brief?.purpose && (
                <div>
                  <h4 className="font-mono text-[10px] font-bold uppercase text-muted-foreground">
                    Purpose & Overview
                  </h4>
                  <p className="mt-1 text-xs leading-relaxed text-foreground">
                    {repoContext.project_brief.purpose}
                  </p>
                </div>
              )}

              {repoContext.project_brief?.architecture_or_stack &&
                repoContext.project_brief.architecture_or_stack.length > 0 && (
                  <div>
                    <h4 className="font-mono text-[10px] font-bold uppercase text-muted-foreground">
                      Tech Stack & Architecture
                    </h4>
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {repoContext.project_brief.architecture_or_stack.map((tech) => (
                        <span
                          key={tech}
                          className="border border-grid/60 bg-panel px-2 py-0.5 font-mono text-[10px] font-semibold text-foreground"
                        >
                          {tech}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

              {repoContext.project_brief?.key_capabilities &&
                repoContext.project_brief.key_capabilities.length > 0 && (
                  <div>
                    <h4 className="font-mono text-[10px] font-bold uppercase text-muted-foreground">
                      Key Capabilities
                    </h4>
                    <ul className="mt-1 space-y-1 text-xs text-foreground">
                      {repoContext.project_brief.key_capabilities.map((cap, i) => (
                        <li key={i} className="flex items-start gap-1.5">
                          <span className="text-block-blue">•</span>
                          <span>{cap}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

              {repoContext.project_brief?.setup && (
                <div>
                  <h4 className="font-mono text-[10px] font-bold uppercase text-muted-foreground">
                    Setup / Getting Started
                  </h4>
                  <pre className="mt-1 overflow-x-auto border border-grid/40 bg-panel p-2 font-mono text-[11px] text-muted-foreground">
                    {repoContext.project_brief.setup}
                  </pre>
                </div>
              )}
            </div>
          )}

          {/* Tab Content: Issues */}
          {activeTab === "issues" && (
            <div className="space-y-2 pt-1">
              {repoContext.issues.length === 0 ? (
                <p className="py-3 text-xs text-muted-foreground">No open issues found.</p>
              ) : (
                <ul className="divide-y divide-grid/20">
                  {repoContext.issues.map((issue) => (
                    <li key={issue.id} className="py-2 text-xs">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[11px] font-bold text-block-blue">
                          #{issue.number}
                        </span>
                        <span className="font-medium text-foreground">{issue.title}</span>
                      </div>
                      {issue.labels.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {issue.labels.map((lbl) => (
                            <span
                              key={lbl}
                              className="border border-grid/40 bg-panel/60 px-1.5 py-0.2 font-mono text-[9px] text-muted-foreground"
                            >
                              {lbl}
                            </span>
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Tab Content: Milestones */}
          {activeTab === "milestones" && (
            <div className="space-y-2 pt-1">
              {repoContext.milestones.length === 0 ? (
                <p className="py-3 text-xs text-muted-foreground">No active milestones.</p>
              ) : (
                <ul className="space-y-2">
                  {repoContext.milestones.map((ms) => (
                    <li key={ms.id} className="border border-grid/40 bg-panel/40 p-2.5 text-xs">
                      <div className="flex items-center justify-between font-bold">
                        <span>{ms.title}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">
                          {ms.open_issues} open / {ms.closed_issues} closed
                        </span>
                      </div>
                      {ms.description && (
                        <p className="mt-1 text-[11px] text-muted-foreground">{ms.description}</p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Tab Content: Recent Commits */}
          {activeTab === "commits" && (
            <div className="space-y-2 pt-1">
              {repoContext.commits.length === 0 ? (
                <p className="py-3 text-xs text-muted-foreground">No commits retrieved.</p>
              ) : (
                <ul className="divide-y divide-grid/20 font-mono text-xs">
                  {repoContext.commits.map((c) => (
                    <li key={c.sha} className="py-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 truncate">
                          <span className="border border-grid bg-muted px-1 py-0.5 text-[10px] font-bold text-block-blue">
                            {c.sha}
                          </span>
                          <span className="truncate text-foreground">{c.message}</span>
                        </div>
                        <span className="shrink-0 text-[10px] text-muted-foreground">{c.author}</span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      ) : (
        /* Repository Not Connected */
        <div className="mt-3 space-y-3">
          {authStatus?.connected ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 border border-grid/40 bg-panel/30 p-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="size-2 rounded-full bg-emerald-500" />
                  <span className="font-mono text-xs text-foreground">
                    {authStatus.public_only ? (
                      <>
                        Public profile: <strong className="text-block-blue">@{authStatus.username}</strong>
                      </>
                    ) : (
                      <>
                        Connected as <strong className="text-block-blue">@{authStatus.username}</strong> on GitHub
                      </>
                    )}
                  </span>
                </div>

                {authStatus.public_only ? (
                  authStatus.configured ? (
                    <button
                      type="button"
                      onClick={() => void handleAuthorizeGitHub()}
                      disabled={authorizing}
                      data-testid="github-authorize-button"
                      className="border border-grid bg-panel px-2.5 py-1 font-mono text-[10px] font-bold uppercase transition-colors hover:bg-muted disabled:opacity-50"
                      title="Authorize with GitHub to access private repositories"
                    >
                      {authorizing ? "Redirecting…" : "Authorize Private Repos"}
                    </button>
                  ) : null
                ) : (
                  <button
                    type="button"
                    onClick={() => void handleDisconnectAccount()}
                    disabled={disconnectingAuth}
                    className="border border-grid bg-panel px-2 py-1 font-mono text-[10px] font-bold uppercase text-block-red transition-colors hover:bg-block-red/10 disabled:opacity-50"
                    title="Disconnect your GitHub account"
                  >
                    {disconnectingAuth ? "Disconnecting…" : "Disconnect GitHub"}
                  </button>
                )}
              </div>

              <div>
                <p className="text-xs text-muted-foreground">
                  {authStatus.public_only
                    ? `Associate a public repository under @${authStatus.username || "user"} to enable README briefs, milestone inspection, and issue tracking.`
                    : "Associate a repository with this project to enable README briefs, milestone inspection, and issue tracking."}
                </p>

                <div className="mt-2.5 flex gap-2">
                  <div className="relative flex-1">
                    <input
                      aria-label="GitHub repository name"
                      value={repoInput}
                      onChange={(e) => {
                        setRepoInput(e.target.value);
                        setShowRepoDropdown(true);
                      }}
                      onFocus={() => setShowRepoDropdown(true)}
                      placeholder={
                        authStatus.public_only
                          ? `Search or enter repo (e.g. ${authStatus.username ? `${authStatus.username}/repo` : "owner/repo"})…`
                          : "Search or enter owner/repo (e.g. octocat/hello-world)…"
                      }
                      className="w-full border-2 border-grid bg-canvas px-3 py-1.5 text-xs outline-none"
                    />

                    {showRepoDropdown && userRepos.length > 0 && (
                      <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-48 overflow-y-auto border-2 border-grid bg-canvas shadow-md">
                        {userRepos
                          .filter((r) =>
                            repoInput ? r.full_name.toLowerCase().includes(repoInput.toLowerCase()) : true,
                          )
                          .slice(0, 8)
                          .map((r) => (
                            <button
                              key={r.id}
                              type="button"
                              onClick={() => void handleConnectRepo(r.full_name)}
                              className="flex w-full items-center justify-between px-3 py-2 text-left text-xs transition-colors hover:bg-panel"
                            >
                              <span className="font-mono font-bold text-foreground">{r.full_name}</span>
                              <span className="font-mono text-[9px] text-muted-foreground">
                                {r.default_branch}
                              </span>
                            </button>
                          ))}
                      </div>
                    )}
                  </div>

                  <button
                    type="button"
                    disabled={connectingRepo || !repoInput.trim()}
                    onClick={() => void handleConnectRepo(repoInput)}
                    className="flex items-center gap-1 border-2 border-grid bg-block-blue px-3 py-1.5 text-xs font-black uppercase text-white transition-opacity hover:opacity-90 disabled:opacity-50"
                  >
                    <LinkIcon className="size-3.5" />
                    <span>{connectingRepo ? "Connecting…" : "Connect"}</span>
                  </button>
                </div>
              </div>
            </div>
          ) : authStatus?.configured === false ? (
            <div data-testid="github-not-configured" className="border-2 border-block-yellow bg-block-yellow/10 p-3 text-xs text-foreground">
              <div className="flex items-center gap-2 font-bold uppercase text-block-yellow">
                <AlertCircle className="size-4" />
                <span>GitHub App Setup Required</span>
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground leading-relaxed">
                GitHub application credentials are not configured on the server. Please configure{" "}
                <code className="font-mono text-[11px] bg-panel px-1 py-0.5 border border-grid/40 text-foreground font-bold">
                  GITHUB_PUBLIC_USERNAME
                </code>{" "}
                for public repositories, or{" "}
                <code className="font-mono text-[11px] bg-panel px-1 py-0.5 border border-grid/40 text-foreground font-bold">
                  GITHUB_CLIENT_ID
                </code>{" "}
                and{" "}
                <code className="font-mono text-[11px] bg-panel px-1 py-0.5 border border-grid/40 text-foreground font-bold">
                  GITHUB_CLIENT_SECRET
                </code>{" "}
                in the server environment to enable repository integration.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">
                Authorize Blockday with GitHub to read repository documentation, inspect milestones, open issues, and recent commits.
              </p>

              {authStatus?.token_expired && (
                <div className="border border-block-yellow bg-block-yellow/10 p-2 text-xs text-block-yellow">
                  Your previous GitHub authorization has expired. Please re-authorize below.
                </div>
              )}

              <button
                type="button"
                onClick={() => void handleAuthorizeGitHub()}
                disabled={authorizing}
                data-testid="github-authorize-button"
                className="flex items-center gap-2 border-2 border-grid bg-block-yellow px-4 py-2 text-xs font-black uppercase text-black hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                <GitBranch className="size-4" />
                <span>{authorizing ? "Redirecting to GitHub…" : "Authorize with GitHub"}</span>
              </button>

              <p className="font-mono text-[9px] text-muted-foreground">
                Requires read-only repository permissions. Tokens are encrypted server-side with AES-128 and never exposed to the frontend.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
