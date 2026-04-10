import axios from 'axios';

const rawBaseURL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:8000/api/v1';

function resolveApiBaseURL(): string {
  if (typeof window === 'undefined') {
    return rawBaseURL;
  }

  try {
    const url = new URL(rawBaseURL);
    const currentHost = window.location.hostname;
    const isLocalPair = ['localhost', '127.0.0.1'].includes(url.hostname) && ['localhost', '127.0.0.1'].includes(currentHost);

    if (isLocalPair && url.hostname !== currentHost) {
      url.hostname = currentHost;
    }

    return url.toString().replace(/\/$/, '');
  } catch {
    return rawBaseURL;
  }
}

const api = axios.create({
  baseURL: resolveApiBaseURL(),
});

api.interceptors.response.use(
  (response) => {
    if (response.data && response.data.code !== undefined) {
      if (response.data.code >= 200 && response.data.code < 300) {
        return response.data;
      }
      return Promise.reject(new Error(response.data.message || 'API Error'));
    }
    return response.data;
  },
  (error) => Promise.reject(error)
);

export function getApiErrorMessage(error: unknown, fallback = 'API Error'): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail;
    }
    if (error.message) {
      return error.message;
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

export function isNotFoundError(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 404;
}

export function buildAssetContentUrl(assetId: string): string {
  return `${resolveApiBaseURL()}/assets/${assetId}/content`;
}

export default api;
