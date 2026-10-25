import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import * as api from './api';
import { hasFile, listClasses, patchClass, readClass } from './storage';
import { SavedClass } from './types';

type ClassesContext = {
  items: SavedClass[]; loading: boolean; storageError?: string; refresh: () => Promise<void>;
  submit: (id: string) => Promise<void>; sync: () => Promise<void>;
  uploads: Record<string, number | null>;
};
const Context = createContext<ClassesContext | null>(null);

export function ClassesProvider({ children }: PropsWithChildren) {
  const [items, setItems] = useState<SavedClass[]>([]);
  const [loading, setLoading] = useState(true);
  const [storageError, setStorageError] = useState<string>();
  const [uploads, setUploads] = useState<Record<string, number | null>>({});
  const active = useRef(new Set<string>());
  const checking = useRef(false);
  const refresh = useCallback(async () => {
    try { setItems(await listClasses()); setStorageError(undefined); }
    catch { setStorageError('Saved classes could not be opened. Check available storage and restart the app.'); }
    finally { setLoading(false); }
  }, []);

  const sync = useCallback(async () => {
    if (checking.current || AppState.currentState === 'background') return;
    checking.current = true;
    try {
      const saved = await listClasses();
      for (const item of saved) {
        if (!item.remoteId || item.result || ['failed', 'expired'].includes(item.stage)) continue;
        try {
          const remote = await api.status(item);
          if (remote.state === 'ready') {
            const output = await api.result(item);
            if (output.job_id !== item.remoteId || !Array.isArray(output.transcript) || !output.notes?.sections?.length) {
              throw new Error('The server returned incomplete notes. Try checking again.');
            }
            await patchClass(item.id, { result: output, title: item.draft.title.trim() || output.notes.title,
              stage: 'ready', mock: !!remote.mock, expiresAt: remote.expires_at, syncError: undefined });
          } else {
            await patchClass(item.id, { stage: remote.state, error: remote.error ?? undefined,
              mock: !!remote.mock, expiresAt: remote.expires_at, syncError: undefined });
          }
        } catch (error) {
          const expired = error instanceof api.ApiError && [404, 410].includes(error.status);
          await patchClass(item.id, { ...(expired ? { stage: 'expired' as const } : {}),
            syncError: error instanceof Error ? error.message : 'Could not check this class.' });
        }
      }
      await refresh();
    } catch { setStorageError('Could not update saved classes. Check available device storage.'); }
    finally { checking.current = false; }
  }, [refresh]);

  const submit = useCallback(async (id: string) => {
    if (active.current.has(id)) return;
    active.current.add(id);
    setUploads(previous => ({ ...previous, [id]: null }));
    try {
      const item = await readClass(id);
      if (!item || item.remoteId) return;
      if (!hasFile(item.draft.audioUri)) throw new Error('The recording is no longer on this device. Start a new class and select it again.');
      await patchClass(id, { error: undefined, syncError: undefined });
      const job = await api.upload(item, value => setUploads(previous => ({ ...previous, [id]: value ?? null })));
      await patchClass(id, { remoteId: job.id, stage: job.state, mock: !!job.mock,
        expiresAt: job.expires_at, error: job.error ?? undefined });
    } catch (error) {
      try {
        const invalid = error instanceof api.ApiError && [410, 413, 422].includes(error.status);
        await patchClass(id, { error: error instanceof Error ? error.message : 'Upload was interrupted.',
          ...(invalid ? { stage: 'failed' as const } : {}) });
      } catch { setStorageError('Could not save upload status. Check device storage before retrying.'); }
    } finally {
      active.current.delete(id);
      setUploads(previous => { const next = { ...previous }; delete next[id]; return next; });
      await refresh();
      void sync();
    }
  }, [refresh, sync]);

  useEffect(() => {
    void refresh().then(sync);
    const timer = setInterval(() => { void sync(); }, 5000);
    const subscription = AppState.addEventListener('change', state => { if (state === 'active') void sync(); });
    return () => { clearInterval(timer); subscription.remove(); };
  }, [refresh, sync]);

  return <Context.Provider value={{ items, loading, storageError, refresh, submit, sync, uploads }}>{children}</Context.Provider>;
}

export function useClasses() {
  const value = useContext(Context);
  if (!value) throw new Error('Missing classes provider');
  return value;
}
