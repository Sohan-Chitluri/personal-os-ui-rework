import { useEffect, useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { completeCognitoLogin } from "@/lib/auth";

export const Route = createFileRoute("/auth/callback")({ component: AuthCallbackPage });

function AuthCallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    void completeCognitoLogin(window.location.search)
      .then(() => navigate({ to: "/", replace: true }))
      .catch((reason: unknown) => {
        if (mounted) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      mounted = false;
    };
  }, [navigate]);

  return (
    <div className="mx-auto my-16 max-w-lg border-2 border-grid bg-canvas p-8">
      <h1 className="font-display text-2xl uppercase">Signing in</h1>
      {error ? (
        <div role="alert" className="mt-4 text-sm text-block-red">
          {error} <a className="underline" href="/">Return to sign in</a>
        </div>
      ) : <p className="mt-3 text-sm text-muted-foreground">Completing secure sign-in…</p>}
    </div>
  );
}
