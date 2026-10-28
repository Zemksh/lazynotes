import { API_URL, status } from './api';
import { patchClass } from './storage';
import { SavedClass } from './types';

export async function reconnectClass(item: SavedClass): Promise<void> {
  if (!item.remoteId || item.serverDeleted) throw new Error('No existing server job to reconnect.');
  const remote = await status({ ...item, apiUrl: API_URL });
  if (remote.id !== item.remoteId) throw new Error('The server returned a different class. Address unchanged.');
  await patchClass(item.id, {
    apiUrl: API_URL, syncError: undefined,
    ...(!item.result ? { stage: remote.state, error: remote.error ?? undefined, expiresAt: remote.expires_at } : {}),
  });
}
