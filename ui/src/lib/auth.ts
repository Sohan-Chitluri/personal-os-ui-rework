const ACCESS_TOKEN_KEY = "personal-os-access-token";
const CODE_VERIFIER_KEY = "personal-os-pkce-verifier";
const OAUTH_STATE_KEY = "personal-os-oauth-state";

const authConfig = {
  get domain() {
    return import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] as string | undefined;
  },
  get clientId() {
    return import.meta.env["VITE_COGNITO_APP_CLIENT_ID"] as string | undefined;
  },
  get redirectUri() {
    return import.meta.env["VITE_COGNITO_REDIRECT_URI"] as string | undefined;
  },
  get logoutUri() {
    return import.meta.env["VITE_COGNITO_LOGOUT_URI"] as string | undefined;
  },
};

export function isCognitoConfigured(): boolean {
  return Boolean(authConfig.domain && authConfig.clientId && authConfig.redirectUri);
}

export function isTokenExpired(token: string): boolean {
  try {
    const parts = token.split(".");
    if (parts.length < 2) return false;
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(base64);
    const payload = JSON.parse(json) as { exp?: number };
    if (typeof payload.exp === "number") {
      return Date.now() / 1000 >= payload.exp - 10;
    }
  } catch {
    return false;
  }
  return false;
}

function getDevAuthFallback(): string | null {
  const explicitDevToken = import.meta.env["VITE_DEV_AUTH_TOKEN"] as string | undefined;
  if (explicitDevToken) {
    return explicitDevToken;
  }
  // In local development (vite dev) when Cognito is not configured, supply the local synthetic dev user
  if (import.meta.env.DEV && import.meta.env.MODE !== "test" && !isCognitoConfigured()) {
    return "test:dev-user";
  }
  return null;
}

export function getCognitoAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  const token = window.sessionStorage.getItem(ACCESS_TOKEN_KEY);
  if (token) {
    if (isTokenExpired(token)) {
      clearCognitoSession();
      return getDevAuthFallback();
    }
    return token;
  }
  return getDevAuthFallback();
}

function randomUrlSafe(bytes: number): string {
  const value = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...value))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function sha256UrlSafe(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

export async function beginCognitoLogin(): Promise<void> {
  if (!isCognitoConfigured()) throw new Error("Cognito sign-in is not configured for this build.");
  const verifier = randomUrlSafe(64);
  const state = randomUrlSafe(32);
  sessionStorage.setItem(CODE_VERIFIER_KEY, verifier);
  sessionStorage.setItem(OAUTH_STATE_KEY, state);

  const url = new URL("/oauth2/authorize", authConfig.domain);
  url.search = new URLSearchParams({
    client_id: authConfig.clientId!,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: authConfig.redirectUri!,
    code_challenge_method: "S256",
    code_challenge: await sha256UrlSafe(verifier),
    state,
  }).toString();
  window.location.assign(url.toString());
}

export async function completeCognitoLogin(search: string): Promise<void> {
  if (!isCognitoConfigured()) throw new Error("Cognito sign-in is not configured for this build.");
  const params = new URLSearchParams(search);
  const oauthError = params.get("error_description") || params.get("error");
  if (oauthError) throw new Error(`Sign-in failed: ${oauthError}`);

  const code = params.get("code");
  const returnedState = params.get("state");
  const expectedState = sessionStorage.getItem(OAUTH_STATE_KEY);
  const verifier = sessionStorage.getItem(CODE_VERIFIER_KEY);
  if (!code || !returnedState || returnedState !== expectedState || !verifier) {
    throw new Error("Sign-in response was incomplete or could not be verified. Please try again.");
  }

  const response = await fetch(new URL("/oauth2/token", authConfig.domain), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: authConfig.clientId!,
      code,
      redirect_uri: authConfig.redirectUri!,
      code_verifier: verifier,
    }),
  });
  const payload = (await response.json().catch(() => ({}))) as {
    access_token?: string;
    error?: string;
    error_description?: string;
  };
  if (!response.ok || !payload.access_token) {
    throw new Error(payload.error_description || payload.error || `Token exchange failed (${response.status}).`);
  }

  sessionStorage.setItem(ACCESS_TOKEN_KEY, payload.access_token);
  sessionStorage.removeItem(CODE_VERIFIER_KEY);
  sessionStorage.removeItem(OAUTH_STATE_KEY);
}

export function clearCognitoSession(): void {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
}

export function beginCognitoLogout(customLogoutUri?: string): void {
  clearCognitoSession();
  if (typeof window === "undefined") return;
  if (!isCognitoConfigured() || !authConfig.domain || !authConfig.clientId) {
    window.location.assign("/");
    return;
  }
  const logoutUri =
    customLogoutUri ||
    authConfig.logoutUri ||
    authConfig.redirectUri?.replace(/\/auth\/callback$/, "/") ||
    `${window.location.origin}/`;
  const url = new URL("/logout", authConfig.domain);
  url.search = new URLSearchParams({
    client_id: authConfig.clientId,
    logout_uri: logoutUri,
  }).toString();
  window.location.assign(url.toString());
}

export function handleUnauthorized(): void {
  clearCognitoSession();
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("personal-os:unauthorized"));
  }
}
