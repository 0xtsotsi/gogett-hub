import { LemmaClient } from 'lemma-sdk';
import { config } from '@/lib/config';

function toOrigin(value: string): string | null {
    try {
        return new URL(value).origin;
    } catch {
        return null;
    }
}

function getApiBaseUrl(): string {
    return config.API_URL;
}

function getAuthBaseUrl(): string {
    const authUrl = config.AUTH_URL;
    const withProtocol = /^https?:\/\//i.test(authUrl) ? authUrl : `https://${authUrl}`;
    const origin = toOrigin(withProtocol);
    return origin ? `${origin}/auth` : `${config.SITE_URL.replace(/\/$/, '')}/auth`;
}

let baseClient: LemmaClient | null = null;
const scopedClients = new Map<string, LemmaClient>();

function createBaseClient(): LemmaClient {
    return new LemmaClient({
        apiUrl: getApiBaseUrl(),
        authUrl: getAuthBaseUrl(),
    });
}

function getBaseClient(): LemmaClient {
    if (!baseClient) {
        baseClient = createBaseClient();
        scopedClients.set('__default__', baseClient);
    }
    return baseClient;
}

export function getLemmaClient(podId?: string): LemmaClient {
    if (!podId) {
        return getBaseClient();
    }

    const key = podId.trim();
    const cached = scopedClients.get(key);
    if (cached) return cached;

    const scopedClient = getBaseClient().withPod(key);
    scopedClients.set(key, scopedClient);
    return scopedClient;
}

export function getLemmaApiBaseUrl(): string {
    return getApiBaseUrl();
}

export function getLemmaAuthUrl(): string {
    return getAuthBaseUrl();
}

export async function getLemmaRequestHeaders(initial?: HeadersInit): Promise<HeadersInit> {
    return { ...(initial || {}) };
}

/**
 * Authenticated fetch for backend endpoints not yet in the generated SDK.
 * Reuses the SDK's AuthManager to build credentials/headers exactly as the
 * typed methods do (Bearer token or SuperTokens cookie), so anti-CSRF and
 * session refresh continue to work via the global SuperTokens fetch interceptor.
 */
export async function lemmaFetch(path: string, init: RequestInit = {}): Promise<Response> {
    const authedInit = getLemmaClient().auth.getRequestInit(init);
    const headers = new Headers(authedInit.headers as HeadersInit);
    // For multipart uploads let the browser set the boundary Content-Type.
    if (init.body instanceof FormData) headers.delete('Content-Type');
    return fetch(`${getLemmaApiBaseUrl()}${path}`, { ...authedInit, headers });
}
