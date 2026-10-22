import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  useRouterState,
  HeadContent,
  Scripts,
  type ErrorComponentProps,
} from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";

import appCss from "../styles.css?url";
import { reportLovableError } from "../lib/lovable-error-reporting";
import { AppHeader } from "@/components/blockday-ui";
import { BlockdayProvider } from "@/lib/blockday-store";
import { AppSidebar } from "@/components/blockday-sidebar";
import { RaphaelOverlay } from "@/components/raphael-overlay";
import { SyncStatusPanel } from "@/components/sync-status-panel";
import {
  beginCognitoLogin,
  getCognitoAccessToken,
  hasCognitoEnv,
  isCognitoConfigured,
  validateCognitoConfig,
} from "@/lib/auth";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or has been moved.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Go home
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: ErrorComponentProps) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          This page didn't load
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Something went wrong on our end. You can try refreshing or head back home.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            Go home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Blockday" },
      { name: "description", content: "Compose your day in focused blocks." },
      { name: "author", content: "Blockday" },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
    links: [
      {
        rel: "stylesheet",
        href: appCss,
      },
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon" },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Jost:wght@400;500;600;700;800;900&display=swap",
      },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  if ((isCognitoConfigured() || hasCognitoEnv()) && !getCognitoAccessToken() && pathname !== "/auth/callback") {
    return <SignInScreen />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <BlockdayProvider>
        <div className="flex h-dvh max-h-dvh flex-col overflow-hidden">
          <AppHeader />
          <SyncStatusPanel />
          <div className="shell-grid">
            <AppSidebar />
            <main key={pathname} className="page-assemble min-h-0 min-w-0 flex-1 overflow-hidden">
              {/* Required: nested routes render here. Removing <Outlet /> breaks all child routes. */}
              <Outlet />
            </main>
          </div>
        </div>
        <RaphaelOverlay />
      </BlockdayProvider>
    </QueryClientProvider>
  );
}

function SignInScreen() {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const configCheck = validateCognitoConfig();
    if (!configCheck.valid && configCheck.error) {
      setError(configCheck.error);
    }
  }, []);

  return (
    <main className="mx-auto my-20 max-w-md border-2 border-grid bg-canvas p-8">
      <p className="section-label">Personal OS</p>
      <h1 className="mt-2 font-display text-3xl uppercase">Sign in to continue</h1>
      <p className="mt-3 text-sm text-muted-foreground">
        Your projects and tasks are private to your account.
      </p>
      <button
        className="mt-6 w-full border-2 border-grid bg-block-blue px-4 py-3 text-sm font-black uppercase text-on-primary"
        onClick={() =>
          void beginCognitoLogin().catch((reason: unknown) =>
            setError(reason instanceof Error ? reason.message : String(reason)),
          )
        }
      >
        Continue with Cognito
      </button>
      {error && (
        <p role="alert" className="mt-3 text-sm text-block-red">
          {error}
        </p>
      )}
    </main>
  );
}
