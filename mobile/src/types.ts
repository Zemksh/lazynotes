export type Stage = 'uploading' | 'queued' | 'transcribing' | 'preparing' | 'ready' | 'failed' | 'expired';
export type Segment = { id: string; start: number; end: number; text: string };
export type Comparison = { columns: string[]; rows: string[][] };
export type Bullet = string | { text: string; children: string[] };
export type Section = { heading: string; subheading?: string; body: string; bullets?: Bullet[];
  comparison?: Comparison | null; source_segment_ids: string[] };
export type SectionEdit = { heading: string; body: string; subheading?: string; bullets?: Bullet[] };
export type ClassResult = {
  job_id: string; mock: boolean; duration_seconds: number;
  notes: { title: string; sections: Section[] };
  transcript: Segment[]; warnings: string[];
};
export type Draft = {
  title: string; topics: string; syllabusText: string;
  audioUri: string; audioName: string; audioSize?: number; audioType: string;
  pdfUri?: string; pdfName?: string;
};
export type SavedClass = {
  id: string; remoteId?: string; token: string; apiUrl: string; createdAt: number;
  title: string; stage: Stage; draft: Draft; result?: ClassResult;
  edits: Record<string, SectionEdit>;
  error?: string; syncError?: string; mock?: boolean; expiresAt?: number;
  serverDeleted?: boolean;
};
export type RemoteJob = {
  id: string; state: Exclude<Stage, 'uploading' | 'expired'>; title: string;
  mock: boolean | number; error: string | null; expires_at: number;
};
