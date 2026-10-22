import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  isCognitoConfigured,
  getCognitoAccessToken,
  clearCognitoSession,
  beginCognitoLogin,
  completeCognitoLogin,
  isTokenExpired,
  beginCognitoLogout,
  handleUnauthorized,
  normalizeCognitoDomain,
  normalizeRedirectUri,
  validateCognitoConfig,
  hasCognitoEnv,
} from "@/lib/auth";
import { resolveApiProxyTarget } from "../../vite.config";

describe("Cognito Authentication client", () => {
  const originalEnv = { ...import.meta.env };
  const mockDomain = "https://personal-os-staging.auth.us-east-1.amazoncognito.com";
  const mockClientId = "test-client-id-123";
  const mockRedirectUri = "https://personal-os-staging.vercel.app/auth/callback";

  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
    import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] = mockDomain;
    import.meta.env["VITE_COGNITO_APP_CLIENT_ID"] = mockClientId;
    import.meta.env["VITE_COGNITO_REDIRECT_URI"] = mockRedirectUri;
  });

  afterEach(() => {
    Object.assign(import.meta.env, originalEnv);
  });

  it("reports configured when all environment variables are present", () => {
    expect(isCognitoConfigured()).toBe(true);

    delete import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"];
    expect(isCognitoConfigured()).toBe(false);
  });

  it("stores and clears access tokens in session storage", () => {
    expect(getCognitoAccessToken()).toBeNull();
    sessionStorage.setItem("personal-os-access-token", "jwt-token-abc");
    expect(getCognitoAccessToken()).toBe("jwt-token-abc");

    clearCognitoSession();
    expect(getCognitoAccessToken()).toBeNull();
  });

  it("initiates PKCE login by setting state, verifier, and redirecting", async () => {
    const assignMock = vi.fn();
    delete (window as unknown as { location: unknown }).location;
    (window as unknown as { location: unknown }).location = { assign: assignMock };

    await beginCognitoLogin();

    const verifier = sessionStorage.getItem("personal-os-pkce-verifier");
    const state = sessionStorage.getItem("personal-os-oauth-state");
    expect(verifier).toBeTruthy();
    expect(state).toBeTruthy();

    expect(assignMock).toHaveBeenCalledOnce();
    const targetUrl = new URL(assignMock.mock.calls[0][0]);
    expect(targetUrl.origin).toBe("https://personal-os-staging.auth.us-east-1.amazoncognito.com");
    expect(targetUrl.pathname).toBe("/oauth2/authorize");
    expect(targetUrl.searchParams.get("client_id")).toBe(mockClientId);
    expect(targetUrl.searchParams.get("response_type")).toBe("code");
    expect(targetUrl.searchParams.get("scope")).toBe("openid email profile");
    expect(targetUrl.searchParams.get("redirect_uri")).toBe(mockRedirectUri);
    expect(targetUrl.searchParams.get("code_challenge_method")).toBe("S256");
    expect(targetUrl.searchParams.get("state")).toBe(state);
    expect(targetUrl.searchParams.get("code_challenge")).toBeTruthy();
  });

  it("completes code exchange with PKCE verifier", async () => {
    const mockState = "random-state-123";
    const mockVerifier = "random-verifier-456";
    sessionStorage.setItem("personal-os-oauth-state", mockState);
    sessionStorage.setItem("personal-os-pkce-verifier", mockVerifier);

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: "mock-access-token-xyz",
        id_token: "mock-id-token",
        token_type: "Bearer",
        expires_in: 3600,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await completeCognitoLogin(`?code=auth-code-789&state=${mockState}`);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [fetchUrl, fetchOptions] = fetchMock.mock.calls[0];
    expect(String(fetchUrl)).toBe(`${mockDomain}/oauth2/token`);
    expect(fetchOptions.method).toBe("POST");
    expect(fetchOptions.headers["Content-Type"]).toBe("application/x-www-form-urlencoded");

    const bodyParams = new URLSearchParams(fetchOptions.body);
    expect(bodyParams.get("grant_type")).toBe("authorization_code");
    expect(bodyParams.get("client_id")).toBe(mockClientId);
    expect(bodyParams.get("code")).toBe("auth-code-789");
    expect(bodyParams.get("redirect_uri")).toBe(mockRedirectUri);
    expect(bodyParams.get("code_verifier")).toBe(mockVerifier);

    expect(sessionStorage.getItem("personal-os-access-token")).toBe("mock-access-token-xyz");
    expect(sessionStorage.getItem("personal-os-oauth-state")).toBeNull();
    expect(sessionStorage.getItem("personal-os-pkce-verifier")).toBeNull();
  });

  it("rejects token exchange when state does not match", async () => {
    sessionStorage.setItem("personal-os-oauth-state", "expected-state");
    sessionStorage.setItem("personal-os-pkce-verifier", "verifier");

    await expect(completeCognitoLogin("?code=auth-code&state=mismatched-state")).rejects.toThrow(
      "Sign-in response was incomplete or could not be verified",
    );
  });

  it("rejects token exchange when OAuth error is returned in URL", async () => {
    await expect(
      completeCognitoLogin("?error=access_denied&error_description=User+cancelled"),
    ).rejects.toThrow("Sign-in failed: User cancelled");
  });

  it("handles token endpoint errors gracefully", async () => {
    sessionStorage.setItem("personal-os-oauth-state", "state");
    sessionStorage.setItem("personal-os-pkce-verifier", "verifier");

    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({
        error: "invalid_grant",
        error_description: "Code has expired",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(completeCognitoLogin("?code=auth-code&state=state")).rejects.toThrow(
      "Code has expired",
    );
  });

  it("checks token expiration correctly", () => {
    // Non-JWT token string
    expect(isTokenExpired("simple-token")).toBe(false);

    // JWT with exp in the future (year 2038)
    const futurePayload = btoa(JSON.stringify({ exp: 2147483647 }));
    const futureToken = `header.${futurePayload}.signature`;
    expect(isTokenExpired(futureToken)).toBe(false);

    // JWT with exp in the past (year 2020)
    const pastPayload = btoa(JSON.stringify({ exp: 1577836800 }));
    const pastToken = `header.${pastPayload}.signature`;
    expect(isTokenExpired(pastToken)).toBe(true);
  });

  it("auto-clears expired tokens on getCognitoAccessToken", () => {
    const pastPayload = btoa(JSON.stringify({ exp: 1577836800 }));
    const pastToken = `header.${pastPayload}.signature`;
    sessionStorage.setItem("personal-os-access-token", pastToken);

    expect(getCognitoAccessToken()).toBeNull();
    expect(sessionStorage.getItem("personal-os-access-token")).toBeNull();
  });

  it("initiates logout by clearing session and redirecting to Cognito logout endpoint", () => {
    const assignMock = vi.fn();
    delete (window as unknown as { location: unknown }).location;
    (window as unknown as { location: unknown }).location = {
      assign: assignMock,
      origin: "http://localhost:8080",
    };

    sessionStorage.setItem("personal-os-access-token", "valid-token");
    beginCognitoLogout();

    expect(sessionStorage.getItem("personal-os-access-token")).toBeNull();
    expect(assignMock).toHaveBeenCalledOnce();
    const logoutUrl = new URL(assignMock.mock.calls[0][0]);
    expect(logoutUrl.origin).toBe("https://personal-os-staging.auth.us-east-1.amazoncognito.com");
    expect(logoutUrl.pathname).toBe("/logout");
    expect(logoutUrl.searchParams.get("client_id")).toBe(mockClientId);
    expect(logoutUrl.searchParams.get("logout_uri")).toBe("https://personal-os-staging.vercel.app/");
  });

  it("handles unauthorized responses by clearing session and emitting event", () => {
    sessionStorage.setItem("personal-os-access-token", "stale-token");
    const listener = vi.fn();
    window.addEventListener("personal-os:unauthorized", listener);

    handleUnauthorized();

    expect(sessionStorage.getItem("personal-os-access-token")).toBeNull();
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener("personal-os:unauthorized", listener);
  });

  it("resolves API proxy target from env or default", () => {
    expect(resolveApiProxyTarget({})).toBe("http://127.0.0.1:8000");
    expect(
      resolveApiProxyTarget({
        VITE_API_PROXY_TARGET: "http://staging-alb.example.com",
      }),
    ).toBe("http://staging-alb.example.com");
    expect(
      resolveApiProxyTarget({
        API_PROXY_TARGET: "http://fallback-alb.example.com",
      }),
    ).toBe("http://fallback-alb.example.com");
  });

  it("supports dev synthetic token via VITE_DEV_AUTH_TOKEN when session storage is empty", () => {
    expect(getCognitoAccessToken()).toBeNull();

    import.meta.env["VITE_DEV_AUTH_TOKEN"] = "test:dev-user";
    expect(getCognitoAccessToken()).toBe("test:dev-user");

    import.meta.env["VITE_DEV_AUTH_TOKEN"] = "test:custom-local-user";
    expect(getCognitoAccessToken()).toBe("test:custom-local-user");
  });

  it("automatically falls back to test:dev-user in local development mode without Cognito", () => {
    delete import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"];
    delete import.meta.env["VITE_DEV_AUTH_TOKEN"];
    expect(isCognitoConfigured()).toBe(false);

    // In vitest (MODE === 'test'), fallback is suppressed to avoid leaking into unit tests
    expect(getCognitoAccessToken()).toBeNull();

    // In local development (MODE === 'development', DEV === true), fallback is active
    import.meta.env.MODE = "development";
    expect(getCognitoAccessToken()).toBe("test:dev-user");
  });

  it("prioritizes explicit session storage token over dev fallback when present", () => {
    import.meta.env["VITE_DEV_AUTH_TOKEN"] = "test:dev-user";
    sessionStorage.setItem("personal-os-access-token", "test:session-override-user");
    expect(getCognitoAccessToken()).toBe("test:session-override-user");
  });

  describe("Configuration validation and normalization", () => {
    it("normalizes domains without https:// scheme", () => {
      expect(
        normalizeCognitoDomain("personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com"),
      ).toBe("https://personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com");

      expect(
        normalizeCognitoDomain("https://personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com/"),
      ).toBe("https://personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com");

      expect(
        normalizeCognitoDomain("  personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com/  "),
      ).toBe("https://personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com");

      expect(normalizeCognitoDomain("http://localhost:9229")).toBe("http://localhost:9229");
    });

    it("rejects invalid domains", () => {
      expect(normalizeCognitoDomain("")).toBeNull();
      expect(normalizeCognitoDomain(null)).toBeNull();
      expect(normalizeCognitoDomain("   ")).toBeNull();
      expect(normalizeCognitoDomain("http://")).toBeNull();
      expect(normalizeCognitoDomain("invalid domain with spaces")).toBeNull();
    });

    it("normalizes and validates redirect URIs", () => {
      expect(
        normalizeRedirectUri("https://staging.d1gbedl9yrj8s.amplifyapp.com/auth/callback"),
      ).toBe("https://staging.d1gbedl9yrj8s.amplifyapp.com/auth/callback");

      expect(
        normalizeRedirectUri("  https://staging.d1gbedl9yrj8s.amplifyapp.com/auth/callback  "),
      ).toBe("https://staging.d1gbedl9yrj8s.amplifyapp.com/auth/callback");

      expect(normalizeRedirectUri("not-a-url")).toBeNull();
      expect(normalizeRedirectUri("")).toBeNull();
    });

    it("validates full configuration successfully with raw domain without scheme", () => {
      import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] =
        "personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com";
      import.meta.env["VITE_COGNITO_APP_CLIENT_ID"] = "195u9r05vim046hkij9vec8d81";
      import.meta.env["VITE_COGNITO_REDIRECT_URI"] =
        "https://staging.d1gbedl9yrj8s.amplifyapp.com/auth/callback";

      const result = validateCognitoConfig();
      expect(result.valid).toBe(true);
      expect(result.domain).toBe(
        "https://personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com",
      );
      expect(result.clientId).toBe("195u9r05vim046hkij9vec8d81");
      expect(result.redirectUri).toBe(
        "https://staging.d1gbedl9yrj8s.amplifyapp.com/auth/callback",
      );
      expect(isCognitoConfigured()).toBe(true);
      expect(hasCognitoEnv()).toBe(true);
    });

    it("returns actionable error when domain is missing or invalid", () => {
      delete import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"];
      const missing = validateCognitoConfig();
      expect(missing.valid).toBe(false);
      expect(missing.error).toContain("missing domain (VITE_COGNITO_HOSTED_UI_DOMAIN)");

      import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] = "invalid domain with spaces";
      const invalid = validateCognitoConfig();
      expect(invalid.valid).toBe(false);
      expect(invalid.error).toContain("invalid domain");
    });

    it("returns actionable error when client ID is missing", () => {
      delete import.meta.env["VITE_COGNITO_APP_CLIENT_ID"];
      const missing = validateCognitoConfig();
      expect(missing.valid).toBe(false);
      expect(missing.error).toContain("missing App Client ID (VITE_COGNITO_APP_CLIENT_ID)");
    });

    it("returns actionable error when redirect URI is missing or invalid", () => {
      delete import.meta.env["VITE_COGNITO_REDIRECT_URI"];
      const missing = validateCognitoConfig();
      expect(missing.valid).toBe(false);
      expect(missing.error).toContain("missing redirect URI (VITE_COGNITO_REDIRECT_URI)");

      import.meta.env["VITE_COGNITO_REDIRECT_URI"] = "invalid-uri";
      const invalid = validateCognitoConfig();
      expect(invalid.valid).toBe(false);
      expect(invalid.error).toContain("invalid redirect URI");
    });

    it("constructs authorization URL correctly when domain lacks https:// prefix", async () => {
      import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] =
        "personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com";
      const assignMock = vi.fn();
      delete (window as unknown as { location: unknown }).location;
      (window as unknown as { location: unknown }).location = { assign: assignMock };

      await beginCognitoLogin();

      expect(assignMock).toHaveBeenCalledOnce();
      const targetUrl = new URL(assignMock.mock.calls[0][0]);
      expect(targetUrl.origin).toBe(
        "https://personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com",
      );
      expect(targetUrl.pathname).toBe("/oauth2/authorize");
    });

    it("constructs logout URL correctly when domain lacks https:// prefix", () => {
      import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] =
        "personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com";
      const assignMock = vi.fn();
      delete (window as unknown as { location: unknown }).location;
      (window as unknown as { location: unknown }).location = {
        assign: assignMock,
        origin: "https://staging.d1gbedl9yrj8s.amplifyapp.com",
      };

      beginCognitoLogout();

      expect(assignMock).toHaveBeenCalledOnce();
      const logoutUrl = new URL(assignMock.mock.calls[0][0]);
      expect(logoutUrl.origin).toBe(
        "https://personal-os-staging-04caa25a.auth.us-east-1.amazoncognito.com",
      );
      expect(logoutUrl.pathname).toBe("/logout");
    });

    it("throws a descriptive error in beginCognitoLogin when configuration is invalid without throwing uncaught URL constructor TypeError", async () => {
      import.meta.env["VITE_COGNITO_HOSTED_UI_DOMAIN"] = "invalid domain with spaces";

      await expect(beginCognitoLogin()).rejects.toThrow(
        /Cognito configuration error: invalid domain/,
      );
    });
  });
});
