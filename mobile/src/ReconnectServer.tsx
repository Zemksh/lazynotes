import { useRef, useState } from 'react';
import { Alert, Text, View } from 'react-native';
import { API_URL, ApiError } from './api';
import { Button, ErrorMessage } from './components';
import { reconnectClass } from './reconnect';
import { ui } from './theme';
import { SavedClass } from './types';

export function ReconnectServer({ item, onConnected }: { item: SavedClass; onConnected: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const guard = useRef(false);
  if (!item.remoteId || item.serverDeleted || item.apiUrl.replace(/\/$/, '') === API_URL) return null;
  async function connect() {
    if (guard.current) return;
    guard.current = true; setBusy(true); setError(undefined);
    try { await reconnectClass(item); await onConnected(); }
    catch (e) {
      setError(e instanceof ApiError && [404, 410].includes(e.status)
        ? 'This server cannot find an accessible copy of the class. It may have expired or be a different server. Your saved notes and recording are unchanged.'
        : 'Could not reconnect. Check the server connection and try again. Your saved class is unchanged.');
    } finally { guard.current = false; setBusy(false); }
  }
  return <View style={ui.notice}>
    <Text style={ui.label}>Server address changed</Text>
    <Text style={ui.muted}>This class still connects to {item.apiUrl}. The current address is {API_URL}.</Text>
    <ErrorMessage message={error} />
    <Button label="Reconnect to current server" busy={busy} secondary onPress={() => {
      Alert.alert('Reconnect this class?', `Only continue if ${API_URL} is your same trusted backend. We will send this class’s access token to check its existing job. No recording will be uploaded.`, [
        { text: 'Cancel', style: 'cancel' }, { text: 'Reconnect', onPress: () => { void connect(); } },
      ]);
    }} />
  </View>;
}
