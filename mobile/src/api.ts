import { ClassResult, LectureInsights, RemoteJob, SavedClass } from './types';

export const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

function message(body: string, status: number): string {
  try {
    const data = JSON.parse(body);
    if (typeof data.detail === 'string') return data.detail;
  } catch { /* non-JSON proxy response */ }
  return status === 413 ? 'This recording is too large for the server.' : `The server could not complete this request (${status}).`;
}

async function request<T>(url: string, token?: string, method = 'GET'): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, { method, signal: controller.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : undefined });
    const body = await response.text();
    if (!response.ok) throw new ApiError(message(body, response.status), response.status);
    return body ? JSON.parse(body) as T : undefined as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new Error('Cannot reach the server. Check your connection and try again.');
  } finally { clearTimeout(timeout); }
}

export type Health = { mock: boolean; max_audio_mb: number; max_pdf_mb: number; max_duration_seconds: number };
export const health = () => request<Health>(`${API_URL}/health`);
export const status = (item: SavedClass) => request<RemoteJob>(`${item.apiUrl}/jobs/${item.remoteId}`, item.token);
export const result = (item: SavedClass) => request<ClassResult>(`${item.apiUrl}/jobs/${item.remoteId}/result`, item.token);
export const lectureInsights = (item: SavedClass) => request<LectureInsights>(`${item.apiUrl}/jobs/${item.remoteId}/insights`, item.token);
export const deleteRemote = (item: SavedClass) => request<void>(`${item.apiUrl}/jobs/${item.remoteId}`, item.token, 'DELETE');

export function upload(item: SavedClass, onProgress: (fraction: number | undefined) => void): Promise<RemoteJob> {
  const body = new FormData();
  body.append('request_id', item.id);
  body.append('title', item.draft.title);
  body.append('topics', item.draft.topics);
  body.append('syllabus_text', item.draft.syllabusText);
  // React Native uses URI-backed form parts rather than browser File objects.
  body.append('audio', { uri: item.draft.audioUri, name: item.draft.audioName, type: item.draft.audioType } as unknown as Blob);
  if (item.draft.pdfUri) body.append('syllabus', {
    uri: item.draft.pdfUri, name: item.draft.pdfName ?? 'syllabus.pdf', type: 'application/pdf',
  } as unknown as Blob);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${item.apiUrl}/jobs`);
    xhr.setRequestHeader('Authorization', `Bearer ${item.token}`);
    xhr.timeout = 30 * 60 * 1000;
    xhr.upload.onprogress = event => onProgress(event.lengthComputable ? event.loaded / event.total : undefined);
    xhr.onerror = () => reject(new Error('Upload connection was interrupted. Retry to safely check or resume this class.'));
    xhr.ontimeout = () => reject(new Error('The upload timed out. Retry when your connection is stable.'));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText) as RemoteJob); }
        catch { reject(new Error('The server response was incomplete. Retry this upload safely.')); }
      } else reject(new ApiError(message(xhr.responseText, xhr.status), xhr.status));
    };
    xhr.send(body);
  });
}
