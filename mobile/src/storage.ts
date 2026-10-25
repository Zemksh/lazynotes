import * as SQLite from 'expo-sqlite';
import { Directory, File, Paths } from 'expo-file-system';
import * as LegacyFileSystem from 'expo-file-system/legacy';
import * as Crypto from 'expo-crypto';
import { Draft, SavedClass } from './types';

function safeExtension(name: string): string {
  return name.match(/\.([a-zA-Z0-9]{1,8})$/)?.[1]?.toLowerCase() ?? 'bin';
}

export async function importPickedFile(uri: string, name: string): Promise<string> {
  const imports = new Directory(Paths.cache, 'picked-files');
  imports.create({ idempotent: true, intermediates: true });
  const destination = new File(imports, `${Crypto.randomUUID()}.${safeExtension(name)}`);
  try {
    // Android supplies the original granted content:// URI. Copy it directly
    // into this experience's cache, avoiding DocumentPicker's shared Go cache.
    await LegacyFileSystem.copyAsync({ from: uri, to: destination.uri });
    if (!destination.exists || destination.size === 0) {
      throw new Error('The selected file could not be read.');
    }
    return destination.uri;
  } catch (error) {
    // Cleanup must not replace the original import error.
    try { if (destination.exists) destination.delete(); } catch { /* best effort */ }
    throw error;
  }
}

let database: Promise<SQLite.SQLiteDatabase> | undefined;
function db() {
  database ??= (async () => {
    const connection = await SQLite.openDatabaseAsync('lazynotes.db');
    await connection.execAsync(`PRAGMA journal_mode = WAL;
      CREATE TABLE IF NOT EXISTS classes (id TEXT PRIMARY KEY NOT NULL, created_at INTEGER NOT NULL, data TEXT NOT NULL);`);
    return connection;
  })();
  return database;
}

export async function listClasses(): Promise<SavedClass[]> {
  const rows = await (await db()).getAllAsync<{ data: string }>('SELECT data FROM classes ORDER BY created_at DESC');
  return rows.map(row => JSON.parse(row.data) as SavedClass);
}

export async function readClass(id: string): Promise<SavedClass | undefined> {
  const row = await (await db()).getFirstAsync<{ data: string }>('SELECT data FROM classes WHERE id = ?', id);
  return row ? JSON.parse(row.data) as SavedClass : undefined;
}

export async function patchClass(id: string, patch: Partial<SavedClass>) {
  const connection = await db();
  await connection.withExclusiveTransactionAsync(async transaction => {
    const row = await transaction.getFirstAsync<{ data: string }>('SELECT data FROM classes WHERE id = ?', id);
    if (!row) throw new Error('This saved class is no longer available.');
    const data = { ...JSON.parse(row.data), ...patch };
    await transaction.runAsync('UPDATE classes SET data = ? WHERE id = ?', JSON.stringify(data), id);
  });
}

export async function createClass(draft: Draft, apiUrl: string): Promise<SavedClass> {
  const id = Crypto.randomUUID();
  const folder = new Directory(Paths.document, 'recordings', id);
  folder.create({ intermediates: true });
  try {
    const audio = new File(folder, `recording.${safeExtension(draft.audioName)}`);
    await new File(draft.audioUri).copy(audio);
    let pdfUri: string | undefined;
    if (draft.pdfUri) {
      const pdf = new File(folder, 'syllabus.pdf');
      await new File(draft.pdfUri).copy(pdf);
      pdfUri = pdf.uri;
    }
    const bytes = await Crypto.getRandomBytesAsync(32);
    const saved: SavedClass = {
      id, apiUrl, token: Array.from(bytes, value => value.toString(16).padStart(2, '0')).join(''),
      createdAt: Date.now(), title: draft.title.trim() || draft.topics.trim().split('\n')[0]!.slice(0, 80),
      stage: 'uploading', draft: { ...draft, audioUri: audio.uri, pdfUri }, edits: {},
    };
    await (await db()).runAsync('INSERT INTO classes VALUES (?, ?, ?)', id, saved.createdAt, JSON.stringify(saved));
    return saved;
  } catch (error) {
    try { if (folder.exists) folder.delete(); } catch { /* preserve the save error */ }
    throw error;
  }
}

export async function removeLocalClass(id: string) {
  await (await db()).runAsync('DELETE FROM classes WHERE id = ?', id);
  const folder = new Directory(Paths.document, 'recordings', id);
  if (folder.exists) folder.delete();
}

export function hasFile(uri?: string): boolean {
  try { return !!uri && new File(uri).exists; } catch { return false; }
}
