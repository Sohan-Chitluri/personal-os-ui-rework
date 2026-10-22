const ACCESS_TOKEN_KEY = "personal-os-access-token";
const CODE_VERIFIER_KEY = "personal-os-pkce-verifier";
const OAUTH_STATE_KEY = "personal-os-oauth-state";

export function normalizeCognitoDomain(rawDomain?: string | null): string | null {
  if (!rawDomain) return null;
  const trimmed = rawDomain.trim();
  if (!trimmed) return null;
  const withProtocol = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  try {
    const parsed = new URL(withProtocol);
    if (!parsed.hostname || (parsed.protocol !== "https:" && parsed.protocol !== "http:")) {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

export function normalizeRedirectUri(rawUri?: string | null): string | null {
  if (!rawUri) return null;
  const trimmed = rawUri.trim();
  if (!trimmed) return null;
  try {
    const parsed = new URL(trimmed);
    if (!parsed.protocol || (parsed.protocol !== "https:" && parsed.protocol !== "http:")) {
      return null;
    }
    return parsed.href;
  } catch {
    return null;
  }
}

export interface CognitoConfigValidation {
  valid: boolean;
  domain?: string;
  clientId?: string;
  redirectUri?: string;
  logoutUri?: string;
  error?: string;
}

export function validateCognitoConfig(): CognitoConfigValidation {
  const rawDomain = import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] as string | undefined;
  const rawClientId = import.meta.env["VITE_COGNITO_APP_CLIENT_ID"] as string | undefined;
  const rawRedirectUri = import.meta.env["VITE_COGNITO_REDIRECT_URI"] as string | undefined;
  const rawLogoutUri = import.meta.env["VITE_COGNITO_LOGOUT_URI"] as string | undefined;

  const hasAny = Boolean(rawDomain || rawClientId || rawRedirectUri || rawLogoutUri);
  if (!hasAny) {
    return { valid: false, error: "Cognito sign-in is not configured for this build." };
  }

  if (!rawDomain || !rawDomain.trim()) {
    return { valid: false, error: "Cognito configuration error: missing domain (VITE_COGNITO_HOSTED_UI_DOMAIN)." };
  }
  const domain = normalizeCognitoDomain(rawDomain);
  if (!domain) {
    return {
      valid: false,
      error: `Cognito configuration error: invalid domain "${rawDomain}". Expected a valid hostname or HTTPS URL.`,
    };
  }

  const clientId = rawClientId?.trim();
  if (!clientId) {
    return { valid: false, error: "Cognito configuration error: missing App Client ID (VITE_COGNITO_APP_CLIENT_ID)." };
  }

  if (!rawRedirectUri || !rawRedirectUri.trim()) {
    return { valid: false, error: "Cognito configuration error: missing redirect URI (VITE_COGNITO_REDIRECT_URI)." };
  }
  const redirectUri = normalizeRedirectUri(rawRedirectUri);
  if (!redirectUri) {
    return {
      valid: false,
      error: `Cognito configuration error: invalid redirect URI "${rawRedirectUri}". Expected an absolute HTTP/HTTPS URL.`,
    };
  }

  let logoutUri: string | undefined;
  if (rawLogoutUri && rawLogoutUri.trim()) {
    logoutUri = normalizeRedirectUri(rawLogoutUri) || undefined;
    if (!logoutUri) {
      return {
        valid: false,
        error: `Cognito configuration error: invalid logout URI "${rawLogoutUri}". Expected an absolute HTTP/HTTPS URL.`,
      };
    }
  }

  return {
    valid: true,
    domain,
    clientId,
    redirectUri,
    logoutUri,
  };
}

export function hasCognitoEnv(): boolean {
  return Boolean(
    import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] ||
    import.meta.env["VITE_COGNITO_APP_CLIENT_ID"] ||
    import.meta.env["VITE_COGNITO_REDIRECT_URI"]
  );
}

export const authConfig = {
  get domain() {
    return validateCognitoConfig().domain;
  },
  get clientId() {
    return validateCognitoConfig().clientId;
  },
  get redirectUri() {
    return validateCognitoConfig().redirectUri;
  },
  get logoutUri() {
    return validateCognitoConfig().logoutUri;
  },
};

export function isCognitoConfigured(): boolean {
  return validateCognitoConfig().valid;
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
  const config = validateCognitoConfig();
  if (!config.valid || !config.domain || !config.clientId || !config.redirectUri) {
    throw new Error(config.error || "Cognito sign-in is not configured for this build.");
  }
  const verifier = randomUrlSafe(64);
  const state = randomUrlSafe(32);
  sessionStorage.setItem(CODE_VERIFIER_KEY, verifier);
  sessionStorage.setItem(OAUTH_STATE_KEY, state);

  const url = new URL("/oauth2/authorize", config.domain);
  url.search = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: config.redirectUri,
    code_challenge_method: "S256",
    code_challenge: await sha256UrlSafe(verifier),
    state,
  }).toString();
  window.location.assign(url.toString());
}

export async function completeCognitoLogin(search: string): Promise<void> {
  const config = validateCognitoConfig();
  if (!config.valid || !config.domain || !config.clientId || !config.redirectUri) {
    throw new Error(config.error || "Cognito sign-in is not configured for this build.");
  }
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

  const response = await fetch(new URL("/oauth2/token", config.domain), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId,
      code,
      redirect_uri: config.redirectUri,
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
  const config = validateCognitoConfig();
  if (!config.valid || !config.domain || !config.clientId) {
    window.location.assign("/");
    return;
  }
  const logoutUri =
    customLogoutUri ||
    config.logoutUri ||
    config.redirectUri?.replace(/\/auth\/callback$/, "/") ||
    `${window.location.origin}/`;
  const url = new URL("/logout", config.domain);
  url.search = new URLSearchParams({
    client_id: config.clientId,
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
