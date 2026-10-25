import { useEffect, useRef, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import { ActivityIndicator, Alert, FlatList, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useClasses } from '../../src/classes';
import { ApiError, deleteRemote } from '../../src/api';
import { Button, ErrorMessage, MockNotice } from '../../src/components';
import { hasFile, patchClass, removeLocalClass } from '../../src/storage';
import { colors, timestamp, ui } from '../../src/theme';
import { SavedClass, Segment } from '../../src/types';

export default function ClassDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { items, loading } = useClasses();
  const item = items.find(value => value.id === id);
  if (!item?.result) return <View style={[ui.screen, ui.content]}>{loading ? <ActivityIndicator /> : <Text style={ui.text}>These notes are not available on this phone yet.</Text>}</View>;
  return <Detail item={item} />;
}

function Detail({ item }: { item: SavedClass }) {
  const { refresh } = useClasses();
  const output = item.result!;
  const available = hasFile(item.draft.audioUri);
  const player = useAudioPlayer(available ? { uri: item.draft.audioUri } : null);
  const playback = useAudioPlayerStatus(player);
  const [tab, setTab] = useState<'notes' | 'transcript'>('notes');
  const [highlight, setHighlight] = useState<string>();
  const [editor, setEditor] = useState<{ index: number; heading: string; body: string }>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [pendingSeek, setPendingSeek] = useState<number>();
  const [serverBusy, setServerBusy] = useState(false);
  const list = useRef<FlatList<Segment>>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const scrollRetries = useRef(0);
  const positions = new Map(output.transcript.map((segment, index) => [segment.id, index]));

  useEffect(() => () => { if (retryTimer.current) clearTimeout(retryTimer.current); }, []);
  useEffect(() => {
    if (tab !== 'transcript' || !highlight) return;
    scrollRetries.current = 0;
    const timer = setTimeout(() => {
      const index = output.transcript.findIndex(segment => segment.id === highlight);
      if (index >= 0) list.current?.scrollToIndex({ index, animated: true, viewPosition: 0.2 });
    }, 100);
    return () => clearTimeout(timer);
  }, [tab, highlight, output.transcript]);

  useEffect(() => {
    if (pendingSeek == null || !playback.isLoaded) return;
    void player.seekTo(pendingSeek).then(() => player.play()).catch(() => setError('The recording could not be played.')).finally(() => setPendingSeek(undefined));
  }, [pendingSeek, playback.isLoaded, player]);

  function sourceLinks(ids: string[]) {
    return <View style={ui.row}>{[...ids].sort((a, b) => (positions.get(a) ?? 0) - (positions.get(b) ?? 0)).map(sid => {
      const segment = output.transcript[positions.get(sid) ?? -1];
      if (!segment) return null;
      return <Pressable key={sid} accessibilityRole="link" accessibilityLabel={`Read source at ${timestamp(segment.start)}`}
        style={{ minHeight: 44, paddingHorizontal: 12, justifyContent: 'center', backgroundColor: colors.soft, borderRadius: 10 }}
        onPress={() => { setTab('transcript'); setHighlight(sid); }}>
        <Text style={{ color: colors.accent, fontWeight: '600', fontSize: 14 }}>{timestamp(segment.start)} ↗</Text>
      </Pressable>;
    })}</View>;
  }

  async function saveEdit() {
    if (!editor || saving) return;
    if (!editor.heading.trim() || !editor.body.trim()) { setError('Keep a heading and some note text before saving.'); return; }
    setSaving(true);
    try {
      await patchClass(item.id, { edits: { ...item.edits, [String(editor.index)]: { heading: editor.heading.trim(), body: editor.body.trim() } } });
      await refresh(); setEditor(undefined); setError(undefined);
    } catch { setError('Your edit could not be saved. Check device storage and try again.'); }
    finally { setSaving(false); }
  }

  async function removeServer() {
    setServerBusy(true);
    try {
      try { await deleteRemote(item); }
      catch (e) { if (!(e instanceof ApiError && [404, 410].includes(e.status))) throw e; }
      await patchClass(item.id, { serverDeleted: true }); await refresh();
    } catch { setError('Could not remove the server copy. Check your connection and try again.'); }
    finally { setServerBusy(false); }
  }

  function removeLocal() {
    Alert.alert('Remove from this phone?', 'The recording, notes, and your edits will be deleted from this phone. Any server copy still expires automatically.', [
      { text: 'Keep class', style: 'cancel' }, { text: 'Remove', style: 'destructive', onPress: () => {
        player.pause();
        void removeLocalClass(item.id).then(refresh).then(() => router.replace('/')).catch(() => setError('Could not remove this class. Try again.'));
      } },
    ]);
  }

  const header = <View style={{ gap: 16 }}>
    <Text style={ui.eyebrow}>SAVED FOR YOU</Text><Text style={ui.title}>{item.title}</Text>
    <Text style={ui.muted}>{new Date(item.createdAt).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })} · {timestamp(output.duration_seconds)} recording</Text>
    {output.mock && <MockNotice />}
    <View accessibilityRole="tablist" style={[ui.row, { padding: 5, borderRadius: 16, backgroundColor: colors.soft }]}>
      {(['notes', 'transcript'] as const).map(value => <Pressable key={value} accessibilityRole="tab" accessibilityState={{ selected: value === tab }}
        onPress={() => setTab(value)} style={{ flex: 1, padding: 14, minHeight: 48, alignItems: 'center', borderRadius: 12, backgroundColor: value === tab ? colors.card : 'transparent' }}>
        <Text style={ui.label}>{value === 'notes' ? 'Notes' : 'Transcript'}</Text></Pressable>)}
    </View>
    <ErrorMessage message={error} />
    {tab === 'transcript' && <>
      <Text style={ui.muted}>The original transcript, kept separately from your edits. Transcription can miss or mishear words.</Text>
      {!available ? <Text style={ui.muted}>The recording is unavailable on this phone. You can still read the transcript and follow source links.</Text> :
        <View style={ui.card}><Text style={ui.muted}>Recording · {timestamp(playback.currentTime)} / {timestamp(output.duration_seconds)}</Text>
          <Button label={playback.playing ? 'Pause recording' : 'Play recording'} secondary onPress={() => {
            if (!playback.isLoaded) { setError('The recording is still loading or cannot be played on this phone.'); return; }
            if (playback.playing) player.pause(); else { if (playback.didJustFinish) void player.seekTo(0); player.play(); }
          }} />
        </View>}
    </>}
  </View>;

  return <SafeAreaView style={ui.screen} edges={['bottom', 'left', 'right']}>
    {tab === 'notes' ? <ScrollView contentContainerStyle={ui.content}>
      {header}
      <Text style={ui.muted}>Tap a time to see the words behind a note. Edit any section to make it yours.</Text>
      {output.notes.sections.map((section, index) => {
        const edit = item.edits[String(index)];
        return <View key={index} style={ui.card}><Text style={ui.heading}>{edit?.heading ?? section.heading}</Text>
          <Text selectable style={ui.text}>{edit?.body ?? section.body}</Text>
          {sourceLinks(section.source_segment_ids)}
          {edit && <Text style={ui.muted}>Edited by you · Original source links retained.</Text>}
          <Button label="Edit section" accessibilityLabel={`Edit ${edit?.heading ?? section.heading}`} secondary onPress={() => { setError(undefined); setEditor({ index, heading: edit?.heading ?? section.heading, body: edit?.body ?? section.body }); }} />
        </View>;
      })}
      {output.notes.review_items.length > 0 && <View style={ui.notice}><Text style={ui.heading}>Check this part</Text>
        <Text style={ui.muted}>These passages may need another listen. Other mistakes may remain unflagged.</Text>
        {output.notes.review_items.map((review, index) => <View key={index} style={{ gap: 10, paddingVertical: 10 }}>
          <Text style={ui.text}>{review.reason}</Text>{sourceLinks(review.source_segment_ids)}</View>)}
      </View>}
      {output.warnings.filter(warning => !output.mock).map((warning, index) => <Text style={ui.muted} key={index}>{warning}</Text>)}
      <View style={ui.divider} /><Text style={ui.muted}>Notes and edits are saved on this phone.</Text>
      {item.serverDeleted ? <Text style={ui.muted}>Server copy removed.</Text> : <>
        <Text style={ui.muted}>Server copy expires {item.expiresAt ? new Date(item.expiresAt * 1000).toLocaleString() : 'automatically'}.</Text>
        <Button label="Remove server copy" secondary busy={serverBusy} onPress={() => { void removeServer(); }} />
      </>}
      <Button label="Remove from this phone" secondary onPress={removeLocal} />
    </ScrollView> : <FlatList ref={list} data={output.transcript} keyExtractor={segment => segment.id}
      contentContainerStyle={ui.content} ListHeaderComponent={header} extraData={highlight}
      onScrollToIndexFailed={info => {
        if (scrollRetries.current++ >= 6) return;
        list.current?.scrollToOffset({ offset: info.averageItemLength * info.index, animated: false });
        if (retryTimer.current) clearTimeout(retryTimer.current);
        retryTimer.current = setTimeout(() => list.current?.scrollToIndex({ index: info.index, animated: true, viewPosition: 0.2 }), 300);
      }}
      renderItem={({ item: segment }) => <View style={[ui.card, { marginTop: 14 }, highlight === segment.id && { borderColor: colors.accent, borderWidth: 2 }]}>
        <Text style={ui.eyebrow}>{timestamp(segment.start)} – {timestamp(segment.end)}</Text>
        <Text selectable style={ui.text}>{segment.text}</Text>
        {available && <Button label={`Play from ${timestamp(segment.start)}`} secondary onPress={() => { setPendingSeek(segment.start); setHighlight(segment.id); }} />}
      </View>} />}
    <Modal visible={!!editor} animationType="slide" onRequestClose={() => { if (!saving) setEditor(undefined); }}>
      <SafeAreaView style={ui.screen}><KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={ui.content}>
          <Text style={ui.heading}>Make it yours</Text>
          <Text style={ui.muted}>Your edits are saved locally. The original transcript and source links stay available.</Text>
          <TextInput accessibilityLabel="Section heading" style={ui.input} value={editor?.heading} maxLength={200}
            onChangeText={heading => setEditor(current => current ? { ...current, heading } : undefined)} editable={!saving} />
          <TextInput accessibilityLabel="Section notes" style={[ui.input, { minHeight: 260 }]} multiline value={editor?.body} maxLength={16000}
            onChangeText={body => setEditor(current => current ? { ...current, body } : undefined)} editable={!saving} />
          <ErrorMessage message={error} /><Button label="Save changes" busy={saving} onPress={() => { void saveEdit(); }} />
          <Button label="Discard changes" secondary disabled={saving} onPress={() => setEditor(undefined)} />
        </ScrollView>
      </KeyboardAvoidingView></SafeAreaView>
    </Modal>
  </SafeAreaView>;
}
