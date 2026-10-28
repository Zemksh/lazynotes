import { useEffect, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { ActivityIndicator, Alert, ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useClasses } from '../../src/classes';
import { Button, ErrorMessage, MockNotice } from '../../src/components';
import { removeLocalClass } from '../../src/storage';
import { deleteRemote, ApiError } from '../../src/api';
import { colors, stageLabel, ui } from '../../src/theme';
import { ReconnectServer } from '../../src/ReconnectServer';

const steps = ['uploading', 'queued', 'transcribing', 'preparing', 'ready'];

export default function Processing() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { items, loading, uploads, submit, sync, refresh } = useClasses();
  const item = items.find(value => value.id === id);
  const [error, setError] = useState<string>();
  const [removing, setRemoving] = useState(false);
  useEffect(() => { if (item?.result) router.replace({ pathname: '/class/[id]', params: { id } }); }, [item?.result, id]);
  if (!item) return <View style={[ui.screen, ui.content]}>{loading ? <ActivityIndicator /> : <Text style={ui.text}>This class is no longer on this phone.</Text>}</View>;
  const uploading = Object.prototype.hasOwnProperty.call(uploads, id);
  const progress = uploads[id];
  const failed = ['failed', 'expired'].includes(item.stage);
  const current = steps.indexOf(item.stage);

  function remove() {
    Alert.alert('Remove this class?', 'This removes the saved recording from this phone and requests removal of any known server copy.', [
      { text: 'Keep class', style: 'cancel' }, { text: 'Remove', style: 'destructive', onPress: () => {
        void (async () => {
          setRemoving(true);
          try {
            if (item!.remoteId) {
              try { await deleteRemote(item!); }
              catch (e) { if (!(e instanceof ApiError && [404, 410].includes(e.status))) throw e; }
            }
            await removeLocalClass(id); await refresh(); router.replace('/');
          } catch { setError('Could not remove the server copy. Check your connection and try again.'); }
          finally { setRemoving(false); }
        })();
      } },
    ]);
  }

  return <SafeAreaView style={ui.screen} edges={['bottom', 'left', 'right']}><ScrollView contentContainerStyle={ui.content}>
    <ReconnectServer item={item} onConnected={async () => { await refresh(); await sync(); }} />
    <Text style={ui.eyebrow}>ONE CLASS, COMING TOGETHER</Text>
    <Text style={ui.title}>{item.title}</Text>
    {item.mock && <MockNotice />}
    <View style={ui.card}>
      <Text accessibilityLiveRegion="polite" style={ui.heading}>{stageLabel[item.stage]}</Text>
      {!failed && steps.map((stage, index) => <View key={stage} style={[ui.row, { paddingVertical: 8 }]}>
        <View style={{ width: 30, alignItems: 'center' }}>{index === current && (stage !== 'uploading' || uploading)
          ? <ActivityIndicator color={colors.accent} /> : <Text style={{ color: index < current ? colors.accent : colors.muted }}>{index < current ? '✓' : '○'}</Text>}</View>
        <Text style={[ui.text, { color: index === current ? colors.ink : colors.muted }]}>{stageLabel[stage]}</Text>
      </View>)}
      {uploading && <Text accessibilityLiveRegion="polite" style={ui.muted}>{progress != null ? `${Math.round(progress * 100)}% uploaded${progress >= 1 ? ' · Waiting for server confirmation' : ''}` : 'Sending your recording…'}</Text>}
      <ErrorMessage message={item.error ?? item.syncError} />
      {!failed && item.stage !== 'uploading' && <Text style={ui.muted}>You can leave this screen. We’ll check for your notes when you return. Longer recordings take more time.</Text>}
      {item.stage === 'uploading' && !uploading && <>
        <Text style={ui.muted}>The upload has not been confirmed. Retry safely using the saved recording; an accepted upload won’t create a second class.</Text>
        <Button label="Retry upload" onPress={() => { void submit(id); }} />
      </>}
      {uploading && <Text style={ui.muted}>Keep the app open until the upload is confirmed. Background uploads are not supported.</Text>}
      {failed && <Button label="Try again with this recording" onPress={() => router.push({ pathname: '/new', params: { retry: id } })} />}
      {!!item.syncError && !failed && <Button label="Check again" secondary onPress={() => { void sync(); }} />}
    </View>
    <ErrorMessage message={error} />
    <Button label="Back to your classes" secondary onPress={() => router.replace('/')} />
    {!uploading && <Button label="Remove class" secondary busy={removing} onPress={remove} />}
  </ScrollView></SafeAreaView>;
}
